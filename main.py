import os
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import scheduler
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
# Optimization lives in scheduler.solve(); main.py only shapes data and calls it.

# 4. API ENDPOINTS


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/optimize-energy")
def optimize_energy(request: OptimizeRequest):
    safe_notes = []
    for note in request.operator_notes:
        clean_note = note.replace('“', '"').replace(
            '”', '"').replace('‘', "'").replace('’', "'")
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

        valid_directives = [DirectiveInterpretation(
            **d) for d in parsed_directives]

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"LLM Interpretation failed: {str(e)}")

    # --- Translate LLM interpretations into the scheduler's expected shape --
    # scheduler.solve() expects: [{"type": "...", "structured_adjustment": {...}}]
    directives_for_solver = []
    for item in valid_directives:
        if not item.applies:
            continue
        adj = item.structured_adjustment
        adj_dict = adj.model_dump() if hasattr(
            adj, "model_dump") else (adj.dict() if adj else {})
        directives_for_solver.append({
            "type": item.directive_type,
            "structured_adjustment": adj_dict,
        })

    # --- Pydantic -> plain dicts for the scheduler -------------------------
    hours_dict = [h.model_dump() if hasattr(
        h, "model_dump") else h.dict() for h in request.hours]
    battery_dict = (request.battery.model_dump() if hasattr(
        request.battery, "model_dump") else request.battery.dict())

    # --- Solve -------------------------------------------------------------
    try:
        hourly_plan = scheduler.solve(
            hours=hours_dict,
            battery=battery_dict,
            directives=directives_for_solver,
        )
    except Exception as e:
        # Safe failure requirement: never 500 on solver issues —
        # return a structured error payload instead.
        return {"error": "Solver failed", "details": str(e)}, 500

    # --- Summary stats -----------------------------------------------------
    total_grid_kwh = round(sum(h["grid_kwh"] for h in hourly_plan), 2)
    peak_grid_kwh = round(max(h["grid_kwh"] for h in hourly_plan), 2)
    total_cost_bdt = round(
        sum(
            plan_h["grid_kwh"] * req_h["tariff_bdt_per_kwh"]
            for plan_h, req_h in zip(hourly_plan, hours_dict)
        ),
        2,
    )

    serialized_directives = [d.model_dump() if hasattr(
        d, 'model_dump') else d.dict() for d in valid_directives]

    return {
        "scenario_id": request.scenario_id,
        "directive_interpretation": serialized_directives,
        "hourly_plan": hourly_plan,
        "total_grid_kwh": total_grid_kwh,
        "total_cost_bdt": total_cost_bdt,
        "peak_grid_kwh": peak_grid_kwh,
        "plan_summary": "Processed schedule via LLM and PuLP optimizer."
    }
