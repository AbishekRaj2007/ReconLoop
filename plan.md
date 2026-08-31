# ReconLoop — Multi-Source Reconciliation Agent
**Razorpay AI Buildathon 2026 — AI Finance Controller Track**
**Submission deadline: September 5, 2026**

---

## 1. Brief Recap (what we're actually being judged on)

> Build an agent that closes one finance-ops loop across a 50+ record batch of synthetic data, reporting its match rate and the exceptions it could not resolve.
>
> **The bar:** Throughput plus measured accuracy plus an honest exception list. One cherry-picked match proves nothing.

Razorpay evaluates on four axes (confirmed from their program page): **Problem Taste, Build Quality, AI Judgment, Failure Recovery.** Every phase below is built to leave visible evidence against all four — not just working code, but a legible paper trail a panel can interrogate in a 5-minute pitch.

---

## 2. Problem Statement

Every real business has (at least) three records of the same transaction that never quite agree:

1. The **ledger** (internal system of record — what the ERP/accounting system says happened)
2. The **bank statement** (what actually hit the account, on the bank's clock, in the bank's format)
3. The **payment gateway settlement report** (what the payment processor says it paid out, net of fees, often batched/split)

Reconciliation is the process of proving these three tell the same story — and honestly flagging the transactions where they don't, instead of quietly ignoring them. This is currently done by hand in most finance teams. ReconLoop automates the loop: ingest all three sources, match what can be matched with certainty, reason carefully about what's ambiguous, and never silently drop a record.

**Project codename:** `ReconLoop`

---

## 3. Success Criteria (explicit and measurable — not vibes)

| # | Criterion | Target |
|---|---|---|
| 1 | Batch size | ≥ 50 records per source, minimum 3 sources → demo run at 200+ total records |
| 2 | Match rate | Reported **and validated against known ground truth** (precision / recall / F1) — not self-reported |
| 3 | Throughput | Measured end-to-end: total batch time + records/sec, reported in the output, not just claimed |
| 4 | Exception honesty | 100% of unresolved records classified into a named exception type with a human-readable rationale — zero records silently dropped |
| 5 | False-match rate | Explicitly measured and reported separately (a wrong match is worse than a missed one in finance — judges will notice if we don't call this out) |
| 6 | Reproducibility | Same seed → same synthetic data → same results, every run |

---

## 4. Mapping to Razorpay's 4 Judging Axes

| Axis | How ReconLoop demonstrates it |
|---|---|
| **Problem Taste** | Reconciliation is chosen deliberately over the flashier options (Q&A agent, forecaster) because it maps *literally* onto "match rate + exception list" — no rubric-stretching required. The synthetic data models real reconciliation pain (fee deductions, timing drift, split settlements), not toy data. |
| **Build Quality** | Clean tiered pipeline, typed models end-to-end, tested matcher core, reproducible synthetic data, a real scoring harness against ground truth — not a demo that only works on the happy path. |
| **AI Judgment** | LLM calls are *rationed*, not sprayed everywhere. Deterministic tiers resolve the easy majority for free and instantly; a cheap/fast model handles medium ambiguity; a stronger reasoning model is escalated to only for the hardest residual cases. This is the single biggest differentiator in the whole project — see §11. |
| **Failure Recovery** | Every record that can't be resolved gets a typed exception + rationale instead of being dropped or force-matched. Low-confidence LLM outputs are routed to "needs human review," never silently accepted. Audit log of every LLM call (prompt, response, latency, cost) survives the run. |

---

## 5. Architecture Overview

```
 ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐
 │   Ledger    │  │    Bank     │  │  Gateway Settlement  │
 │  (CSV/JSON) │  │  Statement  │  │      Report          │
 └──────┬──────┘  └──────┬──────┘  └──────────┬───────────┘
        │                │                    │
        └────────────────┼────────────────────┘
                          ▼
                 ┌──────────────────┐
                 │   Normalization   │  ← IDs, dates, amounts,
                 │   & Ingestion     │    currency to canonical form
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  Tier 1: Exact    │  ← reference ID match
                 │  Match (free)     │    across all sources
                 └────────┬─────────┘
                          │ unresolved
                          ▼
                 ┌──────────────────┐
                 │  Tier 2: Fuzzy    │  ← amount tolerance, date
                 │  Match (free)     │    window, string similarity
                 └────────┬─────────┘
                          │ score 60–89 (ambiguous)
                          ▼
                 ┌──────────────────┐
                 │  Tier 3: Fast LLM │  ← Groq Llama 3.3 70B
                 │  Adjudication     │    context: candidates + deltas
                 └────────┬─────────┘
                          │ still ambiguous / low confidence
                          ▼
                 ┌──────────────────┐
                 │  Tier 4: Strong   │  ← Claude / GPT-4o, only for
                 │  LLM Adjudication │    the hardest residual cases
                 └────────┬─────────┘
                          │ unresolved
                          ▼
                 ┌──────────────────┐
                 │  Exception        │  ← typed category +
                 │  Classifier       │    human-readable rationale
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  Postgres         │
                 │  (matches, audit  │
                 │   log, exceptions)│
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐        ┌──────────────────┐
                 │  FastAPI          │───────▶│  Next.js Dashboard│
                 │  (report + score) │        │  (Recharts)       │
                 └──────────────────┘        └──────────────────┘
```

**The core pitch line for the panel:** *"Most transactions never need AI at all — they resolve deterministically in milliseconds. We only spend model calls, and money, on the transactions that actually need judgment — and even then, we escalate model strength only when the cheap model isn't confident."*

---

## 6. Tech Stack & Rationale

| Layer | Choice | Why |
|---|---|---|
| Matching engine core | Python 3.11+ | pandas/rapidfuzz ecosystem is unmatched for this kind of tabular fuzzy matching |
| Fuzzy string matching | `rapidfuzz` | fast, no GPL issues, battle-tested |
| API | FastAPI | Pydantic v2 gives typed request/response for free; you already know this from Handsoff |
| Database | PostgreSQL | transactional integrity for match/exception records; matches your ERP/MES background |
| Fast LLM tier | Groq API (Llama 3.3 70B) | near-instant inference, cheap, good enough for "does this candidate plausibly match" reasoning |
| Strong LLM tier | Claude (Sonnet) or GPT-4o | only for the small residual of genuinely hard cases — this is the story, not the workhorse |
| Orchestration | Plain Python control flow (not LangGraph) | for a pipeline this linear, a hand-rolled tiered dispatcher is more legible to judges than a graph abstraction — simpler is better build quality here |
| Frontend | Next.js + TypeScript + Recharts | dashboard for match-rate visualization, exception drill-down, throughput chart — you already have this muscle memory |
| Synthetic data | Custom Python generator, seeded | full control over injected mismatch types + guaranteed ground truth for scoring |

> **Note on orchestration:** LangGraph is tempting because you know it well, but for a strictly tiered escalation pipeline (deterministic → cheap LLM → strong LLM → exception), a plain dispatcher function is *more* legible to a judging panel reading your repo in 10 minutes. Save LangGraph for a future project where branching/looping agent behavior is actually needed.

---

## 7. Repository Structure

```
reconloop/
├── CLAUDE.md
├── plan.md
├── README.md
├── .env.example
├── config/
│   └── matching_config.yaml        # tolerances, thresholds, model names
├── data/
│   ├── generator/
│   │   └── generate_synthetic_data.py
│   ├── raw/
│   │   ├── ledger.csv
│   │   ├── bank_statement.csv
│   │   └── gateway_settlement.csv
│   └── ground_truth/
│       └── ground_truth_matches.json
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   └── routes/
│   │   │       ├── reconcile.py
│   │   │       ├── exceptions.py
│   │   │       └── score.py
│   │   ├── core/
│   │   │   ├── normalize.py
│   │   │   ├── matcher/
│   │   │   │   ├── exact_match.py
│   │   │   │   ├── fuzzy_match.py
│   │   │   │   └── llm_adjudicator.py
│   │   │   ├── exceptions/
│   │   │   │   └── classifier.py
│   │   │   └── scoring/
│   │   │       └── evaluator.py
│   │   ├── models/                 # SQLAlchemy ORM
│   │   ├── schemas/                # Pydantic models
│   │   └── db/
│   │       └── session.py
│   ├── tests/
│   │   ├── test_exact_match.py
│   │   ├── test_fuzzy_match.py
│   │   ├── test_exceptions.py
│   │   └── test_pipeline_golden.py  # full pipeline vs ground truth
│   └── requirements.txt
├── frontend/
│   └── (Next.js app: dashboard, exception table, match-rate charts)
├── scripts/
│   ├── run_pipeline.py              # CLI entrypoint: ingest → match → report
│   └── run_scoring.py               # CLI: pipeline output vs ground truth
└── docs/
    ├── architecture.md              # exported diagram + writeup
    ├── metrics_log.md                # running record of match-rate/throughput per run
    └── demo_script.md
```

---

## 8. Timeline (Aug 24 → Sep 5, 2026 — 12 days)

Dates are a guide, not handcuffs — you're a student with classes. The order of phases matters more than the exact dates. Build in the order given; don't start the dashboard before the scoring harness works.

| Phase | Dates | Focus |
|---|---|---|
| 0 | Aug 24 | Repo scaffold, env setup, config |
| 1 | Aug 25–26 | Synthetic data generator + ground truth |
| 2 | Aug 26–28 | Deterministic matching engine (Tier 1 + 2) |
| 3 | Aug 28–30 | LLM adjudication tiers (3 + 4) |
| 4 | Aug 30–31 | Exception classifier |
| 5 | Aug 31–Sep 1 | Scoring & validation harness |
| 6 | Sep 1–2 | FastAPI + Postgres persistence |
| 7 | Sep 2–3 | Dashboard (Next.js + Recharts) |
| 8 | Sep 3–4 | Polish, README, architecture doc, audit log |
| 9 | Sep 4–5 | Demo rehearsal, submission, buffer day |

---

## 9. Detailed Phases

### Phase 0 — Repo Scaffold & Environment
**Objective:** A clean, runnable skeleton before any logic is written.

- [x] Initialize git repo, add `.gitignore` (Python + Node + `.env`)
- [x] Create directory structure exactly as in §7
- [x] `backend/`: Python venv, `requirements.txt` (fastapi, uvicorn, pandas, rapidfuzz, sqlalchemy, psycopg2-binary, pydantic, python-dotenv, groq, anthropic, pytest, pytest-cov)
- [x] `frontend/`: `npx create-next-app@latest` with TypeScript + Tailwind
- [x] `.env.example` with `GROQ_API_KEY`, `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`), `DATABASE_URL`
- [x] `config/matching_config.yaml` with placeholder tolerances (see §11)
- [x] Postgres running locally (Docker or native) with an empty `reconloop` database
- [x] README stub with project one-liner and setup instructions

**Deliverable:** `git log` shows an initial commit; `uvicorn app.main:app` returns a health-check 200; Next.js dev server renders a blank page.

**Definition of done:** A stranger can clone the repo and get both servers running from README instructions alone, in under 10 minutes.

---

### Phase 1 — Synthetic Data Generator & Ground Truth
**Objective:** Generate believable, messy, multi-source transaction data with a known-correct answer key.

- [x] Define canonical transaction schema (see §10) shared conceptually across all 3 sources
- [x] Write `generate_synthetic_data.py`: seeded (`random.seed(42)` or CLI-configurable), generates:
  - [x] 200 "true" underlying transactions
  - [x] Ledger CSV (all 200, clean reference IDs)
  - [x] Bank statement CSV (with timing drift, ID reformatting, ~5% missing entries — pending settlement)
  - [x] Gateway settlement CSV (with fee deductions, ~3% duplicated webhook retries, some split settlements)
- [x] Inject **every** mismatch type from §10.2 at a known rate (e.g., 10% timing drift, 8% fee mismatch, 5% missing counterpart, 3% duplicates, 4% split settlements)
- [x] Write `ground_truth_matches.json`: for every one of the 200 true transactions, the correct `ledger_id` / `bank_id(s)` / `gateway_id(s)` and whether it *should* end up UNRESOLVED (some transactions are genuinely still pending — the ground truth must include intentionally-unresolvable cases, not just clean matches)
- [x] Unit test: generator run twice with same seed produces byte-identical files

**Deliverable:** Three CSVs + one ground truth JSON, regeneratable with one command.

**Definition of done:** You can explain, for any record in any file, exactly why it looks the way it does — because you wrote the rule that put it there. This matters for the pitch: you need to be able to point at a specific exception live and say "this one is a fee-deduction mismatch, here's why."

---

### Phase 2 — Deterministic Matching Engine (Tier 1 + Tier 2)
**Objective:** Resolve the majority of records without spending a single LLM call.

- [x] `normalize.py`: canonicalize amounts (paise-level, no floats — use `Decimal`), canonicalize dates (UTC, date-only), canonicalize reference IDs (strip prefixes/leading zeros for comparison, keep raw for display)
- [x] `exact_match.py` (Tier 1): match on normalized reference ID across all sources; O(n) using a hash index, not nested loops
- [x] `fuzzy_match.py` (Tier 2): for records unresolved after Tier 1:
  - [x] Candidate generation: same-side records within a date window
  - [x] Composite confidence score (weights configurable in `matching_config.yaml`):
    - amount similarity (exact / within tolerance / fee-adjusted match)
    - date proximity (same day / within N days)
    - string similarity on narration (`rapidfuzz.fuzz.token_sort_ratio`)
  - [x] Score ≥ 90 → auto-match (still logged, still auditable)
  - [x] Score 60–89 → pass to Tier 3 with the candidate list attached
  - [x] Score < 60 → pass straight to exception classifier (no plausible candidate)
- [x] Unit tests for both tiers using small hand-crafted fixtures (not the full synthetic set) so failures are easy to debug

**Deliverable:** Running the pipeline on the Phase 1 data resolves a first-pass match rate (expect somewhere in the 70–85% range at this stage — the rest is intentionally the hard part).

**Actual result (`python scripts/run_pipeline.py` on the committed seed=42/count=200 sample):** 200 total records, 152 resolved by Tier 1 exact-ID match (76.0% first-pass match rate), 0 additional Tier 2 auto-matches, 68 side-attempts correctly escalated to the Tier 3 band (60–89), 28 side-attempts correctly found no plausible candidate. The 0% Tier 2 auto-match rate is expected, not a bug: ledger narration ("Payment received from X - Invoice INVxxxxx") and bank/gateway narration ("NEFT/XX/INVxxxxx", "Payout - MCHTxxxx - Order ORDxxxxx") are deliberately different styles per source (§10.1), so `token_sort_ratio` scores them low even for the obviously-correct candidate — which is exactly why those cases are designed to escalate to Tier 3's judgment rather than auto-match, per Golden Rule #2 (never force a match to be helpful).

**Definition of done:** Every match, at every tier, is logged with *why* it matched (which rule, which score) — not just a boolean.

---

### Phase 3 — LLM Adjudication (Tier 3 + Tier 4)
**Objective:** Spend AI judgment only where deterministic rules genuinely can't decide — and make that restraint visible.

- [x] `llm_adjudicator.py`: given an ambiguous record + its candidate(s) + computed deltas (amount diff, date diff, narration diff), build a structured prompt asking the model to output a **structured decision**: `MATCH` / `NO_MATCH` / `NEEDS_HUMAN_REVIEW`, with a confidence score and a one-sentence rationale
- [x] Tier 3 (fast/cheap): Groq Llama 3.3 70B — used first for all score-60–89 records
- [x] Escalation rule: if Tier 3 confidence < configurable threshold (e.g., 70) **or** the model itself asks for review → escalate to Tier 4
- [x] Tier 4 (strong): Claude Sonnet or GPT-4o — only the residual hard cases reach here (expect this to be a small single-digit percentage of the total batch — track and report this percentage explicitly, it's a selling point)
- [x] Every LLM call (both tiers) logged to an audit table: prompt, response, model, latency, estimated cost, final decision
- [x] Hard rule: if the model's own confidence is below threshold, it does **not** get to force a match — routes to exception instead. This is the single most important guardrail in the whole project for "Failure Recovery."
- [x] Unit tests: mock the LLM client, test the escalation logic and the confidence-gate logic in isolation (don't burn API calls in CI)

**Deliverable:** Second-pass match rate after LLM tiers, plus a clear breakdown: "% resolved by Tier 1 / Tier 2 / Tier 3 / Tier 4 / still unresolved."

**Status: implementation complete and unit-tested; the live number is not yet captured.** `GROQ_API_KEY`/`ANTHROPIC_API_KEY` are unset in this environment (`.env` doesn't exist, only `.env.example`), and Tier 3/4 make real, billed API calls — so `python scripts/run_pipeline.py --use-llm` has not been run against the real seed=42/count=200 batch. All escalation/confidence-gate/audit-log logic is verified with `FakeLLMClient` (77 tests in `test_llm_adjudicator.py` + `test_llm_clients.py` + `test_run_pipeline_llm.py`), including a fixture that drives the full Tier 1 → Tier 2 → Tier 3 → Tier 4 → resolved/unresolved path end to end. To get the real second-pass number: fill in both keys in `.env` (copy from `.env.example`), then run `python scripts/run_pipeline.py --use-llm` from the repo root — it prints the Tier 3/4 breakdown, LLM call count, estimated cost, and second-pass match rate. Once run, record the result here and in `docs/metrics_log.md` (Phase 5).

**Definition of done:** You can state, with a number, what fraction of the batch needed AI at all — and defend why that number is honest, not padded. *(Pending the live `--use-llm` run above — the logic that would produce that number is built and tested, but the number itself isn't fabricated here.)*

---

### Phase 4 — Exception Classifier
**Objective:** Nothing disappears. Every unresolved record gets a name and a reason.

- [x] `classifier.py`: bucket every still-unresolved record into one of the named exception types (§12)
- [x] Rule-based classification first (e.g., no candidate at all within date window → `MISSING_COUNTERPART`); LLM rationale only used to *explain*, not to *classify*, keeping this tier fast and deterministic
- [x] Every exception gets: type, affected record IDs, a one-paragraph human-readable rationale, and a suggested next action (e.g., "check gateway dashboard for settlement batch X")
- [x] Unit test: every record in the synthetic batch, after full pipeline run, is accounted for in either `matches` or `exceptions` — assert `len(matches) + len(exceptions) == total_records` as a hard invariant

**Deliverable:** A complete, typed exception report — zero unaccounted-for records.

**Actual result (`backend/tests/test_pipeline_golden.py`, run over the committed seed=42/count=200 sample with a deterministic fake LLM client — no real API calls):** the invariant `resolved + len(exceptions) == 200` holds exactly. Breakdown on the real data (`python scripts/run_pipeline.py`, Tier 1 + Tier 2 only, no `--use-llm`): 152 Tier 1 matches, 38 records still pending Tier 3/4 (not force-classified — see below), and exactly 10 classified now: 8 `MISSING_COUNTERPART` + 2 `AMOUNT_MISMATCH_BEYOND_TOLERANCE`. All 10 correspond exactly to the ground truth's 10 `missing_counterpart` records — the classifier found every one of them, though 2 got a technically-defensible-but-slightly-generous `AMOUNT_MISMATCH_BEYOND_TOLERANCE` label instead of `MISSING_COUNTERPART`: a coincidental, unrelated leftover record from a *different* transaction happened to land in their date window with a partially-correlated (not zero, not close) amount score. Exact-zero amount scores are caught and routed to `MISSING_COUNTERPART` instead (see `classifier.py`'s amount-mismatch check), but a moderate coincidental score can still slip through; this is a known, documented limitation, not a bug to silently paper over.

Design decision worth recording: records Tier 2 escalated to Tier 3 (`ESCALATE_TIER3`) are reported as **pending**, not force-classified, when `--use-llm` isn't used — classifying them without ever asking the LLM would be guessing at a disposition the pipeline hasn't actually reached, which contradicts Golden Rule #2. The invariant only applies to the *fully run* pipeline (Tier 1 → 4); `pending_llm_count` must be 0 for it to hold, which the golden test confirms by driving every escalation to a terminal Tier 3/4 outcome with a deterministic fake client.

**Definition of done:** The invariant test above passes on every run, and is wired into CI (or at minimum, run before every commit).

---

### Phase 5 — Scoring & Validation Harness
**Objective:** Prove the match rate is real, not cherry-picked.

- [x] `evaluator.py`: compare pipeline output against `ground_truth_matches.json`
- [x] Compute and report:
  - Precision, recall, F1 on matches
  - **False-match rate** reported separately and prominently (a match that shouldn't have happened)
  - Missed-match rate (should have matched, ended up as exception)
  - Per-tier breakdown of where correct matches came from
  - Throughput: total pipeline wall-clock time, records/sec, LLM calls made, estimated LLM cost for the batch
- [x] `run_scoring.py` CLI: single command, human-readable table output + JSON export
- [x] Log every run's metrics to `docs/metrics_log.md` (append-only) so you have a visible history of improvement to show the panel if asked

**Deliverable:** A single command that produces the exact numbers §3 requires, reproducibly.

**Actual result (`python scripts/run_scoring.py --no-log`, committed seed=42/count=200 sample, Tier 1 + Tier 2 only, no `--use-llm`):** precision 1.000, recall 0.800, F1 0.889, false-match rate 0.0%, missed-match rate 19.0%, 76.0% of records resolved at Tier 1, 0% at Tier 2 (see Phase 2's note on why narration style differs deliberately per source), 152/200 matched, 38 pending Tier 3/4, 10 exceptions. Recall is capped at 80% here only because Tier 3/4 haven't been run yet (`pending_llm_count` = 38, scored as not-yet-matched rather than guessed at, per Golden Rule #2) — this is not a matcher defect. Zero false matches confirms Tier 1's exact-ID matching and Tier 2's ≥90 auto-match threshold are both behaving conservatively as designed. First entry recorded in `docs/metrics_log.md`.

**Definition of done:** You can run this cold, from a fresh clone, on a machine that's never seen the project, and get the same numbers. *(Confirmed: 117/117 backend tests pass, including a subprocess-level integration test of `run_scoring.py` itself; the numbers above reproduce exactly on repeat runs against the seeded sample data.)*

---

### Phase 6 — API + Persistence
**Objective:** Wrap the pipeline in a real service, not just a script.

- [x] SQLAlchemy models: `Transaction`, `Match`, `Exception`, `LLMAuditLog`
- [x] `POST /reconcile` — trigger a pipeline run on the current data batch, persist results
- [x] `GET /matches` — paginated match list with confidence + tier
- [x] `GET /exceptions` — paginated exception list with type + rationale
- [x] `GET /score` — latest scoring metrics
- [x] Pydantic schemas for all request/response bodies (typed, no raw dicts)
- [x] Basic integration test: spin up test DB, POST /reconcile, assert response shape

**Deliverable:** A running FastAPI service backed by Postgres, fully typed.

**Actual result:** `ReconciliationRun` scopes every `Match`, `Exception`, and `LLMAuditLog` row to one `POST /reconcile` call, so results from different runs never mix. `persist_pipeline_report()` (`backend/app/core/persistence.py`) writes a `run_pipeline()` report straight into the DB without coupling the matching engine to SQLAlchemy — `scripts/run_pipeline.py` and `run_scoring.py` are untouched and still produce identical numbers (152 matches, 10 exceptions, 76.0% Tier-1 match rate on the seed=42/count=200 sample). `GET /score` recomputes precision/recall/F1 via the same `evaluator.score_pipeline()` `run_scoring.py` uses — never a value stored verbatim from the pipeline, always re-derived against ground truth. `DATABASE_URL` drives a module-level engine (Postgres by default per `.env.example`); the integration test (`backend/tests/test_api.py`) overrides it to a temp SQLite file per test, so the full endpoint suite runs without a live Postgres instance — this environment has Postgres installed but not running, and Docker unavailable, so real-Postgres verification is deferred to whoever has a running instance; SQLite is not used in production. 120/120 backend tests pass at 99% coverage.

**Definition of done:** `curl` against all four endpoints returns sane, typed JSON on a fresh database. *(Verified via TestClient against a fresh SQLite DB: `/health`, `POST /reconcile`, `GET /matches`, `GET /exceptions`, `GET /score` all return 200 with the expected shape; `GET /score` with no persisted run correctly 404s instead of fabricating a result.)*

---

### Phase 7 — Dashboard
**Objective:** A judge should understand the whole system in 15 seconds of looking at the screen.

- [ ] Match-rate summary card (overall %, by tier, by source pair)
- [ ] Throughput card (records/sec, total time, LLM calls made)
- [ ] Exception table: filterable by type, expandable to show rationale + record details
- [ ] A "false-match rate" callout — make it visible, don't bury it, this is a trust signal
- [ ] Trigger button to re-run reconciliation on demand (calls `/reconcile`)
- [ ] Recharts: bar chart of match tier breakdown, line/bar of throughput per run

**Deliverable:** A single-page dashboard that tells the whole story without narration.

**Definition of done:** Someone who has never seen the project can open the dashboard and correctly explain what happened in the last run.

---

### Phase 8 — Polish, Docs, Demo Prep
**Objective:** Make the repo and the pitch as legible as the code.

- [ ] `README.md`: one-liner, architecture diagram (from §5), setup instructions, how to run the demo, how to regenerate data
- [ ] `docs/architecture.md`: expanded version of §5 with the "why" for each tier
- [ ] `docs/demo_script.md`: exact script for the 5-minute pitch (see §14)
- [ ] Clean up dead code, remove hardcoded values into `matching_config.yaml`
- [ ] Verify `.env.example` is complete and no real secrets are committed (double-check `git log` for accidental key commits)
- [ ] Record a backup demo video/GIF in case live demo has issues on the day

**Deliverable:** A repo a stranger can understand in 10 minutes without you in the room.

**Definition of done:** Ask someone (classmate, senior) with no context to read the README and explain the project back to you correctly.

---

### Phase 9 — Submission & Buffer
- [ ] Final end-to-end run, capture fresh metrics for the pitch
- [ ] Public GitHub repo confirmed public, README renders correctly on GitHub
- [ ] Submit via razorpay.com/buildathon/ before Sep 5 deadline
- [ ] Buffer day for anything that breaks at the last minute — do not plan to submit exactly at the deadline

---

## 10. Data Design

### 10.1 Canonical Transaction Fields (conceptual — actual columns differ slightly per source to be realistic)

| Field | Ledger | Bank Statement | Gateway Settlement |
|---|---|---|---|
| Reference ID | `TXN-000123` | `0000123` (bank-truncated) | `pay_00123` (gateway prefix) |
| Amount | ₹10,000.00 | ₹10,000.00 (if no fee) | ₹9,882.00 (fee-adjusted) |
| Date | transaction date | settlement date (T+1/T+2) | payout date |
| Narration | free text, ledger-style | free text, bank-style (often truncated/abbreviated) | free text, gateway-style |
| Counterparty | name | account number fragment | merchant/payout ID |

### 10.2 Injected Mismatch Types (with target injection rate)

| Type | Rate | Description |
|---|---|---|
| Clean exact match | ~55% | Baseline — same ID recoverable across all 3 sources after normalization |
| ID format drift | ~15% | Same transaction, differently formatted reference ID per source |
| Timing drift | ~10% | Ledger date vs bank/gateway date differ by 1–3 days (clearing delay) |
| Fee-adjusted amount | ~8% | Gateway amount = ledger amount − fee − GST on fee (must be fee-schedule aware) |
| Missing counterpart | ~5% | Genuinely pending settlement — **should** end up unresolved, ground truth marks this explicitly |
| Duplicate entry | ~3% | Same transaction appears twice in one source (webhook retry) |
| Split settlement | ~4% | One ledger invoice ↔ two gateway settlement records, or vice versa |

### 10.3 Ground Truth Schema (`ground_truth_matches.json`)

```json
{
  "true_transaction_id": "GT-0042",
  "ledger_id": "TXN-000042",
  "bank_ids": ["0000042"],
  "gateway_ids": ["pay_00042"],
  "expected_outcome": "MATCH",
  "mismatch_type": "fee_adjusted",
  "notes": "Gateway amount is ledger amount minus 2% fee minus 18% GST on fee"
}
```

For intentionally-unresolvable cases, `expected_outcome` is `"UNRESOLVED"` with the correct exception type, so the scoring harness can check that the pipeline correctly identifies genuine exceptions rather than forcing a bad match.

---

## 11. Matching Logic Spec

### 11.1 Confidence Score Formula (Tier 2)

```
score = (0.4 × amount_score) + (0.35 × date_score) + (0.25 × narration_score)
```

- `amount_score`: 100 if exact, scaled down by % deviation, with a fee-schedule check applied before penalizing (a known fee deduction should not tank the score)
- `date_score`: 100 if same day, linearly decaying to 0 at the configured max window (default 5 days)
- `narration_score`: `rapidfuzz.fuzz.token_sort_ratio`, 0–100 directly

Default thresholds (all in `matching_config.yaml`, not hardcoded):
- `AUTO_MATCH_THRESHOLD = 90`
- `LLM_TIER_THRESHOLD = 60`
- `TIER3_CONFIDENCE_ESCALATION_THRESHOLD = 70`

### 11.2 LLM Prompt Contract (Tier 3 & 4)

The model receives: the ambiguous record, its 1–3 candidates, and pre-computed deltas (never raw unprocessed data — the deterministic layer does the math, the model does the judgment call). The model must return structured JSON: `{decision, confidence, rationale}`. Never free text. Reject and retry once on malformed output; if still malformed, route straight to exception with type `LLM_OUTPUT_UNPARSEABLE` (this itself is a Failure Recovery signal worth having, honestly).

---

## 12. Exception Taxonomy

| Type | Meaning |
|---|---|
| `MISSING_COUNTERPART` | No plausible candidate found in the other source(s) within the search window |
| `AMOUNT_MISMATCH_BEYOND_TOLERANCE` | Best candidate's amount differs beyond what fee-schedule logic can explain |
| `TIMING_DRIFT_EXCEEDED` | Best candidate is outside the configured date window |
| `DUPLICATE_CANDIDATE` | More than one equally strong candidate — genuinely ambiguous, not resolved by tie-break rules |
| `SPLIT_SETTLEMENT_UNRESOLVED` | Evidence of a one-to-many relationship but the pieces don't sum correctly |
| `LOW_CONFIDENCE_AMBIGUOUS` | LLM tiers explicitly declined to decide (confidence below threshold) |
| `LLM_OUTPUT_UNPARSEABLE` | Model failed to return valid structured output twice in a row |
| `UNCLASSIFIED` | Escape hatch — should be rare; if this bucket is large, the taxonomy needs revisiting before submission |

---

## 13. Scoring & Validation Harness — Metrics Reported

- Precision / Recall / F1 (overall, and per source-pair)
- False-match rate (matches made that ground truth says shouldn't exist) — **reported even if zero**, don't omit a metric because it's good news, state it explicitly
- Missed-match rate (should have matched, ended up in exceptions)
- Per-tier match attribution (what % resolved at Tier 1 / 2 / 3 / 4)
- Throughput: total wall-clock time, records/sec
- LLM cost: total calls made, estimated $ cost, cost per resolved-via-LLM record

---

## 14. Demo & Pitch Plan (5 minutes)

| Time | Content |
|---|---|
| 0:00–0:30 | Problem: reconciliation is still manual; verification capacity is the bottleneck, not generation speed |
| 0:30–1:30 | Architecture: tiered pipeline, emphasize AI is rationed, not sprayed |
| 1:30–3:00 | Live run: trigger `/reconcile` on fresh synthetic batch, dashboard updates live, walk through match-rate + throughput numbers |
| 3:00–4:00 | Honest exceptions: click into 2–3 specific exceptions, explain rationale, show the false-match rate is measured and reported, not hidden |
| 4:00–4:30 | What scaling looks like: note on batching/async for thousands of records, note on where a human stays in the loop |
| 4:30–5:00 | Close: restate the numbers, stop talking |

---

## 15. Submission Checklist

- [ ] Public GitHub repo, README renders cleanly
- [ ] `plan.md` and `CLAUDE.md` present (shows process, not just output)
- [ ] Fresh end-to-end run with final metrics captured
- [ ] Architecture diagram exported/visible in README
- [ ] Demo script rehearsed at least twice, timed under 5 minutes
- [ ] No secrets committed — double-check `.env` is gitignored
- [ ] Submitted at razorpay.com/buildathon/ before Sep 5, 2026

---

## 16. Risk Register

| Risk | Mitigation |
|---|---|
| LLM API rate limits/downtime during live demo | Cache a known-good run's results as fallback; have a recorded backup demo |
| Synthetic data too clean, matcher looks trivially good | Deliberately stress-test with adversarial cases in Phase 1 before declaring it done |
| Scope creep into forecasting/Q&A features | Plan explicitly excludes them — resist adding "just one more track" mid-build |
| Running out of time before Phase 7/8 | Dashboard can be simplified to a single summary page if time is short — scoring harness (Phase 5) and exception honesty (Phase 4) are non-negotiable, dashboard polish is not |
| Class schedule conflicts with timeline | Timeline has built-in slack (Phase 9 buffer day); reorder within a phase if needed, but don't skip Phase 5's ground-truth validation — it's the single most differentiating deliverable |

---

## 17. Stretch Goals (only if Phases 0–9 are done early)

- Batch async processing to demonstrate scaling to 1,000+ records
- A second synthetic scenario with a different mismatch profile, to show the pipeline generalizes
- Cost/latency comparison chart: Tier 3 vs Tier 4 model, to make the "AI Judgment" story even more concrete
- Simple human-review UI stub for `NEEDS_HUMAN_REVIEW` / `LOW_CONFIDENCE_AMBIGUOUS` exceptions
