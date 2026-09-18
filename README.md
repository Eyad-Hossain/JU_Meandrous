# GridWise Energy Scheduler

24-hour energy scheduling optimizer for the **BUP CSE Fest 2026 GridWise Hackathon**. Minimizes grid electricity cost using linear programming while respecting battery dynamics, solar generation, time-of-use tariffs, and operator directives.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Operator Notes │────▶│  LLM Interpreter │────▶│  PuLP Optimizer │
│  (natural lang) │     │  (Groq/GPT-OSS)  │     │  (CBC Solver)   │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                                       │
                                                       ▼
                                              ┌─────────────────┐
                                              │  Hourly Schedule │
                                              │  (JSON output)   │
                                              └─────────────────┘
```

- **LLM Provider**: Groq (model: `openai/gpt-oss-20b`)
- **Solver**: PuLP with bundled CBC (Coin-or Branch and Cut)
- **API Framework**: FastAPI + Uvicorn

---

## Quick Start

### Prerequisites
- Python 3.10+
- Docker (optional, for containerized deployment)
- Groq API key ([get one free](https://console.groq.com))

### Local Development

```bash
# 1. Clone and enter directory
cd gridwise

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set environment variable
export GROQ_API_KEY="your_groq_api_key_here"

# 5. Run the API server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Server starts at `http://localhost:8000`

### Docker Deployment

```bash
# Build image
docker build -t gridwise:latest .

# Run container (requires GROQ_API_KEY)
docker run -d \
  -p 8000:8000 \
  -e GROQ_API_KEY="your_groq_api_key_here" \
  --name gridwise \
  gridwise:latest

# Verify health
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | **Yes** | Groq API key for LLM directive interpretation. Get from [console.groq.com](https://console.groq.com) |

---

## API Reference

### `GET /health`
Health check endpoint. Returns `{"status": "ok"}` if server is running.

### `POST /optimize-energy`
Main optimization endpoint.

#### Request Body
```json
{
  "scenario_id": "string",
  "operator_notes": ["string", "..."],
  "hours": [
    {
      "hour": 0,
      "demand_kwh": 90.0,
      "solar_kwh": 0.0,
      "tariff_bdt_per_kwh": 6.0
    }
    // ... 24 entries (hours 0-23)
  ],
  "battery": {
    "capacity_kwh": 220.0,
    "initial_energy_kwh": 110.0,
    "minimum_energy_kwh": 40.0,
    "max_charge_kwh_per_hour": 50.0,
    "max_discharge_kwh_per_hour": 50.0
  }
}
```

#### Response
```json
{
  "scenario_id": "string",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {
        "hours": [12, 13],
        "factor": 0.25
      },
      "explanation": "Solar availability reduced to 25% during cleaning window"
    }
  ],
  "hourly_plan": [
    {
      "hour": 0,
      "grid_kwh": 90.0,
      "solar_used_kwh": 0.0,
      "battery_action": "idle",
      "battery_kwh": 0.0,
      "battery_energy_after_kwh": 110.0
    }
    // ... 24 entries
  ],
  "total_grid_kwh": 2692.5,
  "total_cost_bdt": 38365.0,
  "peak_grid_kwh": 175.0,
  "plan_summary": "Human-readable summary"
}
```

---

## Supported Directives

| Type | Parameters | Description |
|------|------------|-------------|
| `solar_reduction` | `factor` (0-1), `hours` | Reduces usable solar to `factor × forecast` |
| `minimum_battery_reserve` | `minimum_energy_kwh`, `hours` | Raises SOC floor during specified hours |
| `no_charge_window` | `hours` | Forces battery charge = 0 |
| `no_discharge_window` | `hours` | Forces battery discharge = 0 |
| `max_grid_window` | `max_grid_kwh`, `hours` | Caps grid import per hour |
| `no_op` | — | Distractor note (no effect) |

**Time windows**: Start-inclusive, end-exclusive. "6 PM to 9 PM" → `hours: [18, 19, 20]`

---

## Sample cURL Test

```bash
# Using the provided public sample case (SAMPLE-01)
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "SAMPLE-01",
    "operator_notes": [
      "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
      "The sports office moved next month registration deadline."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 1, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 2, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 3, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 4, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 5, "demand_kwh": 95, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 6, "demand_kwh": 110, "solar_kwh": 5, "tariff_bdt_per_kwh": 8},
      {"hour": 7, "demand_kwh": 130, "solar_kwh": 20, "tariff_bdt_per_kwh": 10},
      {"hour": 8, "demand_kwh": 150, "solar_kwh": 50, "tariff_bdt_per_kwh": 12},
      {"hour": 9, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 10, "demand_kwh": 175, "solar_kwh": 130, "tariff_bdt_per_kwh": 16},
      {"hour": 11, "demand_kwh": 180, "solar_kwh": 160, "tariff_bdt_per_kwh": 16},
      {"hour": 12, "demand_kwh": 185, "solar_kwh": 180, "tariff_bdt_per_kwh": 15},
      {"hour": 13, "demand_kwh": 180, "solar_kwh": 170, "tariff_bdt_per_kwh": 14},
      {"hour": 14, "demand_kwh": 170, "solar_kwh": 140, "tariff_bdt_per_kwh": 13},
      {"hour": 15, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 16, "demand_kwh": 170, "solar_kwh": 45, "tariff_bdt_per_kwh": 18},
      {"hour": 17, "demand_kwh": 185, "solar_kwh": 10, "tariff_bdt_per_kwh": 22},
      {"hour": 18, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 28},
      {"hour": 19, "demand_kwh": 215, "solar_kwh": 0, "tariff_bdt_per_kwh": 30},
      {"hour": 20, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 26},
      {"hour": 21, "demand_kwh": 175, "solar_kwh": 0, "tariff_bdt_per_kwh": 18},
      {"hour": 22, "demand_kwh": 135, "solar_kwh": 0, "tariff_bdt_per_kwh": 10},
      {"hour": 23, "demand_kwh": 105, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}
    ],
    "battery": {
      "capacity_kwh": 220,
      "initial_energy_kwh": 110,
      "minimum_energy_kwh": 40,
      "max_charge_kwh_per_hour": 50,
      "max_discharge_kwh_per_hour": 50
    }
  }' | jq .
```

---

## Project Structure

```
.
├── main.py                                    # FastAPI application + LLM interpreter
├── scheduler.py                               # Standalone PuLP optimizer (bug-fixed)
├── requirements.txt                           # Python dependencies
├── Dockerfile                                 # Container definition
├── BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json  # 10 test cases
├── test_api.py                                # Test runner for sample cases
├── PROJECT_STATUS.md                          # Development status & bug fixes
└── README.md                                  # This file
```

---

## Running Tests

```bash
# With API server running locally
python test_api.py

# Or test the standalone scheduler (no LLM/API key needed)
python scheduler.py
```

The `scheduler.py` self-test demonstrates all directive types with mock data and prints a formatted schedule.

---

## Key Implementation Details

### Bug Fixes in `scheduler.py`
Three critical fixes applied to `_apply_directives()`:

1. **Correct JSON key**: Uses `minimum_energy_kwh` (not `min_energy_kwh`)
2. **Hour-specific solar reduction**: Only applies to directive's `hours` array (not all 24 hours)
3. **Dynamic PuLP bounds**: Adds explicit constraints instead of modifying variable bounds after creation (which PuLP ignores)

### Constraints Enforced
- Energy balance: `grid + solar + discharge = demand + charge` (every hour)
- Battery SOC limits: `minimum_reserve ≤ energy_after ≤ capacity`
- Charge/discharge rate limits
- End-of-day neutrality: `energy_after[23] == initial_energy`
- All directive constraints (hard constraints via PuLP)

---

## License

MIT License - Built for BUP CSE Fest 2026 GridWise Hackathon