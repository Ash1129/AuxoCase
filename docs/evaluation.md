# Evaluation report

Generated 2026-10-29T21:49:36 UTC. Live model: `demo-v1`.

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
| 2028-08 | yes | 498 | 306 | 0.61 | 18 | 64% |
| 2028-09 | yes | 490 | 303 | 0.62 | 17 | 69% |
| 2028-10 | yes | 502 | 280 | 0.56 | 18 | 65% |
| 2028-11 | yes | 551 | 321 | 0.58 | 24 | 50% |
| 2028-12 | yes | 551 | 317 | 0.58 | 23 | 52% |
| 2029-01 | yes | 491 | 281 | 0.57 | 24 | 50% |

Average per month: 172 demos from 497 leads worked before, 301 from 514 after (+75% demos at the same capacity). On the mock, the speed effect is the generator's assumption from BrightPath's answer 4 (a lead touched within a day books about twice as often); on the real portal it is measured.

All precision figures are computed on leads an SDR actually worked, because unworked leads have no label. They are biased by the SDRs' past choices until the exploration slice has run for a few months (knowledge base L05).

## The live model's test months

Months 2028-05, 2028-06 (a model from before the three-way split: it was refitted on them, so these are not held out). Precision@299 0.4482 vs HubSpot's points 0.3746 (lift 1.196x) and the service's cold-start table 0.4448 (lift 1.008x); AUC-PR 0.4456 against a base rate of 0.358; AUC-ROC 0.5945; Brier 0.2233; ECE 0.0068. Challenger: {'family': 'hgb', 'precision_at_k': 0.4615, 'ece': 0.0301, 'auc_pr': 0.4336}.

| Predicted | n | Observed |
|---|---|---|
| 0.0–0.1 | 4 | 0.0 |
| 0.1–0.2 | 13 | 0.154 |
| 0.2–0.3 | 205 | 0.273 |
| 0.3–0.4 | 501 | 0.349 |
| 0.4–0.5 | 231 | 0.429 |
| 0.5–0.6 | 43 | 0.558 |
| 0.6–0.7 | 3 | 0.667 |

## Phase 3: automated outreach below the capacity line

Explore share 50%; the band off (holdback 10%); at most 60 emails a day; sequence not set. Leads below the line are drawn into an arm as they become eligible: emailed by the sequence, or left to the ordinary process. A label matures 30 days after the draw.

| Arm | Leads | Replied | Worked by an SDR | Matured | Booked | Demo rate | Model's prediction |
|---|---|---|---|---|---|---|---|
| Emailed | 1867 | 322 | 343 | 1249 | 136 | 0.109 | 0.296 |
| Control | 1865 | 0 | 70 | 1284 | 10 | 0.008 | 0.296 |

The sequence lifts the demo rate of a lead below the line by +0.101. The model's prediction is the chance of a demo if an SDR worked the lead promptly, so the emailed arm's shortfall against it is what an automated email gives up against a person. Enough matured labels to judge the band.

## Cost against outcome: what a demo costs by the way the lead was reached

Unit costs are settings, not facts: an SDR hour 45 dollars, a cold touch 20 minutes, answering a reply 10 minutes, a Claude draft 0.02 dollars. A demo is worth 4,800 dollars in first-year contract value (the brief: 24,000 at a 20% close rate; the outcomes show 23% on 3211 demos old enough to have closed).

| Route | Leads reached | Demos | Demo rate | SDR hours | Cost | Cost per demo | Demos per SDR hour | Value |
|---|---|---|---|---|---|---|---|---|
| An SDR's touch on a ranked lead | 1268 | 734 | 0.579 | 422.7 | 19,020 | 26 | 1.740 | 3,523,200 |
| The automated sequence (phase 3) | 1867 | 197 | 0.106 | 53.7 | 2,457 | 12 | 3.670 | 945,600 |

Both routes are counted from 2028-10-30, the sequence's first day, so the months match. Per 100 emails: 17 replies and 10.6 demos, for 41.8 dollars of drafting (6 drafts metered by the Claude CLI, the figure Revenium receives, and the rest at their mean of 0.0224 dollars) and 53.7 SDR hours on replies in all. The control arm (1865 leads left to the ordinary process) booked 14, a rate of 0.007, so the sequence added about 183.0 demos it would not otherwise have had, worth about 878,400 dollars. SDR time is the scarce input (12 SDRs, by the brief), so demos per SDR hour is the number to compare.

Every reply took SDR time that would have gone to a ranked lead: at 10 minutes a reply against 20 a cold touch, the 322 replies displaced about 93.2 ranked demos, so the sequence's net is about +89.8 demos, +431,040 dollars. It breaks even when a reply takes 19.6 minutes of an SDR's time; the number to measure on the real portal is how long a reply really takes.

Demos booked a month, the months before the sequence against the months with it (the 12 SDRs are fixed, so this total is what has to rise):

| Month | Demos | Through the sequence | |
|---|---|---|---|
| 2028-07 | 82 | 0 | before |
| 2028-08 | 231 | 0 | before |
| 2028-09 | 302 | 0 | before |
| 2028-10 | 298 | 0 | the sequence starts |
| 2028-11 | 313 | 65 | with the sequence |
| 2028-12 | 322 | 61 | with the sequence |
| 2029-01 | 320 | 68 | with the sequence |

## Ranked slice vs exploration slice

| Slice | Assigned | Worked | Booked |
|---|---|---|---|
| assigned | 2901 | 2862 | 1760 |
| holdout_assigned | 230 | 229 | 50 |

## SDR overrides

| Action | Count | Booked |
|---|---|---|
| demote | 1 | 1 |
| promote | 4 | 4 |
| skipped | 33 | 1 |
| worked | 6 | 2 |

## Speed to lead (lead created to first touch)

| Leads | Touched | Median hours | Within 24 h |
|---|---|---|---|
| Not routed by AuxoCase (history) | 6044 | 70.8 | 21% |
| Routed by AuxoCase | 3083 | 20.0 | 58% |

Measured from outcomes: leads first touched within a day booked at 0.66, later ones at 0.308 (factor 0.47; 3074 and 6061 leads). Routing discounts a lead past its first day by `stale_lead_factor` = 0.5; keep the two close.

## Where HubSpot's MQL gate drops leads (last three months)

Only MQLs reach the pool this service ranks. Leads missing a title or a size get no points for them in HubSpot's model, so they pass the gate less often.

| Channel | Leads | Reach MQL | No job title | No company size |
|---|---|---|---|---|
| website | 6449 | 49% | 12% | 14% |
| paid_ads | 3770 | 24% | 16% | 17% |
| webinar | 2812 | 8% | 45% | 47% |
| referral | 1904 | 10% | 49% | 48% |
| events | 1867 | 14% | 20% | 26% |
| email | 1798 | 23% | 12% | 16% |

## Enrichment

Asked the enrichment tool about 670 pool leads missing a field; it filled at least one field for 486. Titles: 40 of 62. Sizes: 446 of 608.

## Why deals were lost (free-text reasons read into categories, quote checked)

| Reason | under 50 | 50 149 | 150 299 | 300 500 | over 500 | unknown | Total |
|---|---|---|---|---|---|---|---|
| price | 74 | 549 | 386 | 228 | 17 | 30 | 1284 |
| competitor | 50 | 503 | 354 | 199 | 33 | 27 | 1166 |
| no decision | 31 | 455 | 272 | 175 | 29 | 28 | 990 |
| timing | 38 | 429 | 278 | 151 | 27 | 20 | 943 |
| missing feature | 21 | 320 | 207 | 121 | 56 | 15 | 740 |
| other | 26 | 199 | 137 | 70 | 9 | 12 | 453 |
| poor fit | 37 | 162 | 94 | 61 | 12 | 14 | 380 |

## Channel monitor (latest run)

| Period | Channel | Worked | Demos | Observed | Predicted | Flag |
|---|---|---|---|---|---|---|
| 2028-10 | email | 8 | 4 | 0.500 | 0.340 | held |
| 2028-10 | events | 12 | 6 | 0.500 | 0.451 | held |
| 2028-10 | paid_ads | 11 | 4 | 0.364 | 0.345 | held |
| 2028-10 | referral | 6 | 6 | 1.000 | 0.508 | held |
| 2028-10 | webinar | 14 | 7 | 0.500 | 0.483 | held |
| 2028-10 | website | 55 | 26 | 0.473 | 0.397 | ok |
| 2028-11 | email | 44 | 27 | 0.614 | 0.372 | above |
| 2028-11 | events | 37 | 18 | 0.486 | 0.424 | ok |
| 2028-11 | paid_ads | 46 | 18 | 0.391 | 0.338 | ok |
| 2028-11 | referral | 60 | 51 | 0.850 | 0.526 | above |
| 2028-11 | webinar | 52 | 32 | 0.615 | 0.484 | ok |
| 2028-11 | website | 294 | 160 | 0.544 | 0.401 | above |
| 2028-12 | email | 42 | 18 | 0.429 | 0.379 | ok |
| 2028-12 | events | 40 | 26 | 0.650 | 0.423 | ok |
| 2028-12 | paid_ads | 58 | 33 | 0.569 | 0.332 | above |
| 2028-12 | referral | 60 | 42 | 0.700 | 0.498 | above |
| 2028-12 | webinar | 69 | 50 | 0.725 | 0.490 | above |
| 2028-12 | website | 285 | 152 | 0.533 | 0.388 | above |
| 2029-01 | email | 22 | 7 | 0.318 | 0.380 | held |
| 2029-01 | events | 52 | 29 | 0.558 | 0.423 | ok |
| 2029-01 | paid_ads | 39 | 22 | 0.564 | 0.344 | ok |
| 2029-01 | referral | 41 | 28 | 0.683 | 0.516 | ok |
| 2029-01 | webinar | 49 | 33 | 0.673 | 0.506 | ok |
| 2029-01 | website | 234 | 142 | 0.607 | 0.400 | above |
