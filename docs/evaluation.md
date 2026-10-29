# Evaluation report

Generated 2026-10-29T01:02:13 UTC. Live model: `demo-v1`.

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
| 2028-08 | yes | 505 | 280 | 0.55 | 15 | 66% |
| 2028-09 | yes | 493 | 284 | 0.58 | 17 | 66% |
| 2028-10 | yes | 481 | 285 | 0.59 | 16 | 66% |

Average per month: 172 demos from 497 leads worked before, 283 from 493 after (+64% demos at the same capacity). On the mock, the speed effect is the generator's assumption from BrightPath's answer 4 (a lead touched within a day books about twice as often); on the real portal it is measured.

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
| assigned | 1388 | 1381 | 827 |
| holdout_assigned | 99 | 98 | 22 |

## SDR overrides

| Action | Count | Booked |
|---|---|---|
| skipped | 13 | 0 |

## Speed to lead (lead created to first touch)

| Leads | Touched | Median hours | Within 24 h |
|---|---|---|---|
| Not routed by AuxoCase (history) | 6044 | 70.8 | 21% |
| Routed by AuxoCase | 1479 | 16.2 | 66% |

Measured from outcomes: leads first touched within a day booked at 0.63, later ones at 0.288 (factor 0.46; 2254 and 5269 leads). Routing discounts a lead past its first day by `stale_lead_factor` = 0.5; keep the two close.

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
| price | 64 | 498 | 322 | 193 | 16 | 28 | 1121 |
| competitor | 44 | 421 | 311 | 177 | 30 | 25 | 1008 |
| no decision | 27 | 391 | 239 | 142 | 27 | 27 | 853 |
| timing | 35 | 379 | 245 | 130 | 25 | 16 | 830 |
| missing feature | 20 | 272 | 189 | 104 | 45 | 13 | 643 |
| other | 22 | 181 | 116 | 73 | 8 | 13 | 413 |
| poor fit | 30 | 145 | 74 | 55 | 10 | 15 | 329 |

## Channel monitor (latest run)

| Period | Channel | Worked | Demos | Observed | Predicted | Flag |
|---|---|---|---|---|---|---|
| 2028-07 | events | 1 | 0 | 0.000 | 0.369 | held |
| 2028-07 | paid_ads | 3 | 1 | 0.333 | 0.276 | held |
| 2028-07 | referral | 2 | 0 | 0.000 | 0.476 | held |
| 2028-07 | website | 13 | 6 | 0.462 | 0.322 | held |
| 2028-08 | email | 61 | 30 | 0.492 | 0.429 | ok |
| 2028-08 | events | 62 | 33 | 0.532 | 0.435 | ok |
| 2028-08 | paid_ads | 31 | 13 | 0.419 | 0.349 | ok |
| 2028-08 | referral | 50 | 40 | 0.800 | 0.532 | above |
| 2028-08 | webinar | 63 | 39 | 0.619 | 0.498 | ok |
| 2028-08 | website | 231 | 120 | 0.519 | 0.411 | above |
| 2028-09 | email | 49 | 26 | 0.531 | 0.416 | ok |
| 2028-09 | events | 56 | 28 | 0.500 | 0.443 | ok |
| 2028-09 | paid_ads | 18 | 10 | 0.556 | 0.372 | held |
| 2028-09 | referral | 66 | 53 | 0.803 | 0.518 | above |
| 2028-09 | webinar | 57 | 34 | 0.596 | 0.490 | ok |
| 2028-09 | website | 247 | 142 | 0.575 | 0.412 | above |
| 2028-10 | email | 49 | 20 | 0.408 | 0.415 | ok |
| 2028-10 | events | 56 | 31 | 0.554 | 0.440 | ok |
| 2028-10 | paid_ads | 13 | 5 | 0.385 | 0.372 | held |
| 2028-10 | referral | 46 | 29 | 0.630 | 0.524 | ok |
| 2028-10 | webinar | 68 | 48 | 0.706 | 0.511 | above |
| 2028-10 | website | 220 | 137 | 0.623 | 0.412 | above |
