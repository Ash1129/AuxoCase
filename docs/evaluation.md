# Evaluation report

Generated 2026-10-28T18:32:33 UTC. Live model: `demo-v1`.

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
| 2028-08 | yes | 505 | 306 | 0.61 | 17 | 65% |
| 2028-09 | yes | 493 | 304 | 0.62 | 17 | 69% |
| 2028-10 | yes | 481 | 264 | 0.55 | 17 | 65% |

Average per month: 172 demos from 497 leads worked before, 291 from 493 after (+69% demos at the same capacity). On the mock, the speed effect is the generator's assumption from BrightPath's answer 4 (a lead touched within a day books about twice as often); on the real portal it is measured.

All precision figures are computed on leads an SDR actually worked, because unworked leads have no label. They are biased by the SDRs' past choices until the exploration slice has run for a few months (knowledge base L05).

## Head to head at the capacity line

K = 500 (one month of SDR capacity), 6388 labelled leads, 2574 demos.

| Ranker | Precision@K |
|---|---|
| AuxoCase live model | **0.668** |
| Points table (channel-aware, cold start) | 0.672 |
| HubSpot's current points (title, size, pages) | 0.456 |
| Random (base rate) | 0.4029 |

## Across the whole pool, on the generator's truth (mock only)

The newest month's 1740 MQLs, worked or not, ranked by each ranker; the average hidden chance of a demo, if touched promptly, in each top third (580 leads). On leads the SDRs chose, HubSpot's score barely loses (above); across everything, it barely beats random.

| Ranker | Demo chance in the top third |
|---|---|
| AuxoCase live model | **0.689** |
| HubSpot's current points | 0.559 |
| Random | 0.519 |
| Best possible (the truth itself) | 0.736 |

## Validation window (time-ordered)

Months 2028-05, 2028-06: precision@299 0.4482 vs HubSpot's points 0.3746 (lift 1.196x) and the service's cold-start table 0.4448 (lift 1.008x); AUC-PR 0.4456 against a base rate of 0.358; AUC-ROC 0.5945; Brier 0.2233; ECE 0.0068. Challenger: {'family': 'hgb', 'precision_at_k': 0.4615, 'ece': 0.0301, 'auc_pr': 0.4336}.

| Predicted | n | Observed |
|---|---|---|
| 0.0–0.1 | 4 | 0.0 |
| 0.1–0.2 | 13 | 0.154 |
| 0.2–0.3 | 205 | 0.273 |
| 0.3–0.4 | 501 | 0.349 |
| 0.4–0.5 | 231 | 0.429 |
| 0.5–0.6 | 43 | 0.558 |
| 0.6–0.7 | 3 | 0.667 |

## By channel (all labelled history)

| Channel | n | Observed demo rate | Predicted |
|---|---|---|---|
| email | 444 | 0.383 | 0.355 |
| events | 423 | 0.461 | 0.406 |
| paid_ads | 835 | 0.322 | 0.327 |
| referral | 332 | 0.666 | 0.506 |
| webinar | 362 | 0.583 | 0.485 |
| website | 3992 | 0.378 | 0.364 |

## Ranked slice vs exploration slice

| Slice | Assigned | Worked | Booked |
|---|---|---|---|
| assigned | 1388 | 1381 | 851 |
| holdout_assigned | 99 | 98 | 23 |

## SDR overrides

| Action | Count | Booked |
|---|---|---|
| skipped | 16 | 0 |

## Speed to lead (lead created to first touch)

| Leads | Touched | Median hours | Within 24 h |
|---|---|---|---|
| Not routed by AuxoCase (history) | 6044 | 70.8 | 21% |
| Routed by AuxoCase | 1479 | 17.0 | 66% |

Measured from outcomes: leads first touched within a day booked at 0.633, later ones at 0.291 (factor 0.46; 2257 and 5266 leads). Routing discounts a lead past its first day by `stale_lead_factor` = 0.5; keep the two close.

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
| price | 63 | 489 | 330 | 201 | 15 | 27 | 1125 |
| competitor | 45 | 438 | 307 | 179 | 28 | 25 | 1022 |
| no decision | 26 | 393 | 238 | 142 | 25 | 28 | 852 |
| timing | 34 | 387 | 249 | 130 | 26 | 18 | 844 |
| missing feature | 19 | 281 | 182 | 97 | 45 | 15 | 639 |
| other | 24 | 179 | 115 | 65 | 8 | 11 | 402 |
| poor fit | 29 | 144 | 80 | 56 | 10 | 14 | 333 |

## Channel monitor (latest run)

| Period | Channel | Worked | Demos | Observed | Predicted | Flag |
|---|---|---|---|---|---|---|
| 2028-07 | email | 1 | 1 | 1.000 | 0.370 | held |
| 2028-07 | paid_ads | 2 | 1 | 0.500 | 0.345 | held |
| 2028-07 | referral | 2 | 1 | 0.500 | 0.479 | held |
| 2028-07 | website | 10 | 7 | 0.700 | 0.364 | held |
| 2028-08 | email | 33 | 24 | 0.727 | 0.416 | above |
| 2028-08 | events | 54 | 34 | 0.630 | 0.431 | ok |
| 2028-08 | paid_ads | 28 | 14 | 0.500 | 0.376 | held |
| 2028-08 | referral | 51 | 42 | 0.824 | 0.515 | above |
| 2028-08 | webinar | 65 | 42 | 0.646 | 0.490 | ok |
| 2028-08 | website | 266 | 148 | 0.556 | 0.416 | above |
| 2028-09 | email | 25 | 17 | 0.680 | 0.420 | held |
| 2028-09 | events | 55 | 27 | 0.491 | 0.423 | ok |
| 2028-09 | paid_ads | 29 | 18 | 0.621 | 0.380 | held |
| 2028-09 | referral | 69 | 57 | 0.826 | 0.498 | above |
| 2028-09 | webinar | 54 | 34 | 0.630 | 0.489 | ok |
| 2028-09 | website | 263 | 146 | 0.555 | 0.417 | above |
| 2028-10 | email | 27 | 13 | 0.481 | 0.406 | held |
| 2028-10 | events | 53 | 29 | 0.547 | 0.431 | ok |
| 2028-10 | paid_ads | 13 | 6 | 0.462 | 0.395 | held |
| 2028-10 | referral | 44 | 33 | 0.750 | 0.511 | above |
| 2028-10 | webinar | 68 | 48 | 0.706 | 0.492 | above |
| 2028-10 | website | 248 | 128 | 0.516 | 0.416 | above |
