# GridWise Energy Scheduler - Project Status

## Overview
This repository implements a 24-hour energy scheduling optimizer for the BUP CSE Fest 2026 GridWise hackathon. It solves a linear programming problem to minimize grid electricity cost while respecting battery dynamics, solar generation, time-of-use tariffs, and operator directives.

## Files in Repository

| File | Description | Status |
|------|-------------|--------|
| `main.py` | FastAPI application with LLM-based directive interpretation + PuLP optimizer | Original (committed) |
| `test_api.py` | Test script for API endpoint | Original (committed) |
| `BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json` | 10 public sample cases with expected outputs | Original (committed) |
| `scheduler.py` | Standalone PuLP scheduler module with **3 critical bug fixes** | **New (uncommitted)** |

---

## Critical Bug Fixes in `scheduler.py`

The new `scheduler.py` module fixes three critical bugs in the `_apply_directives` function that would cause failures in hidden judge tests:

### Bug 1: Incorrect JSON Key for Battery Reserve
**Problem**: Code looked for `min_energy_kwh` but schema mandates `minimum_energy_kwh`
**Fix**: Changed key lookup to match official schema
```python
# Before (broken)
min_energy = float(adj.get("min_energy_kwh"))

# After (fixed)
min_energy = float(adj.get("minimum_energy_kwh"))
```

### Bug 2: Solar Reduction Applied to Wrong Hours
**Problem**: Loop iterated over `HOURS` (all 24 hours) instead of directive's `hours` array
**Fix**: Only apply to specified hours
```python
# Before (broken)
for h in HOURS:
    effective_solar[h] *= factor

# After (fixed)
for h in adj.get("hours", []):
    reduced_limit = effective_solar[h] * factor
    prob += (battery_vars["solar"][h] <= reduced_limit, f"solar_reduction_{h}")
```

### Bug 3: PuLP Bounds Are Static (Critical)
**Problem**: Tried to modify `effective_solar` list after `solar_used` LpVariables were created. In PuLP, a variable's `upBound` is fixed at creation - changing the list later does nothing.
**Fix**: Add explicit constraints to enforce bounds dynamically
```python
# Before (broken - bounds ignored by solver)
solar_used = [pulp.LpVariable(f"solar_{h}", lowBound=0, upBound=effective_solar[h]) for h in HOURS]
# ... later modifying effective_solar has no effect ...

# After (fixed - explicit constraints)
for h in adj.get("hours", []):
    reduced_limit = effective_solar[h] * factor
    prob += (battery_vars["solar"][h] <= reduced_limit, f"solar_reduction_{h}")
```

---

## Architecture Comparison

### `main.py` (Original)
- Single-file FastAPI app
- LLM (Groq/GPT-OSS) interprets operator notes → structured directives
- Optimizer embedded in `solve_optimization()` function
- Directive application happens inline with variable bounds modification

### `scheduler.py` (New - Standalone Module)
- Clean separation: pure PuLP solver, no LLM dependency
- `_apply_directives()` function with **all 3 bugs fixed**
- Proper constraint-based approach (not bound modification)
- Comprehensive self-test block with realistic mock data
- Matches official schema exactly (`minimum_energy_kwh` key)

---

## Directive Types Supported

| Directive | Key Parameters | Effect |
|-----------|---------------|--------|
| `solar_reduction` | `factor` (0-1), `hours` | Reduces usable solar in specified hours |
| `minimum_battery_reserve` | `minimum_energy_kwh`, `hours` | Raises SOC floor in specified hours |
| `no_charge_window` | `hours` | Forces charge = 0 in specified hours |
| `no_discharge_window` | `hours` | Forces discharge = 0 in specified hours |
| `max_grid_window` | `max_grid_kwh`, `hours` | Caps grid import in specified hours |
| `no_op` | - | No effect (distractor) |

---

## Verification Results

Running the self-test in `scheduler.py` produces valid output:

```
 h    grid   solar     action     kwh     soc
------------------------------------------------
 0   0.000   0.000  discharge   0.800   4.200
 1   0.000   0.000  discharge   0.700   3.500
 ...
 8   3.700   1.500     charge   3.000   4.000  ← Reserve ≥ 4.0 enforced
 9   0.000   2.100     charge   0.100   4.100
10   0.000   2.600     charge   0.800   4.900
11   0.000   2.900     charge   1.000   5.900
12   0.000   1.800  discharge   0.300   5.600  ← Solar reduced to 60% (3.0→1.8)
13   0.000   1.680  discharge   0.320   5.280  ← Solar reduced to 60% (2.8→1.68)
...
18   1.500   0.000  discharge   1.900   4.300  ← No charge, grid ≤ 1.5
19   1.500   0.000  discharge   1.700   2.600
20   1.500   0.000  discharge   1.100   1.500
21   1.500   0.000  discharge   0.500   1.000
...
Total grid cost: 168.44 BDT
Final SOC: 5.000 kWh (started at 5.0)  ← End-of-day neutrality satisfied
```

**All constraints verified:**
- ✅ Solar reduction only hours 12-16 (60% factor)
- ✅ Minimum reserve ≥ 4.0 kWh hours 8-11
- ✅ No charging hours 18-21
- ✅ Grid import ≤ 1.5 kWh hours 17-21
- ✅ Energy balance every hour
- ✅ End-of-day SOC = initial SOC

---

## Next Steps

1. **Integrate `scheduler.py` into `main.py`**: Replace the embedded `solve_optimization()` with import from scheduler module
2. **Run full test suite**: Execute `test_api.py` against all 10 sample cases
3. **Add validation layer**: Deterministic validation of LLM output before passing to optimizer
4. **Commit changes**: Stage and commit `scheduler.py` once integration verified

---

## Dependencies

```txt
pulp          # Linear programming (bundled CBC)
fastapi       # Web framework (main.py)
pydantic      # Data validation (main.py)
openai        # LLM client (main.py, uses Groq endpoint)
requests      # HTTP client (test_api.py)
```