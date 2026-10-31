# Evaluation report

Generated 2026-10-31T21:43:14 UTC. Live model: `demo-v1`.

## The win BrightPath asked for: more demos, faster follow-up, same 12 SDRs

Month of first touch. Before: the history, SDRs choosing by gut feel and HubSpot's score. After: leads this service ranked and routed.

| Month | Routed | Worked | Demos booked | Demo rate | Median hours to first touch | Touched within 24 h |
|---|---|---|---|---|---|---|
| 2025-07 | no | 463 | 157 | 0.34 | 62 | 22% |
| 2025-08 | no | 507 | 174 | 0.34 | 66 | 24% |
| 2025-09 | no | 497 | 165 | 0.33 | 75 | 19% |
| 2025-10 | no | 499 | 160 | 0.32 | 69 | 22% |
| 2025-11 | no | 494 | 170 | 0.34 | 69 | 23% |
| 2025-12 | no | 506 | 176 | 0.35 | 75 | 21% |
| 2026-01 | no | 500 | 180 | 0.36 | 73 | 21% |
| 2026-02 | no | 496 | 185 | 0.37 | 66 | 23% |
| 2026-03 | no | 491 | 164 | 0.33 | 62 | 22% |
| 2026-04 | no | 511 | 178 | 0.35 | 72 | 20% |
| 2026-05 | no | 493 | 171 | 0.35 | 74 | 21% |
| 2026-06 | no | 506 | 186 | 0.37 | 83 | 19% |
| 2026-08 | yes | 498 | 306 | 0.61 | 18 | 64% |
| 2026-09 | yes | 491 | 304 | 0.62 | 17 | 69% |
| 2026-10 | yes | 512 | 265 | 0.52 | 19 | 62% |
| 2026-11 | yes | 1111 | 622 | 0.56 | 8 | 89% |
| 2026-12 | yes | 1155 | 669 | 0.58 | 8 | 89% |
| 2027-01 | yes | 1100 | 629 | 0.57 | 8 | 90% |

Average per month: 172 demos from 497 leads worked before, 466 from 811 after (+171% demos at the same capacity). On the mock, the speed effect is the generator's assumption from BrightPath's answer 4 (a lead touched within a day books about twice as often); on the real portal it is measured.

All precision figures are computed on leads an SDR actually worked, because unworked leads have no label. They are biased by the SDRs' past choices until the exploration slice has run for a few months (knowledge base L05).

## The live model's test months

Months 2026-05, 2026-06 (a model from before the three-way split: it was refitted on them, so these are not held out). Precision@299 0.4482 vs HubSpot's points 0.3746 (lift 1.196x) and the service's cold-start table 0.4448 (lift 1.008x); AUC-PR 0.4456 against a base rate of 0.358; AUC-ROC 0.5945; Brier 0.2233; ECE 0.0068. Challenger: {'family': 'hgb', 'precision_at_k': 0.4615, 'ece': 0.0301, 'auc_pr': 0.4336}.

| Predicted | n | Observed |
|---|---|---|
| 0.0–0.1 | 4 | 0.0 |
| 0.1–0.2 | 13 | 0.154 |
| 0.2–0.3 | 205 | 0.273 |
| 0.3–0.4 | 501 | 0.349 |
| 0.4–0.5 | 231 | 0.429 |
| 0.5–0.6 | 43 | 0.558 |
| 0.6–0.7 | 3 | 0.667 |

## Below HubSpot's MQL gate: what the exploration slice has learned

Exploration share drawn below the gate: 50%; ranking below the gate: off. 135 plain leads handed out at random, 135 worked, 116 with a matured label (32 booked); 0 more handed out by rank. The model's score at hand-out averaged 0.285 for the matured ones; 0.276 booked. Enough labels to judge ranking below the gate.

## Cost against outcome: what a demo costs by the way the lead was reached

Unit costs are settings, not facts: an SDR hour 45 dollars, a cold touch 20 minutes, answering a reply 4 minutes, a Claude draft 0.02 dollars. A demo is worth 4,800 dollars in first-year contract value (the brief: 24,000 at a 20% close rate; the outcomes show 23% on 3434 demos old enough to have closed).

| Route | Leads reached | Demos | Demo rate | SDR hours | Cost | Cost per demo | Demos per SDR hour | Value |
|---|---|---|---|---|---|---|---|---|
| An SDR's touch on a ranked lead | 4867 | 2795 | 0.574 | 1622.3 | 73,005 | 26 | 1.720 | 13,416,000 |
| The automated sequence (phase 3) | 0 | 0 | n/a | 0.0 | 0 | n/a | n/a | 0 |

## Ranked slice vs exploration slice

| Slice | Assigned | Worked | Booked |
|---|---|---|---|
| assigned | 4518 | 4497 | 2711 |
| holdout_assigned | 235 | 235 | 48 |
| holdout_assigned (below HubSpot's gate) | 135 | 135 | 36 |

## SDR overrides

| Action | Count | Booked |
|---|---|---|
| demote | 1 | 1 |
| junk | 7 | 0 |
| promote | 4 | 4 |
| skipped | 21 | 1 |
| worked | 30 | 2 |

## Speed to lead (lead created to first touch)

| Leads | Touched | Median hours | Within 24 h |
|---|---|---|---|
| Not routed by AuxoCase (history) | 6043 | 70.4 | 21% |
| Routed by AuxoCase | 4862 | 9.8 | 82% |

Measured from outcomes: leads first touched within a day booked at 0.621, later ones at 0.286 (factor 0.46; 5259 and 5652 leads). Routing discounts a lead past its first day by `stale_lead_factor` = 0.5; keep the two close.

## Where HubSpot's MQL gate drops leads (last three months)

Only MQLs reach the pool this service ranks. Leads missing a title or a size get no points for them in HubSpot's model, so they pass the gate less often.

| Channel | Leads | Reach MQL | No job title | No company size |
|---|---|---|---|---|
| website | 6383 | 49% | 11% | 15% |
| paid_ads | 3624 | 24% | 16% | 18% |
| webinar | 2729 | 8% | 47% | 48% |
| email | 1835 | 24% | 10% | 16% |
| events | 1822 | 17% | 20% | 22% |
| referral | 1807 | 10% | 49% | 48% |

## Enrichment

Asked the enrichment tool about 748 pool leads missing a field; it filled at least one field for 539. Titles: 61 of 104. Sizes: 483 of 657.

## Why deals were lost (free-text reasons read into categories, quote checked)

| Reason | under 50 | 50 149 | 150 299 | 300 500 | over 500 | unknown | Total |
|---|---|---|---|---|---|---|---|
| price | 86 | 643 | 415 | 266 | 21 | 28 | 1459 |
| competitor | 54 | 564 | 402 | 230 | 28 | 31 | 1309 |
| no decision | 35 | 502 | 302 | 198 | 27 | 30 | 1094 |
| timing | 37 | 495 | 319 | 177 | 37 | 23 | 1088 |
| missing feature | 21 | 371 | 242 | 129 | 57 | 19 | 839 |
| other | 25 | 226 | 148 | 80 | 12 | 12 | 503 |
| poor fit | 35 | 179 | 111 | 73 | 11 | 15 | 424 |

## Channel monitor (latest run)

| Period | Channel | Worked | Demos | Observed | Predicted | Flag |
|---|---|---|---|---|---|---|
| 2026-10 | email | 1 | 0 | 0.000 | 0.263 | held |
| 2026-10 | events | 2 | 1 | 0.500 | 0.394 | held |
| 2026-10 | paid_ads | 4 | 0 | 0.000 | 0.383 | held |
| 2026-10 | referral | 1 | 0 | 0.000 | 0.357 | held |
| 2026-10 | webinar | 6 | 4 | 0.667 | 0.420 | held |
| 2026-10 | website | 18 | 6 | 0.333 | 0.409 | held |
| 2026-11 | email | 109 | 52 | 0.477 | 0.358 | ok |
| 2026-11 | events | 98 | 59 | 0.602 | 0.400 | above |
| 2026-11 | paid_ads | 160 | 84 | 0.525 | 0.338 | above |
| 2026-11 | referral | 64 | 56 | 0.875 | 0.487 | above |
| 2026-11 | webinar | 79 | 50 | 0.633 | 0.473 | ok |
| 2026-11 | website | 600 | 326 | 0.543 | 0.367 | above |
| 2026-12 | email | 100 | 56 | 0.560 | 0.354 | above |
| 2026-12 | events | 90 | 50 | 0.556 | 0.400 | above |
| 2026-12 | paid_ads | 179 | 94 | 0.525 | 0.333 | above |
| 2026-12 | referral | 88 | 80 | 0.909 | 0.487 | above |
| 2026-12 | webinar | 66 | 48 | 0.727 | 0.454 | above |
| 2026-12 | website | 634 | 347 | 0.547 | 0.369 | above |
| 2027-01 | email | 82 | 44 | 0.537 | 0.357 | above |
| 2027-01 | events | 86 | 44 | 0.512 | 0.398 | ok |
| 2027-01 | paid_ads | 145 | 71 | 0.490 | 0.340 | above |
| 2027-01 | referral | 48 | 38 | 0.792 | 0.489 | above |
| 2027-01 | webinar | 75 | 62 | 0.827 | 0.480 | above |
| 2027-01 | website | 605 | 347 | 0.574 | 0.371 | above |
