# GridWise — LLM-Assisted Campus Energy Optimization

## Architecture

1. **LLM Interpreter:** Operator notes are passed to Groq/Llama to extract structured JSON directives.
2. **Deterministic Guardrails:** The system enforces time window bounds (0-23) and valid numeric types to prevent LLM hallucinations.
3. **PuLP Optimizer:** The mathematical solver minimizes `total_cost_bdt` while strictly enforcing energy balance, battery rate limits, and end-of-day battery neutrality ($E_{23} = E_{initial}$).

## Local Quickstart

```bash
pip install -r requirements.txt
export GROQ_API_KEY="your-groq-key"
uvicorn main:app --host 0.0.0.0 --port 8000
```
