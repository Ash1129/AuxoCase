# Evaluation report

Generated 2026-10-28T17:01:10 UTC. Live model: `demo-v1`.

All precision figures are computed on leads an SDR actually worked, because unworked leads have no label. They are biased by the SDRs' past choices until the exploration slice has run for a few months (knowledge base L05).

## Head to head at the capacity line

K = 500 (one month of SDR capacity), 5230 labelled leads, 2159 demos.

| Ranker | Precision@K |
|---|---|
| AuxoCase live model | **0.76** |
| Points table (channel-aware, cold start) | 0.742 |
| HubSpot's current points (title, size, pages) | 0.398 |
| Random (base rate) | 0.4128 |

## Validation window (time-ordered)

Months 2026-07, 2026-08: precision@305 0.518 vs HubSpot's points 0.4164 (lift 1.244x) and the service's cold-start table 0.5279 (lift 0.981x); AUC-PR 0.5319 against a base rate of 0.346; AUC-ROC 0.6728; Brier 0.2055; ECE 0.0156. Challenger: {'family': 'hgb', 'precision_at_k': 0.4951, 'ece': 0.0197, 'auc_pr': 0.495}.

| Predicted | n | Observed |
|---|---|---|
| 0.0–0.1 | 13 | 0.0 |
| 0.1–0.2 | 123 | 0.179 |
| 0.2–0.3 | 268 | 0.269 |
| 0.3–0.4 | 314 | 0.344 |
| 0.4–0.5 | 148 | 0.426 |
| 0.5–0.6 | 56 | 0.464 |
| 0.6–0.7 | 49 | 0.673 |
| 0.7–0.8 | 23 | 0.739 |
| 0.8–0.9 | 6 | 0.833 |

## By channel (all labelled history)

| Channel | n | Observed demo rate | Predicted |
|---|---|---|---|
| email | 399 | 0.273 | 0.277 |
| events | 232 | 0.405 | 0.387 |
| paid_ads | 619 | 0.292 | 0.283 |
| referral | 913 | 0.722 | 0.697 |
| webinar | 426 | 0.563 | 0.551 |
| website | 2641 | 0.332 | 0.321 |

## Ranked slice vs exploration slice

| Slice | Assigned | Worked | Booked |
|---|---|---|---|
| assigned | 1491 | 994 | 717 |
| holdout_assigned | 129 | 86 | 22 |

## SDR overrides

| Action | Count | Booked |
|---|---|---|
| skipped | 1 | 0 |

## Speed to lead

1620 assigned leads touched; median 1148.5 h to first touch; 33% within 24 h.

## Channel monitor (latest run)

| Period | Channel | Worked | Demos | Observed | Predicted | Flag |
|---|---|---|---|---|---|---|
| 2026-11 | events | 2 | 1 | 0.500 | 0.593 | held |
| 2026-11 | referral | 110 | 84 | 0.764 | 0.693 | ok |
| 2026-11 | webinar | 30 | 24 | 0.800 | 0.639 | ok |
| 2026-11 | website | 18 | 11 | 0.611 | 0.561 | held |
| 2026-12 | email | 1 | 1 | 1.000 | 0.649 | held |
| 2026-12 | events | 4 | 3 | 0.750 | 0.667 | held |
| 2026-12 | referral | 111 | 77 | 0.694 | 0.695 | ok |
| 2026-12 | webinar | 26 | 13 | 0.500 | 0.619 | held |
| 2026-12 | website | 19 | 13 | 0.684 | 0.563 | held |
