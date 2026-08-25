"""ReconLoop synthetic data generator (Phase 1).

Generates three source files that describe the same underlying set of "true"
transactions from three different, imperfectly-agreeing points of view:

  - ledger.csv              the internal system of record (clean reference IDs)
  - bank_statement.csv      what actually hit the account, on the bank's clock
  - gateway_settlement.csv  what the payment gateway says it paid out, net of fees

...plus ground_truth_matches.json, the answer key used only for scoring — never
fed to the matcher.

Deterministic: the same --seed and --count always produce byte-identical output.
No wall-clock time, no OS randomness, no unordered-dict iteration is used anywhere
in the generation path.

Every one of the 200 (default) true transactions is assigned exactly one scenario
from the seven mismatch categories in plan.md §10.2, at the rate configured in
config/matching_config.yaml under `injection_rates` (the single source of truth
for these rates — this script does not duplicate the numbers). Category counts are
apportioned via the largest-remainder method so they always sum to exactly
--count, for any count, not just 200.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

# Order matters: it is the deterministic tie-break order for the largest-remainder
# apportionment below, and the label used as `mismatch_type` in the ground truth
# (fee_adjusted_amount -> "fee_adjusted" to match plan.md §10.3's example schema;
# clean_exact_match -> None, since a clean match is not a "mismatch").
CATEGORY_ORDER = [
    "clean_exact_match",
    "id_format_drift",
    "timing_drift",
    "fee_adjusted_amount",
    "missing_counterpart",
    "duplicate_entry",
    "split_settlement",
]
MISMATCH_TYPE_LABEL = {
    "clean_exact_match": None,
    "id_format_drift": "id_format_drift",
    "timing_drift": "timing_drift",
    "fee_adjusted_amount": "fee_adjusted",
    "missing_counterpart": "missing_counterpart",
    "duplicate_entry": "duplicate_entry",
    "split_settlement": "split_settlement",
}

# Fixed scenario rules (deterministic, not randomized per record — every record's
# shape is fully explained by which category it belongs to plus these constants).
FEE_PCT = Decimal("0.02")
GST_ON_FEE_PCT = Decimal("0.18")
SPLIT_RATIO_A = Decimal("0.6")
BANK_CLEAR_DAYS = 1
GATEWAY_PAYOUT_DAYS = 2
TIMING_DRIFT_EXTRA_DAYS = 3

BASE_DATE = date(2026, 6, 1)
DATE_SPREAD_DAYS = 89
AMOUNT_MIN_PAISE = 50_000  # ₹500.00
AMOUNT_MAX_PAISE = 5_000_000  # ₹50,000.00

NAME_POOL = [
    "Aarav Mehta Traders",
    "Bhoomi Textiles",
    "Chandan Logistics",
    "Devika Foods",
    "Elite Steel Works",
    "Farah Exports",
    "Ganga Agro Mills",
    "Hariom Electronics",
    "Ishaan Furnishings",
    "Jyoti Pharma",
    "Kavya Interiors",
    "Lakshya Motors",
    "Meera Handicrafts",
    "Nandan Plastics",
    "Omkar Constructions",
    "Priya Bakers",
    "Quilon Spices",
    "Ravi Print House",
    "Sanchi Garments",
    "Trisha Organics",
    "Utsav Events",
    "Vikram Hardware",
    "Waseem Auto Parts",
    "Yashika Jewellers",
    "Zenith Consulting",
    "Anand Book Depot",
    "Bansal Dairy",
    "Cauvery Ceramics",
    "Deshmukh Freight",
    "Everest Stationers",
]

CSV_DECIMAL = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CSV_DECIMAL, rounding=ROUND_HALF_UP)


def decimal_amount(rng: random.Random) -> Decimal:
    paise = rng.randint(AMOUNT_MIN_PAISE, AMOUNT_MAX_PAISE)
    return money(Decimal(paise) / Decimal(100))


def initials(name: str) -> str:
    return "".join(word[0] for word in name.split()).upper()


def ledger_narration(name: str, invoice_no: str) -> str:
    return f"Payment received from {name} - Invoice {invoice_no}"


def bank_narration(name: str, invoice_no: str) -> str:
    return f"NEFT/{initials(name)}/{invoice_no}"


def gateway_narration(merchant_id: str, order_no: str) -> str:
    return f"Payout - {merchant_id} - Order {order_no}"


def transposition_candidates(canonical: str) -> list[str]:
    """All results of swapping one adjacent pair of differing digits, ordered
    rightmost pair first. Scanning multiple positions (rather than a single
    fixed swap) is what lets `unique_transposed_id` dodge collisions."""
    chars = list(canonical)
    candidates = []
    for pos in range(len(chars) - 1, 0, -1):
        if chars[pos] != chars[pos - 1]:
            swapped = chars.copy()
            swapped[pos], swapped[pos - 1] = swapped[pos - 1], swapped[pos]
            candidates.append("".join(swapped))
    return candidates


def unique_transposed_id(canonical: str, used: set[str]) -> str:
    """A transcription-error-style corruption of `canonical` that (a) actually
    differs from it and (b) doesn't collide with any bank_id already in use —
    a corrupted ID landing on another real transaction's canonical ID would be
    a duplicate-ID bug, not the intended "drift" scenario.

    IDs with only one significant digit (e.g. "0000006") have very few adjacent
    transpositions, and in a dense ID space (hundreds of records packed into a
    small numeric range) most of those collide with another real record. The
    fallback corrupts the leading digit instead: since every canonical ID here
    starts with "0" (count is always far below 10,000,000), setting it to a
    nonzero digit pushes the value far outside the range any real record could
    occupy, so it is essentially guaranteed collision-free — still checked
    against `used` to be certain."""
    for candidate in transposition_candidates(canonical):
        if candidate not in used:
            return candidate
    for leading_digit in "123456789":
        candidate = leading_digit + canonical[1:]
        if candidate not in used and candidate != canonical:
            return candidate
    raise RuntimeError(f"could not find a unique corrupted id for {canonical}")


def load_injection_rates(config_path: Path) -> dict[str, float]:
    with config_path.open() as f:
        config = yaml.safe_load(f)
    rates = config["injection_rates"]
    missing = set(CATEGORY_ORDER) - set(rates)
    if missing:
        raise ValueError(f"config missing injection_rates for: {sorted(missing)}")
    return {cat: float(rates[cat]) for cat in CATEGORY_ORDER}


def build_scenario_list(count: int, rates: dict[str, float]) -> list[str]:
    """Apportion `count` records across categories via largest-remainder method
    so the totals always sum exactly to `count`, for any count."""
    raw = {cat: rates[cat] * count for cat in CATEGORY_ORDER}
    floor_counts = {cat: math.floor(raw[cat]) for cat in CATEGORY_ORDER}
    remainder = count - sum(floor_counts.values())
    by_fraction = sorted(
        CATEGORY_ORDER,
        key=lambda c: (-(raw[c] - floor_counts[c]), CATEGORY_ORDER.index(c)),
    )
    for cat in by_fraction[:remainder]:
        floor_counts[cat] += 1

    scenario_list: list[str] = []
    for cat in CATEGORY_ORDER:
        scenario_list.extend([cat] * floor_counts[cat])
    return scenario_list


def generate(seed: int, count: int, rates: dict[str, float]):
    rng = random.Random(seed)
    scenario_list = build_scenario_list(count, rates)
    rng.shuffle(scenario_list)

    # All canonical bank IDs are known upfront; used_bank_ids grows to also include
    # corrupted (id_format_drift) IDs as they're assigned, so no corrupted ID ever
    # collides with another transaction's real ID or with a previously-assigned one.
    used_bank_ids = {f"{j:07d}" for j in range(1, count + 1)}

    ledger_rows: list[dict] = []
    bank_rows: list[dict] = []
    gateway_rows: list[dict] = []
    ground_truth: list[dict] = []

    for i in range(1, count + 1):
        scenario = scenario_list[i - 1]
        true_id = f"GT-{i:04d}"
        ledger_id = f"TXN-{i:06d}"
        bank_id = f"{i:07d}"
        gateway_id = f"pay_{i:05d}"

        amount = decimal_amount(rng)
        ledger_date = BASE_DATE + timedelta(days=rng.randint(0, DATE_SPREAD_DAYS))
        name = rng.choice(NAME_POOL)
        invoice_no = f"INV{i:05d}"
        order_no = f"ORD{i:05d}"
        merchant_id = f"MCHT{((i - 1) % 40) + 1:04d}"
        account_fragment = f"{(1000 + (i * 37) % 9000):04d}"

        ledger_rows.append(
            {
                "ledger_id": ledger_id,
                "date": ledger_date.isoformat(),
                "amount": str(amount),
                "narration": ledger_narration(name, invoice_no),
                "counterparty_name": name,
            }
        )

        gt = {
            "true_transaction_id": true_id,
            "ledger_id": ledger_id,
            "bank_ids": [],
            "gateway_ids": [],
            "expected_outcome": "MATCH",
            "mismatch_type": MISMATCH_TYPE_LABEL[scenario],
            "notes": "",
        }

        if scenario == "clean_exact_match":
            bank_date = ledger_date + timedelta(days=BANK_CLEAR_DAYS)
            gateway_date = ledger_date + timedelta(days=GATEWAY_PAYOUT_DAYS)
            bank_rows.append(
                {
                    "bank_id": bank_id,
                    "date": bank_date.isoformat(),
                    "amount": str(amount),
                    "narration": bank_narration(name, invoice_no),
                    "account_fragment": account_fragment,
                }
            )
            gateway_rows.append(
                {
                    "gateway_id": gateway_id,
                    "date": gateway_date.isoformat(),
                    "amount": str(amount),
                    "narration": gateway_narration(merchant_id, order_no),
                    "merchant_id": merchant_id,
                }
            )
            gt["bank_ids"] = [bank_id]
            gt["gateway_ids"] = [gateway_id]
            gt["notes"] = (
                "Clean match: standard T+"
                f"{BANK_CLEAR_DAYS} bank clearing, T+{GATEWAY_PAYOUT_DAYS} gateway payout, "
                "no fee deduction, identical amount across all three sources."
            )

        elif scenario == "id_format_drift":
            corrupted_bank_id = unique_transposed_id(bank_id, used_bank_ids)
            used_bank_ids.add(corrupted_bank_id)
            bank_date = ledger_date + timedelta(days=BANK_CLEAR_DAYS)
            gateway_date = ledger_date + timedelta(days=GATEWAY_PAYOUT_DAYS)
            bank_rows.append(
                {
                    "bank_id": corrupted_bank_id,
                    "date": bank_date.isoformat(),
                    "amount": str(amount),
                    "narration": bank_narration(name, invoice_no),
                    "account_fragment": account_fragment,
                }
            )
            gateway_rows.append(
                {
                    "gateway_id": gateway_id,
                    "date": gateway_date.isoformat(),
                    "amount": str(amount),
                    "narration": gateway_narration(merchant_id, order_no),
                    "merchant_id": merchant_id,
                }
            )
            gt["bank_ids"] = [corrupted_bank_id]
            gt["gateway_ids"] = [gateway_id]
            gt["notes"] = (
                f"Bank reference ID transcribed with digits transposed ({bank_id} -> "
                f"{corrupted_bank_id}); exact-ID matching cannot resolve this, requires "
                "fuzzy match on amount + date + narration."
            )

        elif scenario == "timing_drift":
            bank_date = ledger_date + timedelta(
                days=BANK_CLEAR_DAYS + TIMING_DRIFT_EXTRA_DAYS
            )
            gateway_date = ledger_date + timedelta(days=GATEWAY_PAYOUT_DAYS)
            bank_rows.append(
                {
                    "bank_id": bank_id,
                    "date": bank_date.isoformat(),
                    "amount": str(amount),
                    "narration": bank_narration(name, invoice_no),
                    "account_fragment": account_fragment,
                }
            )
            gateway_rows.append(
                {
                    "gateway_id": gateway_id,
                    "date": gateway_date.isoformat(),
                    "amount": str(amount),
                    "narration": gateway_narration(merchant_id, order_no),
                    "merchant_id": merchant_id,
                }
            )
            gt["bank_ids"] = [bank_id]
            gt["gateway_ids"] = [gateway_id]
            gt["notes"] = (
                f"Bank clearing delayed by an extra {TIMING_DRIFT_EXTRA_DAYS} days beyond the "
                f"standard T+{BANK_CLEAR_DAYS} window (posted "
                f"{BANK_CLEAR_DAYS + TIMING_DRIFT_EXTRA_DAYS} days after the ledger date) — "
                "simulates a clearing delay. Still inside the configured date-match window."
            )

        elif scenario == "fee_adjusted_amount":
            bank_date = ledger_date + timedelta(days=BANK_CLEAR_DAYS)
            gateway_date = ledger_date + timedelta(days=GATEWAY_PAYOUT_DAYS)
            fee = amount * FEE_PCT
            gst_on_fee = fee * GST_ON_FEE_PCT
            gateway_amount = money(amount - fee - gst_on_fee)
            bank_rows.append(
                {
                    "bank_id": bank_id,
                    "date": bank_date.isoformat(),
                    "amount": str(amount),
                    "narration": bank_narration(name, invoice_no),
                    "account_fragment": account_fragment,
                }
            )
            gateway_rows.append(
                {
                    "gateway_id": gateway_id,
                    "date": gateway_date.isoformat(),
                    "amount": str(gateway_amount),
                    "narration": gateway_narration(merchant_id, order_no),
                    "merchant_id": merchant_id,
                }
            )
            gt["bank_ids"] = [bank_id]
            gt["gateway_ids"] = [gateway_id]
            gt["notes"] = (
                f"Gateway amount is ledger amount minus a {FEE_PCT * 100}% processing fee minus "
                f"{GST_ON_FEE_PCT * 100}% GST on that fee (₹{amount} -> ₹{gateway_amount}); "
                "bank statement still shows the full ledger amount."
            )

        elif scenario == "missing_counterpart":
            gt["bank_ids"] = []
            gt["gateway_ids"] = []
            gt["expected_outcome"] = "UNRESOLVED"
            gt["notes"] = (
                "Transaction recorded in the ledger only; genuinely pending settlement — no "
                "bank or gateway record exists yet. Ground truth expects this to remain "
                "unresolved (exception type MISSING_COUNTERPART), never force-matched."
            )

        elif scenario == "duplicate_entry":
            bank_date = ledger_date + timedelta(days=BANK_CLEAR_DAYS)
            gateway_date = ledger_date + timedelta(days=GATEWAY_PAYOUT_DAYS)
            bank_rows.append(
                {
                    "bank_id": bank_id,
                    "date": bank_date.isoformat(),
                    "amount": str(amount),
                    "narration": bank_narration(name, invoice_no),
                    "account_fragment": account_fragment,
                }
            )
            gateway_row = {
                "gateway_id": gateway_id,
                "date": gateway_date.isoformat(),
                "amount": str(amount),
                "narration": gateway_narration(merchant_id, order_no),
                "merchant_id": merchant_id,
            }
            gateway_rows.append(dict(gateway_row))
            gateway_rows.append(dict(gateway_row))
            gt["bank_ids"] = [bank_id]
            gt["gateway_ids"] = [gateway_id]
            gt["notes"] = (
                f"Gateway settlement row for {gateway_id} appears twice in "
                "gateway_settlement.csv (duplicate webhook retry of the same payout); both "
                "raw rows correspond to this single true transaction."
            )

        elif scenario == "split_settlement":
            bank_date = ledger_date + timedelta(days=BANK_CLEAR_DAYS)
            gateway_date = ledger_date + timedelta(days=GATEWAY_PAYOUT_DAYS)
            part_a = money(amount * SPLIT_RATIO_A)
            part_b = amount - part_a
            gateway_id_a = gateway_id + "A"
            gateway_id_b = gateway_id + "B"
            bank_rows.append(
                {
                    "bank_id": bank_id,
                    "date": bank_date.isoformat(),
                    "amount": str(amount),
                    "narration": bank_narration(name, invoice_no),
                    "account_fragment": account_fragment,
                }
            )
            gateway_rows.append(
                {
                    "gateway_id": gateway_id_a,
                    "date": gateway_date.isoformat(),
                    "amount": str(part_a),
                    "narration": gateway_narration(merchant_id, order_no),
                    "merchant_id": merchant_id,
                }
            )
            gateway_rows.append(
                {
                    "gateway_id": gateway_id_b,
                    "date": gateway_date.isoformat(),
                    "amount": str(part_b),
                    "narration": gateway_narration(merchant_id, order_no),
                    "merchant_id": merchant_id,
                }
            )
            gt["bank_ids"] = [bank_id]
            gt["gateway_ids"] = [gateway_id_a, gateway_id_b]
            split_pct_a = int(SPLIT_RATIO_A * 100)
            gt["notes"] = (
                f"Ledger invoice settled via two gateway records ({gateway_id_a}: ₹{part_a}, "
                f"{gateway_id_b}: ₹{part_b}) summing exactly to the ledger amount ₹{amount} — a "
                f"{split_pct_a}/{100 - split_pct_a} split settlement."
            )

        else:  # pragma: no cover - CATEGORY_ORDER is exhaustive
            raise AssertionError(f"unhandled scenario: {scenario}")

        ground_truth.append(gt)

    bank_rows.sort(key=lambda r: (r["date"], r["bank_id"]))
    gateway_rows.sort(key=lambda r: (r["date"], r["gateway_id"]))

    return (
        ledger_rows,
        bank_rows,
        gateway_rows,
        ground_truth,
        floor_scenario_counts(scenario_list),
    )


def floor_scenario_counts(scenario_list: list[str]) -> dict[str, int]:
    counts = {cat: 0 for cat in CATEGORY_ORDER}
    for scenario in scenario_list:
        counts[scenario] += 1
    return counts


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument(
        "--config", type=Path, default=REPO_ROOT / "config" / "matching_config.yaml"
    )
    parser.add_argument("--raw-dir", type=Path, default=REPO_ROOT / "data" / "raw")
    parser.add_argument(
        "--gt-dir", type=Path, default=REPO_ROOT / "data" / "ground_truth"
    )
    args = parser.parse_args()

    rates = load_injection_rates(args.config)
    ledger_rows, bank_rows, gateway_rows, ground_truth, counts = generate(
        args.seed, args.count, rates
    )

    args.raw_dir.mkdir(parents=True, exist_ok=True)
    args.gt_dir.mkdir(parents=True, exist_ok=True)

    write_csv(
        args.raw_dir / "ledger.csv",
        ledger_rows,
        ["ledger_id", "date", "amount", "narration", "counterparty_name"],
    )
    write_csv(
        args.raw_dir / "bank_statement.csv",
        bank_rows,
        ["bank_id", "date", "amount", "narration", "account_fragment"],
    )
    write_csv(
        args.raw_dir / "gateway_settlement.csv",
        gateway_rows,
        ["gateway_id", "date", "amount", "narration", "merchant_id"],
    )

    with (args.gt_dir / "ground_truth_matches.json").open("w") as f:
        json.dump(ground_truth, f, indent=2)
        f.write("\n")

    print(f"Generated {args.count} true transactions (seed={args.seed}):")
    for cat in CATEGORY_ORDER:
        print(f"  {cat:22s} {counts[cat]:4d}")
    print(f"  ledger rows:           {len(ledger_rows):4d}")
    print(f"  bank rows:             {len(bank_rows):4d}")
    print(f"  gateway rows:          {len(gateway_rows):4d}")


if __name__ == "__main__":
    main()
