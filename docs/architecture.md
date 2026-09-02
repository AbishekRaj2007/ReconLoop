# ReconLoop Architecture

This is the expanded version of `plan.md` §5 — the pipeline diagram plus the reasoning
behind each stage. If plan.md is the build log, this is the "why," written for someone
reading the repo cold.

## The core idea

Every real business has three records of the same transaction that never quite agree:
the ledger (what the ERP says happened), the bank statement (what actually cleared),
and the payment gateway's settlement report (what the processor says it paid out, net
of fees). Reconciliation is proving these three tell the same story, and honestly
flagging the ones where they don't.

The pipeline is a **tiered dispatcher**, not a single matching function. Each record
that isn't yet resolved is handed to progressively more expensive tiers, in order, and
stops at the first tier that can resolve it with confidence:

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

## Why a tiered dispatcher, not a graph or a single model call

The tempting shortcut is to hand every record to an LLM and ask "does this match?" It
would work, but it would also be the least defensible design in the room: expensive,
slow, and — worse — it hides how much of the problem is actually easy. The whole point
of ReconLoop is to make that visible: **most transactions never need AI at all.** They
resolve deterministically, in milliseconds, for free. Money and latency are spent only
on the transactions that are genuinely ambiguous, and even then a cheap model is tried
before a strong one.

A plain tiered dispatcher was chosen over an agent-graph framework (LangGraph, etc.)
because the control flow here is strictly linear — deterministic → cheap LLM → strong
LLM → exception, with no branching or looping back. A graph abstraction would add
indirection a judge has to trace through without adding any capability the linear
version doesn't already have. Simpler is more legible, and legibility is part of Build
Quality.

## Normalization

Before any matching happens, every record is canonicalized: amounts to `Decimal`
(paise-level, never `float` — this is financial data), dates to UTC date-only, and
reference IDs to a comparable form with prefixes/leading zeros stripped (the raw form
is kept for display). Doing this once up front means every downstream tier compares
like with like — Tier 1's exact match, in particular, is only as good as normalization
makes it.

## Tier 1 — Exact match (free)

Matches on the normalized reference ID across all three sources using a hash index —
O(n), not nested loops. This resolves the clean baseline: the ~55% of transactions
injected with no mismatch, plus the ~15% with only ID-format drift (since
normalization already undoes that). On the committed seed=42/count=200 sample this
tier alone resolves 76% of records. Every Tier 1 match is still logged with which rule
matched — "free" doesn't mean "unaudited."

## Tier 2 — Fuzzy match (free)

For records Tier 1 couldn't resolve, Tier 2 generates candidates from the same side
within a date window and scores them:

```
score = 0.4 × amount_score + 0.35 × date_score + 0.25 × narration_score
```

- `amount_score`: 100 if exact, scaled down by deviation, with a fee-schedule check
  applied first — a known 2% fee + 18% GST on the fee should not tank the score just
  because it looks like a mismatch.
- `date_score`: 100 same-day, decaying linearly to 0 at the configured window (default
  5 days) — clearing delay is expected, not an anomaly.
- `narration_score`: `rapidfuzz.fuzz.token_sort_ratio` on free-text narration.

A score ≥ 90 auto-matches (still logged and auditable). A score of 60–89 is genuinely
ambiguous and is handed to Tier 3 with its candidate list and pre-computed deltas
attached. Below 60, there's no plausible candidate at all, and the record skips
straight to the exception classifier — asking an LLM to adjudicate "is there any
match here" when the deterministic layer already found nothing plausible would just be
paying for a coin flip.

**On the sample data, Tier 2's auto-match rate is 0%, by design, not by bug.** Ledger
narration reads like `"Payment received from X - Invoice INVxxxxx"`; bank and gateway
narration read like `"NEFT/XX/INVxxxxx"` or `"Payout - MCHTxxxx - Order ORDxxxxx"`.
Those are deliberately different styles per source (see plan.md §10.1), so
`token_sort_ratio` scores them low even for the obviously-correct candidate — which is
exactly why those cases are supposed to escalate to a tier that can read narration for
meaning rather than string shape, instead of being force-matched to inflate the
headline number.

## Tier 3 — Fast LLM adjudication (Groq, Llama 3.3 70B)

Every score-60–89 record reaches Tier 3 first. The model receives the ambiguous
record, its 1–3 candidates, and pre-computed deltas (amount diff, date diff, narration
diff) — never raw unprocessed data. The deterministic layer does the arithmetic; the
model does the judgment call. It must return structured JSON —
`{decision, confidence, rationale}` — never free text parsed with regex. Malformed
output is rejected and retried once; if it's still malformed, the record routes to
`LLM_OUTPUT_UNPARSEABLE` rather than being retried indefinitely or silently dropped.

## Tier 4 — Strong LLM adjudication (escalation only)

If Tier 3's confidence is below `TIER3_CONFIDENCE_ESCALATION_THRESHOLD` (configurable,
default 70), **or** the model itself asks for human review, the record escalates to a
stronger model (Claude Sonnet or GPT-4o). This tier should only ever see a small,
single-digit percentage of the total batch — that percentage is tracked and reported
explicitly, because it's the clearest evidence that AI judgment is rationed rather than
sprayed everywhere.

## The confidence gate — the single most important guardrail

At both LLM tiers, a decision below the confidence threshold **does not get to force a
match.** It routes to the exception classifier instead, exactly like a deterministic
non-match would. A false match is worse than a missed one in finance — auto-matching a
low-confidence guess "to be helpful" would look good on a leaderboard and be wrong in
practice. This rule has no override path in the code.

## Exception classification

Nothing disappears: every record that survives all four tiers unresolved gets bucketed
into a named exception type (`MISSING_COUNTERPART`,
`AMOUNT_MISMATCH_BEYOND_TOLERANCE`, `TIMING_DRIFT_EXCEEDED`, `DUPLICATE_CANDIDATE`,
`SPLIT_SETTLEMENT_UNRESOLVED`, `LOW_CONFIDENCE_AMBIGUOUS`, `LLM_OUTPUT_UNPARSEABLE`,
or the deliberately-rare `UNCLASSIFIED` escape hatch), with a human-readable rationale
and a suggested next action. Classification is rule-based, not LLM-based — the model's
job (when it was consulted) was judgment on the match itself, not on how to file the
paperwork afterward. This keeps the classifier fast, deterministic, and testable in
isolation.

The invariant `len(matches) + len(exceptions) == total_records` is enforced by
`backend/tests/test_pipeline_golden.py` on every run and is never weakened to make a
demo look cleaner.

## Audit trail

Every LLM call, at both tiers, is logged to the `LLMAuditLog` table: prompt, response,
model name, latency, and estimated cost. This is what makes the "AI Judgment" story
defensible under questioning — a judge can ask "show me one of the hard ones" and get
an actual prompt and an actual model response, not a claim.

## Scoring

`backend/app/core/scoring/evaluator.py` compares pipeline output against
`data/ground_truth/ground_truth_matches.json` — the same seeded, deterministic
generator output every run reproduces exactly. It reports precision, recall, F1, and,
prominently, the **false-match rate** (matches made that shouldn't have happened) and
**missed-match rate** (should have matched, ended up in exceptions) — reported
separately even when zero, because a good number omitted reads as a number being
hidden. Per-tier attribution shows where correct matches actually came from.

## API and persistence

FastAPI wraps the pipeline: `POST /reconcile` triggers a run and persists it,
`GET /matches` / `GET /exceptions` return paginated results scoped to that run,
`GET /score` recomputes precision/recall/F1 against ground truth on demand rather than
serving a cached number verbatim. Every SQLAlchemy row is scoped to a
`ReconciliationRun`, so results from different runs never mix. The Next.js dashboard
is a thin, single-page client over these four endpoints — see the "Architecture at a
glance" section in the README for the same diagram at a higher level.
