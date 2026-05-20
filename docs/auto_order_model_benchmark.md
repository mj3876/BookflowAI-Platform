# BOOKFLOW Auto-Order Model Benchmark

Last updated: 2026-05-20

## Scope

This benchmark is for AWS `decision-svc` automatic ordering using GCP
`forecast_results` without changing the BigQuery/RDS/dashboard schema.

The model must not write inventory or orders directly. It only provides demand
forecasts. `decision-svc` owns order decisions, and `inventory-svc` remains the
only inventory write gateway.

## Forecast Contract

The existing `forecast_results` schema stays unchanged.

| Column | Auto-order meaning |
|---|---|
| `predicted_demand` | Calibrated demand for auto-order decisioning |
| `confidence_low` | Base demand proxy before auto-order calibration |
| `confidence_high` | Upper risk bound for overstock checks |
| `model_version` | Approved champion model identifier |
| `inference_ms` | Batch/online inference runtime signal |

## External Benchmark Assumptions

Large booksellers do not publish store-SKU-day replenishment model metrics.
BOOKFLOW therefore uses a conservative inferred benchmark based on common retail
demand-planning practice:

| Level | Intended use | WAPE | Fill-rate proxy | Underforecast rate | Bias policy |
|---|---:|---:|---:|---:|---|
| L0 | Demo / visual dashboard | <= 0.70 | >= 0.80 | <= 0.50 | Any stable bias |
| L1 | Manager approval recommendation | <= 0.55 | >= 0.85 | <= 0.45 | -5% to +20% |
| L2 | Guarded auto-order for high-demand items | <= 0.45 | >= 0.92 | <= 0.25 | 0% to +20% |
| L3 | Broad auto-order | <= 0.35 | >= 0.95 | <= 0.15 | 0% to +12% |
| L4 | Mature large-retailer target | <= 0.25 | >= 0.97 | <= 0.10 | 0% to +8% |

`L2` is the minimum for automatic ordering without human approval. `L3/L4` are
aspirational for broader automation.

## Current Champion Candidate

Model:
`store-demand-autoorder-lightgbm-l2-20260520-v1`

Holdout:
`2026-05-01` through `2026-05-14`

| Segment | WAPE | Fill-rate proxy | Underforecast rate | Weighted order cost rate | Status |
|---|---:|---:|---:|---:|---|
| all | 0.1379 | 0.9777 | 0.2257 | 0.1826 | L2 pass |
| high | 0.1385 | 0.9777 | 0.2320 | 0.1831 | L2 pass |
| medium | 0.0624 | 0.9697 | 0.0484 | 0.1231 | L2 pass |

## Go / No-Go Decision

Current model passes the BOOKFLOW `L2` guarded auto-order benchmark on the
enhanced simulation holdout dataset.

Allowed:
- Dashboard recommendations.
- Manager approval workflow.
- Shadow-mode auto-order simulation.
- Guarded approval-free auto-order dry-run in AWS `decision-svc`.

Blocked:
- Direct order or inventory writes from GCP.
- Production approval-free execution until AWS `decision-svc` inventory, budget,
  reorder, and overstock caps are verified in shadow mode.

## Required Improvements Before Approval-Free Auto-Order

The model must maintain `L2` on at least two rolling holdout windows before
production approval-free execution:

| Requirement | Target |
|---|---:|
| High-segment WAPE | <= 0.45 |
| High-segment fill-rate proxy | >= 0.92 |
| High-segment underforecast rate | <= 0.25 |
| Positive bias cap | <= +20% |
| Weighted order cost rate | <= 0.75 |

Recommended path:

1. Keep the enhanced simulator causal structure in version control.
2. Backtest through an inventory/order simulator, not only forecast error.
3. Run AWS `decision-svc` shadow-mode auto-order for the approved model version.
4. Promote only if the champion passes `L2` for at least two rolling holdout
   windows.
