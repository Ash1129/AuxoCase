# Evaluation report

Generated 2026-10-29T03:40:26 UTC. Live model: `demo-v1`.

## The win BrightPath asked for: more demos, faster follow-up, same 12 SDRs

Month of first touch. Before: the history, SDRs choosing by gut feel and HubSpot's score. After: leads this service ranked and routed.

| Month | Routed | Worked | Demos booked | Demo rate | Median hours to first touch | Touched within 24 h |
|---|---|---|---|---|---|---|
| 2027-07 | no | 463 | 157 | 0.34 | 62 | 22% |
| 2027-08 | no | 507 | 174 | 0.34 | 66 | 24% |
| 2027-09 | no | 497 | 165 | 0.33 | 75 | 19% |
| 2027-10 | no | 499 | 160 | 0.32 | 69 | 22% |
| 2027-11 | no | 494 | 170 | 0.34 | 69 | 23% |
| 2027-12 | no | 506 | 176 | 0.35 | 75 | 21% |
| 2028-01 | no | 500 | 180 | 0.36 | 73 | 21% |
| 2028-02 | no | 496 | 185 | 0.37 | 67 | 22% |
| 2028-03 | no | 491 | 164 | 0.33 | 68 | 22% |
| 2028-04 | no | 511 | 178 | 0.35 | 72 | 20% |
| 2028-05 | no | 493 | 171 | 0.35 | 74 | 21% |
| 2028-06 | no | 506 | 186 | 0.37 | 83 | 19% |
| 2028-08 | yes | 505 | 282 | 0.56 | 17 | 63% |
| 2028-09 | yes | 493 | 268 | 0.54 | 18 | 64% |
| 2028-10 | yes | 481 | 271 | 0.56 | 17 | 65% |

Average per month: 172 demos from 497 leads worked before, 274 from 493 after (+59% demos at the same capacity). On the mock, the speed effect is the generator's assumption from BrightPath's answer 4 (a lead touched within a day books about twice as often); on the real portal it is measured.

All precision figures are computed on leads an SDR actually worked, because unworked leads have no label. They are biased by the SDRs' past choices until the exploration slice has run for a few months (knowledge base L05).

## Head to head on worked leads, in the test months

The live model's test months (2028-05, 2028-06): it never trained or calibrated on them. K = 333, a third of the 1000 worked leads there (358 demos), as capacity is a third of the MQLs.

| Ranker | Precision@K |
|---|---|
| AuxoCase live model | **0.4444** |
| Points table (channel-aware, cold start) | 0.4474 |
| HubSpot's current points (title, size, pages) | 0.3694 |
| Random (base rate) | 0.358 |

## Capacity head to head on held-out months, on the generator's truth (mock only)

For each of 6 months the live model never trained on (2028-05, 2028-06, 2028-07, 2028-08, 2028-09, 2028-10), the 500 leads each ranker would hand the SDRs, and the demos they would book if worked promptly: the sum of the generator's hidden demo chances, worked or not. Every ranker gets the same timing, so this is the ranking alone; faster follow-up comes on top. Ranges resample the months (2.5th to 97.5th percentile) and say how steady the gap is on this generator, not how sure a real portal would be.

| Who gets the 500 | Demos a month |
|---|---|
| HubSpot's points, top of the MQLs | 292.2 |
| Points table, top of the MQLs | 345.0 |
| AuxoCase model, top of the MQLs (today) | **347.6** |
| Random MQLs | 262.1 |
| Best possible among the MQLs | 375.8 |
| AuxoCase model, top of every lead (below the gate too) | **395.7** |
| Best possible among every lead | 440.4 |

The model over HubSpot's points: +55.4 demos a month (+53.4 to +58.0). Ranking every lead over ranking the MQLs only: +48.2 (+44.9 to +51.1); about 358 of the 500 would be leads HubSpot's gate kept out (referral 166, webinar 166, events 13, website 8, email 4, paid_ads 1). The model learned from worked MQLs only, so how it ranks below the gate is a guess the exploration slice must check before it is used.

| Month | HubSpot | Model (MQLs) | Model (every lead) | Best (every lead) |
|---|---|---|---|---|
| 2028-05 | 290 | 343 | 395 | 440 |
| 2028-06 | 292 | 347 | 395 | 440 |
| 2028-07 | 294 | 350 | 400 | 440 |
| 2028-08 | 289 | 351 | 393 | 443 |
| 2028-09 | 296 | 350 | 394 | 441 |
| 2028-10 | 293 | 345 | 398 | 439 |

## The live model's test months

Trained on 2027-08 to 2028-02 (3404 worked leads), family chosen and calibrated on 2028-03, 2028-04 (1000), tested once on 2028-05, 2028-06 (1000). Precision@299 0.4482 vs HubSpot's points 0.3746 (lift 1.196x) and the service's cold-start table 0.4448 (lift 1.008x); AUC-PR 0.4481 against a base rate of 0.358; AUC-ROC 0.5953; Brier 0.2233; ECE 0.0118. Challenger: {'family': 'hgb', 'precision_at_k': 0.4381, 'ece': 0.0268, 'auc_pr': 0.4286, 'chosen_on': 'calibration months', 'calibration_precision_at_k': {'lr': 0.4561, 'hgb': 0.4358}}.

| Predicted | n | Observed |
|---|---|---|
| 0.0–0.1 | 4 | 0.0 |
| 0.1–0.2 | 41 | 0.171 |
| 0.2–0.3 | 260 | 0.288 |
| 0.3–0.4 | 435 | 0.352 |
| 0.4–0.5 | 203 | 0.443 |
| 0.5–0.6 | 50 | 0.58 |
| 0.6–0.7 | 7 | 0.571 |

## By channel (test months, worked leads)

| Channel | n | Observed demo rate | Predicted |
|---|---|---|---|
| email | 62 | 0.21 | 0.351 |
| events | 61 | 0.475 | 0.4 |
| paid_ads | 158 | 0.342 | 0.296 |
| referral | 37 | 0.514 | 0.509 |
| webinar | 30 | 0.6 | 0.481 |
| website | 652 | 0.345 | 0.339 |

## Ranked slice vs exploration slice

| Slice | Assigned | Worked | Booked |
|---|---|---|---|
| assigned | 1369 | 1361 | 801 |
| holdout_assigned | 118 | 118 | 20 |

## SDR overrides

| Action | Count | Booked |
|---|---|---|
| skipped | 17 | 0 |

## Speed to lead (lead created to first touch)

| Leads | Touched | Median hours | Within 24 h |
|---|---|---|---|
| Not routed by AuxoCase (history) | 6044 | 70.8 | 21% |
| Routed by AuxoCase | 1479 | 17.1 | 64% |

Measured from outcomes: leads first touched within a day booked at 0.62, later ones at 0.289 (factor 0.47; 2229 and 5294 leads). Routing discounts a lead past its first day by `stale_lead_factor` = 0.5; keep the two close.

## Where HubSpot's MQL gate drops leads (last three months)

Only MQLs reach the pool this service ranks. Leads missing a title or a size get no points for them in HubSpot's model, so they pass the gate less often.

| Channel | Leads | Reach MQL | No job title | No company size |
|---|---|---|---|---|
| website | 6457 | 49% | 12% | 16% |
| paid_ads | 3655 | 24% | 15% | 18% |
| webinar | 2730 | 8% | 47% | 47% |
| events | 1932 | 16% | 20% | 24% |
| referral | 1924 | 9% | 49% | 49% |
| email | 1844 | 22% | 9% | 16% |

## Enrichment

Asked the enrichment tool about 394 pool leads missing a field; it filled at least one field for 280. Titles: 22 of 37. Sizes: 258 of 357.

## Why deals were lost (free-text reasons read into categories, quote checked)

| Reason | under 50 | 50 149 | 150 299 | 300 500 | over 500 | unknown | Total |
|---|---|---|---|---|---|---|---|
| price | 63 | 486 | 328 | 198 | 16 | 26 | 1117 |
| competitor | 47 | 416 | 305 | 184 | 29 | 25 | 1006 |
| no decision | 25 | 402 | 246 | 132 | 25 | 27 | 857 |
| timing | 33 | 375 | 245 | 126 | 25 | 17 | 821 |
| missing feature | 18 | 272 | 174 | 111 | 47 | 15 | 637 |
| other | 20 | 176 | 117 | 69 | 8 | 11 | 401 |
| poor fit | 29 | 146 | 80 | 57 | 9 | 14 | 335 |

## Channel monitor (latest run)

| Period | Channel | Worked | Demos | Observed | Predicted | Flag |
|---|---|---|---|---|---|---|
| 2028-07 | paid_ads | 2 | 0 | 0.000 | 0.366 | held |
| 2028-07 | referral | 2 | 0 | 0.000 | 0.476 | held |
| 2028-07 | website | 11 | 5 | 0.455 | 0.364 | held |
| 2028-08 | email | 66 | 43 | 0.652 | 0.416 | above |
| 2028-08 | events | 62 | 34 | 0.548 | 0.434 | ok |
| 2028-08 | paid_ads | 31 | 12 | 0.387 | 0.354 | ok |
| 2028-08 | referral | 50 | 36 | 0.720 | 0.532 | ok |
| 2028-08 | webinar | 62 | 41 | 0.661 | 0.501 | ok |
| 2028-08 | website | 236 | 120 | 0.508 | 0.409 | above |
| 2028-09 | email | 45 | 21 | 0.467 | 0.422 | ok |
| 2028-09 | events | 58 | 31 | 0.534 | 0.437 | ok |
| 2028-09 | paid_ads | 23 | 10 | 0.435 | 0.337 | held |
| 2028-09 | referral | 65 | 48 | 0.738 | 0.520 | above |
| 2028-09 | webinar | 56 | 37 | 0.661 | 0.493 | ok |
| 2028-09 | website | 239 | 116 | 0.485 | 0.412 | ok |
| 2028-10 | email | 50 | 28 | 0.560 | 0.418 | ok |
| 2028-10 | events | 56 | 36 | 0.643 | 0.437 | above |
| 2028-10 | paid_ads | 13 | 5 | 0.385 | 0.377 | held |
| 2028-10 | referral | 45 | 36 | 0.800 | 0.524 | above |
| 2028-10 | webinar | 71 | 41 | 0.577 | 0.507 | ok |
| 2028-10 | website | 218 | 118 | 0.541 | 0.406 | above |
