# ReconLoop

A multi-source reconciliation agent that ingests a ledger, a bank statement, and a
payment-gateway settlement report, matches records across sources through a tiered
deterministic → LLM escalation pipeline, and produces an honest, scored report: match
rate, throughput, and a fully-classified exception list for everything it couldn't
resolve.

Built for the Razorpay AI Buildathon 2026, AI Finance Controller track. See
[`plan.md`](./plan.md) for the full build plan and [`CLAUDE.md`](./CLAUDE.md) for
project conventions.

## Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL 16+ (via Docker, or a native install)

## Setup

### 1. Database

Using Docker (recommended):

```bash
docker compose up -d postgres
```

Or natively, initialize and start a local cluster, then create the `reconloop`
database with a `reconloop` role.

### 2. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # fill in GROQ_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL
uvicorn app.main:app --reload
```

Health check: `curl http://127.0.0.1:8000/health` → `{"status": "ok"}`

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Dashboard: http://localhost:3000

## Running the pipeline

```bash
# Generate synthetic data (deterministic, seeded)
python data/generator/generate_synthetic_data.py --seed 42 --count 200

# Run the full pipeline end-to-end
python scripts/run_pipeline.py

# Score the last pipeline run against ground truth
python scripts/run_scoring.py
```

## Tests

```bash
cd backend && pytest --cov=app tests/
```
