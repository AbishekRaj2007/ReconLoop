# CLAUDE.md

Guidance for Claude Code (or any Claude instance) working in this repository.

## Project

**ReconLoop** — a multi-source reconciliation agent built for the Razorpay AI Buildathon 2026, AI Finance Controller track. It ingests a ledger, a bank statement, and a payment-gateway settlement report (synthetic, 200+ records total), matches records across sources through a tiered deterministic → LLM escalation pipeline, and produces an honest, scored report: match rate, throughput, and a fully-classified exception list for everything it couldn't resolve.

**`plan.md` in the repo root is the source of truth for phase status and scope.** Before starting any work, check which phase is in progress and read that phase's task list. Update checkboxes in `plan.md` as tasks are completed — don't let it go stale.

---

## Golden Rules

1. **Deterministic before LLM, always.** Never call an LLM for something a rule can decide. The whole point of this project is that AI judgment is rationed to genuinely ambiguous cases — this is the core evaluation differentiator (see plan.md §4, §11). If you're tempted to "just ask the model," check whether a rule in `matcher/exact_match.py` or `fuzzy_match.py` should handle it instead.
2. **Never force a match.** If confidence is below threshold — deterministic or LLM — the record goes to the exception classifier. It does not get auto-matched "to be helpful." A false match is worse than a missed one in this domain; don't optimize match rate at the expense of correctness.
3. **Nothing disappears.** Every input record must end up in exactly one of: matched, or exceptioned with a named type and rationale. The invariant `len(matches) + len(exceptions) == total_records` must hold after every pipeline run — this is enforced by a test in `backend/tests/test_pipeline_golden.py` and should never be weakened to make a demo look cleaner.
4. **Every LLM call is audited.** Prompt, response, model name, latency, and estimated cost get logged to the `LLMAuditLog` table for every single call, no exceptions (pun intended). This is what makes the "AI Judgment" story defensible to a judging panel — don't skip it for convenience.
5. **Ground truth is generated, never fabricated.** The synthetic data generator (`data/generator/generate_synthetic_data.py`) is seeded and deterministic. Never hand-edit `ground_truth_matches.json` to make scoring look better — if the matcher is wrong, fix the matcher, not the answer key.

---

## Tech Stack

| Layer | Tech |
|---|---|
| Matching engine | Python 3.11+, pandas, rapidfuzz |
| API | FastAPI + Pydantic v2 |
| Database | PostgreSQL + SQLAlchemy |
| Fast LLM tier | Groq API (Llama 3.3 70B) |
| Strong LLM tier | Claude (Sonnet) or GPT-4o — escalation only |
| Frontend | Next.js + TypeScript + Recharts |
| Testing | pytest + pytest-cov (backend), no frontend test suite required for this timeline |

---

## Repository Structure

```
reconloop/
├── plan.md                # phase-by-phase build plan — check this first
├── config/matching_config.yaml   # all tolerances/thresholds — never hardcode these in code
├── data/
│   ├── generator/          # synthetic data generation
│   ├── raw/                 # generated CSVs (gitignored except a committed sample)
│   └── ground_truth/        # answer key for scoring
├── backend/
│   └── app/
│       ├── core/matcher/    # Tier 1 + 2 (deterministic) + Tier 3/4 (LLM)
│       ├── core/exceptions/ # exception classification
│       └── core/scoring/    # evaluator vs ground truth
├── frontend/                # Next.js dashboard
└── scripts/                 # run_pipeline.py, run_scoring.py CLIs
```

---

## Setup & Common Commands

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # fill in GROQ_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL
uvicorn app.main:app --reload

# Generate synthetic data (deterministic, seeded)
python data/generator/generate_synthetic_data.py --seed 42 --count 200

# Run the full pipeline end-to-end
python scripts/run_pipeline.py

# Score the last pipeline run against ground truth
python scripts/run_scoring.py

# Run tests
cd backend && pytest --cov=app tests/

# Frontend
cd frontend
npm install
npm run dev
```

---

## Coding Conventions

**Python**
- Type hints everywhere — this is a data pipeline, untyped dicts are how silent record loss happens
- Pydantic v2 models for all cross-boundary data (API in/out, LLM structured output)
- `black` + `ruff` for formatting/linting, run before every commit
- Amounts are always `Decimal`, never `float` — this is financial data
- Dates are always UTC, normalized at ingestion, never compared in mixed timezones

**TypeScript / Next.js**
- Strict mode on
- ESLint + Prettier defaults
- Keep the dashboard to a single page unless there's a clear reason to split — judges should understand it in 15 seconds, not navigate a nav bar

**Testing**
- Coverage target ≥80% on `core/matcher/` and `core/exceptions/` — these are the actual intellectual property of the project, everything else is scaffolding
- `test_pipeline_golden.py` runs the full pipeline against the committed sample synthetic data and asserts the invariant in Golden Rule #3, plus a minimum expected match rate (regression guard — if a change drops match rate unexpectedly, this test should fail loudly)
- Mock LLM clients in unit tests; don't burn API calls in CI. A small number of real-call integration tests are fine to run manually before a demo, not on every save

---

## LLM Usage Rules (see also plan.md §11)

- Structured output only — the model returns `{decision, confidence, rationale}` as JSON, never free text parsed with regex
- If output is malformed twice in a row, route to exception type `LLM_OUTPUT_UNPARSEABLE` — don't retry indefinitely or silently drop the record
- Tier 3 (Groq/fast) is tried first for every ambiguous record; Tier 4 (strong model) is only invoked if Tier 3's confidence is below `TIER3_CONFIDENCE_ESCALATION_THRESHOLD` (see `matching_config.yaml`)
- Never let a low-confidence LLM decision auto-match — confidence below threshold always routes to exception, regardless of tier

---

## Definition of Done (for any change to the matcher or scoring logic)

Before considering a change complete:
1. `pytest --cov=app tests/` passes, coverage targets still met
2. `python scripts/run_pipeline.py && python scripts/run_scoring.py` runs clean on the committed sample data
3. The invariant test (Golden Rule #3) passes
4. Match rate hasn't regressed against the last recorded number in `docs/metrics_log.md` — if it has, that's either an intentional documented tradeoff or a bug, not something to leave unexplained
5. `plan.md` checkboxes updated for whatever task this change corresponds to

---

## Explicitly Out of Scope (don't add these, even if it seems easy)

- Authentication / multi-tenant support — not needed for a single-batch buildathon submission
- A generalized "any number of sources" abstraction — 3 sources, hardcoded conceptually, is fine and more legible than premature generalization
- LangGraph or other agent-graph frameworks — the pipeline is a linear tiered dispatcher; a graph abstraction adds indirection without adding capability here
- Real bank/gateway API integrations — synthetic data only, this is a buildathon submission, not a production system

---

## When Picking Up Work

1. Read `plan.md`, find the current phase, read its task list and Definition of Done
2. Read this file for conventions
3. Do the work
4. Update `plan.md` checkboxes
5. Run the Definition of Done checklist above before considering it complete
