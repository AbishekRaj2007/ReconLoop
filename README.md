# ReconLoop

A multi-source reconciliation agent that ingests a ledger, a bank statement, and a
payment-gateway settlement report, matches records across sources through a tiered
deterministic → LLM escalation pipeline, and produces an honest, scored report: match
rate, throughput, and a fully-classified exception list for everything it couldn't
resolve.

Built for the Razorpay AI Buildathon 2026, AI Finance Controller track. See
[`plan.md`](./plan.md) for the full build plan, [`CLAUDE.md`](./CLAUDE.md) for
project conventions, [`docs/architecture.md`](./docs/architecture.md) for the pipeline
design and the reasoning behind each tier, and
[`docs/demo_script.md`](./docs/demo_script.md) for the 5-minute pitch walkthrough.

## Architecture at a glance

```
 Ledger + Bank + Gateway CSVs
            │
            ▼
     Normalization              ← IDs, dates, amounts to canonical form
            │
            ▼
 Tier 1: Exact ID match  ───────► resolved (free, instant)
            │ unresolved
            ▼
 Tier 2: Fuzzy match     ───────► resolved (score ≥ 90, free)
            │ score 60–89
            ▼
 Tier 3: Fast LLM (Groq) ───────► resolved (confidence ≥ threshold)
            │ low confidence / declined
            ▼
 Tier 4: Strong LLM      ───────► resolved (confidence ≥ threshold)
            │ still unresolved
            ▼
   Exception Classifier   ──────► typed exception + rationale
            │
            ▼
  Postgres (matches, exceptions, LLM audit log)
            │
            ▼
   FastAPI  ──────►  Next.js dashboard
```

Every record ends up in exactly one of **matched** or **exceptioned with a named
type and rationale** — nothing is silently dropped. See
[`docs/architecture.md`](./docs/architecture.md) for the full design rationale.

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
cp .env.example .env.local   # defaults to http://localhost:8000, override if needed
npm install
npm run dev
```

Dashboard: http://localhost:3000 — shows the latest run's match rate, throughput,
false-match rate, per-tier attribution chart, and a filterable exception table. Click
**Run reconciliation** to trigger a fresh `POST /reconcile` and watch it update live.

## Running the pipeline (CLI)

```bash
# Generate synthetic data (deterministic, seeded)
python data/generator/generate_synthetic_data.py --seed 42 --count 200

# Run the full pipeline end-to-end (add --use-llm to enable Tier 3/4)
python scripts/run_pipeline.py

# Score the last pipeline run against ground truth
python scripts/run_scoring.py
```

Every scoring run is appended to [`docs/metrics_log.md`](./docs/metrics_log.md) so
match-rate history is visible, not just the latest claim.

## Tests

```bash
cd backend && pytest --cov=app tests/
```

Coverage target is ≥80% on `core/matcher/` and `core/exceptions/` — these are the
project's actual intellectual property. `test_pipeline_golden.py` enforces the
`len(matches) + len(exceptions) == total_records` invariant on every run.
