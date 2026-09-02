# ReconLoop Demo Script (5 minutes)

Expanded from `plan.md` §14. Rehearse this at least twice, timed, before submission —
see `plan.md` §15 Submission Checklist.

**Before you start:** have both servers running (`uvicorn app.main:app --reload` in
`backend/`, `npm run dev` in `frontend/`), the dashboard open at
`http://localhost:3000`, and — if you're demoing live LLM calls — `GROQ_API_KEY` /
`ANTHROPIC_API_KEY` filled in `.env`. If you're not confident about live API/network
access on the day, run `python scripts/run_pipeline.py && python scripts/run_scoring.py`
beforehand so `POST /reconcile` on the day just reproduces a known-good result, and
have the backup recording ready as a fallback (see `plan.md` §8 checklist).

---

## 0:00–0:30 — The problem

> "Every business has three records of the same transaction that never quite agree:
> the ledger — what the accounting system says happened. The bank statement — what
> actually hit the account. And the payment gateway's settlement report — what the
> processor says it paid out, net of fees. Reconciling these by hand is still how most
> finance teams do it today. The bottleneck isn't generating more transactions — it's
> verifying the ones you already have. That's the loop ReconLoop closes."

## 0:30–1:30 — Architecture

Show `docs/architecture.md`'s diagram or the one in the README, or just narrate it:

> "This is a tiered pipeline, not a single model call. Every record first tries an
> exact ID match — free, instant. What's left tries a fuzzy match on amount, date, and
> narration — still free. Only what's left after *that* — genuinely ambiguous records
> — goes to a fast, cheap LLM. And only if *that* model isn't confident does it escalate
> to a stronger model. On our test batch, 76% of records never touch AI at all. The
> story here isn't 'we used an LLM' — it's that we rationed it to the minority of cases
> that actually needed judgment."

**Key line to land:** *"Most transactions never need AI at all — they resolve
deterministically in milliseconds. We only spend model calls, and money, on the
transactions that actually need judgment."*

## 1:30–3:00 — Live run

1. Click **Run reconciliation** on the dashboard.
2. While it runs, narrate: "This is hitting the same pipeline as our CLI script —
   `POST /reconcile` — against a synthetic batch of 200 transactions across three
   sources, seeded so it's reproducible."
3. Point at the summary cards as they populate: match rate, throughput
   (records/sec, wall-clock), and — explicitly — the false-match rate callout.
4. Point at the tier-attribution chart: "Here's the breakdown — this bar is Tier 1,
   free and instant. This much escalated to the fast model. This sliver, if any,
   needed the strong model."

Have the last known-good numbers on hand in case of a live-network hiccup — see
`docs/metrics_log.md` for the latest recorded run.

## 3:00–4:00 — Honest exceptions

1. Scroll to the exception table, filter by type (e.g. `MISSING_COUNTERPART`).
2. Click into 2–3 rows to expand rationale + suggested action.
3. Say explicitly: "Every one of these got a name and a reason — nothing was silently
   dropped. We can prove that: `len(matches) + len(exceptions)` equals the total
   record count on every single run, and that's enforced by a test, not a claim."
4. Point back at the false-match-rate card: "And this number — matches we made that
   shouldn't have happened — is reported even when it's zero. A wrong match is worse
   than a missed one in finance, so we don't get to hide this number just because it's
   good news."

## 4:00–4:30 — What scaling looks like

> "Today this runs synchronously against 200 records in well under a second for the
> deterministic tiers. To scale to thousands of records, the LLM tiers would move to
> async batched calls — the escalation logic doesn't change, only the concurrency
> model. And a human stays in the loop exactly where the pipeline says confidence ran
> out: `NEEDS_HUMAN_REVIEW` and `LOW_CONFIDENCE_AMBIGUOUS` exceptions are designed to
> be the handoff point, not a dead end."

## 4:30–5:00 — Close

> "To recap: [state the actual match rate, false-match rate, and % resolved without AI
> from the run you just showed]. Every number on this screen is measured against a
> known ground truth, not self-reported. That's ReconLoop."

Stop talking. Let the numbers sit.

---

## Fallback plan

If live `/reconcile` fails (network, API rate limit, demo Wi-Fi):

1. Have a terminal ready with `python scripts/run_pipeline.py && python scripts/run_scoring.py`
   pre-run once before the session so Postgres/the dashboard already has a persisted
   run to show via `GET /score` and `GET /exceptions` without needing a fresh trigger.
2. Fall back to the recorded backup demo (`plan.md` §8) and narrate over it.
3. Never skip the exception walkthrough or the false-match-rate callout even in the
   fallback — those are the differentiators, not the live-run animation.
