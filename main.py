import os
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import pulp
from openai import OpenAI

# 1. INITIALIZATION
app = FastAPI()
client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

# 2. DATA MODELS
class HourData(BaseModel):
    hour: int
    demand_kwh: float
    solar_kwh: float
    tariff_bdt_per_kwh: float

class BatteryData(BaseModel):
    capacity_kwh: float
    initial_energy_kwh: float
    minimum_energy_kwh: float
    max_charge_kwh_per_hour: float
    max_discharge_kwh_per_hour: float

class OptimizeRequest(BaseModel):
    scenario_id: str
    operator_notes: List[str]
    hours: List[HourData]
    battery: BatteryData

class StructuredAdjustment(BaseModel):
    hours: Optional[List[int]] = None
    factor: Optional[float] = None
    minimum_energy_kwh: Optional[float] = None
    max_grid_kwh: Optional[float] = None

class DirectiveInterpretation(BaseModel):
    note_index: int
    applies: bool
    directive_type: str
    structured_adjustment: Optional[StructuredAdjustment]
    explanation: str

# 3. THE OPTIMIZER LOGIC
def solve_optimization(request: OptimizeRequest, directives: List[DirectiveInterpretation]):
    model = pulp.LpProblem("GridWise", pulp.LpMinimize)

    # Create variables for the 24 hours
    grid = [pulp.LpVariable(f"g_{i}", lowBound=0) for i in range(24)]
    charge = [pulp.LpVariable(f"c_{i}", lowBound=0) for i in range(24)]
    discharge = [pulp.LpVariable(f"d_{i}", lowBound=0) for i in range(24)]
    batt = [pulp.LpVariable(
        f"b_{i}", lowBound=0, upBound=request.battery.capacity_kwh) for i in range(24)]
    solar_used = [pulp.LpVariable(f"s_{i}", lowBound=0) for i in range(24)]

    # Objective: Minimize cost
    model += pulp.lpSum([grid[i] * request.hours[i].tariff_bdt_per_kwh for i in range(24)])

    # Apply constraints hour by hour
    for i in range(24):
        effective_solar = request.hours[i].solar_kwh
        min_reserve = request.battery.minimum_energy_kwh
        max_grid = None

        # Apply directives safely
        for d in directives:
            if d.applies and d.structured_adjustment and d.structured_adjustment.hours is not None:
                if i in d.structured_adjustment.hours:
                    if d.directive_type == "solar_reduction" and d.structured_adjustment.factor is not None:
                        effective_solar *= d.structured_adjustment.factor
                    elif d.directive_type == "minimum_battery_reserve" and d.structured_adjustment.minimum_energy_kwh is not None:
                        min_reserve = max(min_reserve, d.structured_adjustment.minimum_energy_kwh)
                    elif d.directive_type == "no_charge_window":
                        model += charge[i] == 0
                    elif d.directive_type == "no_discharge_window":
                        model += discharge[i] == 0
                    elif d.directive_type == "max_grid_window" and d.structured_adjustment.max_grid_kwh is not None:
                        if max_grid is None:
                            max_grid = d.structured_adjustment.max_grid_kwh
                        else:
                            max_grid = min(max_grid, d.structured_adjustment.max_grid_kwh)

        # Base physical constraints
        model += solar_used[i] <= effective_solar
        model += grid[i] + solar_used[i] + discharge[i] == request.hours[i].demand_kwh + charge[i]
        model += charge[i] <= request.battery.max_charge_kwh_per_hour
        model += discharge[i] <= request.battery.max_discharge_kwh_per_hour
        model += batt[i] >= min_reserve

        if max_grid is not None:
            model += grid[i] <= max_grid

        # Battery tracking
        if i == 0:
            model += batt[i] == request.battery.initial_energy_kwh + charge[i] - discharge[i]
        else:
            model += batt[i] == batt[i-1] + charge[i] - discharge[i]

    # End of day rule: Battery must end at the exact same level it started
    model += batt[23] == request.battery.initial_energy_kwh

    model.solve(pulp.PULP_CBC_CMD(msg=False))

    # Format the output plan
    plan = []
    for i in range(24):
        c_val = charge[i].varValue or 0.0
        d_val = discharge[i].varValue or 0.0
        action = "idle"
        kwh = 0.0
        if c_val > 0.001:
            action = "charge"
            kwh = c_val
        elif d_val > 0.001:
            action = "discharge"
            kwh = d_val

        plan.append({
            "hour": i,
            "grid_kwh": round(grid[i].varValue or 0.0, 4),
            "solar_used_kwh": round(solar_used[i].varValue or 0.0, 4),
            "battery_action": action,
            "battery_kwh": round(kwh, 4),
            "battery_energy_after_kwh": round(batt[i].varValue or 0.0, 4)
        })

    total_cost = round(pulp.value(model.objective), 2)
    peak_grid = round(max((g.varValue or 0.0) for g in grid), 2)
    return plan, total_cost, peak_grid

# 4. API ENDPOINTS
@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/optimize-energy")
def optimize_energy(request: OptimizeRequest):
    # Brute-force sanitize the input to strict ASCII
    safe_notes = []
    for note in request.operator_notes:
        clean_note = note.replace('“', '"').replace('”', '"').replace('‘', "'").replace('’', "'")
        clean_note = clean_note.encode('ascii', 'ignore').decode('ascii')
        safe_notes.append(clean_note)

    prompt = f"""
    Analyze these operator notes for an energy grid: {json.dumps(safe_notes)}.
    Return a JSON object with a single key "directives" containing an array of objects. 
    
    CRITICAL INSTRUCTIONS:
    - You MUST output the "hours" array inside "structured_adjustment" for ANY active directive.
    - Time windows are start-inclusive and end-exclusive (e.g., "noon until 2 PM" means hours [12, 13]).
    - For solar_reduction, factor is the fraction REMAINING (e.g., 25% means factor: 0.25).
    
    Keys for each object in the array: 
    - note_index (int): 0-based index.
    - applies (bool): true if the note affects the schedule, false if it is irrelevant.
    - directive_type (string): exactly one of: solar_reduction, minimum_battery_reserve, no_charge_window, no_discharge_window, max_grid_window, no_op.
    - structured_adjustment (object or null): If no_op, set to null. Otherwise, it MUST contain "hours": [array of ints]. For solar_reduction, also include "factor" (float).
    - explanation (string)
    """

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "system", "content": prompt}],
            response_format={"type": "json_object"}
        )

        raw_json = json.loads(response.choices[0].message.content)
        parsed_directives = raw_json.get("directives", [])

        valid_directives = [DirectiveInterpretation(**d) for d in parsed_directives]

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"LLM Interpretation failed: {str(e)}")

    # Run the math solver
    plan, total_cost, peak = solve_optimization(request, valid_directives)

    # Handle Pydantic v1 vs v2 dict export safely
    serialized_directives = [d.model_dump() if hasattr(d, 'model_dump') else d.dict() for d in valid_directives]

    return {
        "scenario_id": request.scenario_id,
        "directive_interpretation": serialized_directives,
        "hourly_plan": plan,
        "total_grid_kwh": round(sum(h['grid_kwh'] for h in plan), 2),
        "total_cost_bdt": total_cost,
        "peak_grid_kwh": peak,
        "plan_summary": "Processed schedule via LLM and PuLP optimizer."
    }