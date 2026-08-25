# NHbot — Equalized Rate Methodology, Validated & Locked

**Date:** 2026-08-25  **Calibration year:** 2024 (DRA official published) → applied to 2025 (estimate).

## The decision

**DRA publishes the authoritative equalized rate itself.** The annual *Comparison of Full Value Tax Rates* PDF gives, per municipality, the official full-value tax rate **and** the total equalized valuation (incl. utilities + railroad). We parsed it cleanly for 242 municipalities. So the canonical rule is:

1. **For any year DRA has published** → ingest DRA's official full-value rate directly. No computation, no estimation error. (`nh_2024_equalized_rates_DRA_official.csv` is the 2024 authoritative dataset.)
2. **For the current year only, before DRA publishes** (right now, 2025) → publish `total_rate × equalization_ratio` as an **estimate, explicitly labeled**, and swap in DRA's official figure when the comparison drops.

This reframes the earlier "which formula is correct" question: we don't need our formula to *be* DRA's — for published years we just use DRA's number. The estimator only has to be good enough to hold the current year until DRA catches up.

## DRA's actual definition (from the PDF, verbatim)

> full value tax rate = 2024 gross local property taxes to be raised ÷ total equalized valuation **including utility values and equalized railroad taxes**, × 1000.

Note the two things that make it *not* equal to either of our computed methods: **gross** taxes (before veterans' credits), and a denominator that includes utilities/railroad at equalized value rather than scaling the whole town by one ratio.

## How the estimators did vs DRA's 240 published rates

| method | bias (signed) | mean \|err\| | median \|err\| | max \|err\| | within 0.10 | within 0.25 |
|---|---|---|---|---|---|---|
| `simple` = total_rate × ratio | **+0.131** | 0.151 | 0.085 | 2.24 | 148/240 | 206/240 |
| `rigorous` = net_commit / equalized_val | +0.044 | 0.173 | 0.080 | 2.33 | 149/240 | 205/240 |

Read: both track DRA to a median of ~0.08 and land inside 0.25 for ~86% of towns. **`simple` carries a systematic upward bias of +0.13** (it ignores veterans' credits and scales utilities by the town ratio). `rigorous` removes most of the bias but is no tighter overall. For a labeled current-year estimate, **`simple` is the pragmatic choice** — one input (advertised rate) times one input (ratio), both easy to source — with the +0.13 bias documented. If we want the bias gone, subtract ~0.13 or use `rigorous`; if we want it *right*, ingest the DRA equalized valuation and compute the exact formula (see next steps).

Where the estimators break: utility/dam-heavy and revaluation-year towns — Errol, Monroe, Peterborough, Bow. There the single-ratio assumption fails because utilities are already assessed near 100% and shouldn't be grossed up. `rigorous` rescues some (Monroe: DRA 8.07, simple 8.87, rigorous 8.01) but not all.

## Data-quality findings (these matter more than the formula)

- **Ratios must be cross-validated across sources.** The ten-year ratio-history file disagrees with the comparison PDF for **Errol** (history 60.4 vs DRA 85.6 — a swap with adjacent Erving's Grant, which carries the 60.4 default for unincorporated places). One bad ratio throws the estimate off by >2 points. Rule for the pipeline: reconcile the equalization ratio across the ratio-history file and the comparison PDF; flag any mismatch > ~1 point.
- **DRA's published ratio matched the history file for 239/240 towns** — so the history file is a fine ratio source *once cross-checked*.
- Column layout in the 2024 tax-rate workbook is byte-identical to 2025 — the positional parser ported with zero changes. Still re-verify positions each new vintage.

## Files

- `phase0/nh_2024_equalized_rates_DRA_official.csv` — **authoritative** 2024 equalized rates + equalized valuation, straight from DRA (242 munis).
- `phase0/validation_2024_method_comparison.csv` — per-town DRA-official vs simple vs rigorous, with errors and ratio-source mismatch flag (sorted worst-first).
- `phase0/validate_2024.py` — reproducible validation (needs `openpyxl`, `pdfplumber`).

## Locked next steps

1. **Ingest DRA official full-value rates for all published years** (the comparison PDF exists back many years) — this is the real equalized-rate column for the dataset, and it comes with equalized valuation for free.
2. **Relabel the 2025 `equalized_rate_simple` column as an estimate** in the Phase 0 output, note the +0.13 bias, and set a reminder to replace it when DRA publishes the 2025 comparison.
3. **Optional precision upgrade for the current year:** ingest the current-year *equalized valuation (incl. utilities + railroad)* from the equalization reports and compute DRA's exact formula ourselves — turns the current-year number from "estimate ±0.15" into "effectively official."
