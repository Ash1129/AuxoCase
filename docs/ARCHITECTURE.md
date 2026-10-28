# Architecture

AuxoCase is one Python service with three jobs inside it, the three Blueprint drew for the BrightPath brief (`docs/blueprint/`), with the same boundaries and the same HubSpot handoffs. Where the build departs from Blueprint's result, the reason is given and traced to the knowledge base (`knowledge/`).

```
HubSpot (mock in development, real API in production)
   │ contact created / property changed (webhook or search polling, H04)
   ▼
┌───────────────────────────── Job 1: score ─────────────────────────────┐
│ completeness check → features (channel, firmographics, behaviour)       │
│ → calibrated p(demo) from the live model (points table before one exists)│
│ → free-text reading: Claude lists buying signals with exact quotes;      │
│   code verifies every quote, caps the adjustment at ±10 points           │
│ → explanation: per-feature contributions → fixed-wording reason codes    │
│ → review flag (near the line, large referral, gaming text, reader unsure)│
│ → rank in the current unowned-MQL pool                                   │
│ → batch write auxo_* properties, keyed by contact + model + input hash   │
└──────────────────────────────────────────────────────────────────────────┘
   │ auxo_score / auxo_probability / auxo_rank / auxo_reason written
   ▼
┌───────────────────────────── Job 2: route ─────────────────────────────┐
│ free SDR slots = Σ (limit − open leads)                                  │
│ top of the pool fills (1 − e) of them; e = 8% drawn at random from below │
│ the line (the exploration slice, marked auxo_holdout)                    │
│ round robin skipping full SDRs; compare-and-set owner; task due in 4 h   │
│ nobody free → held queue with timestamp → manager page                   │
│ no first touch in 24 h → reminder task                                    │
└──────────────────────────────────────────────────────────────────────────┘
   │ hubspot_owner_id + task; SDR works / skips / junks / nudges rank (logged)
   ▼
┌───────────────────────────── Job 3: learn ─────────────────────────────┐
│ outcomes: meetings, deal stages, lead status → booked / worked-no-demo / │
│ not-worked (never a negative) + first-touch time                          │
│ monitor: p-chart per channel, observed vs predicted demo rate, n<30 held │
│ retrain on a sliding 12-month window of mature labels; LR vs GBT on      │
│ time-split precision@500; Platt calibration on the validation window     │
│ candidate → metrics beside the live model → marketing AND sales approve  │
│ → live; previous model kept as shadow for one cycle                      │
└──────────────────────────────────────────────────────────────────────────┘
```

## Modules

| Path | Job | What it holds |
|---|---|---|
| `hubspot/models.py`, `client.py` | all | HubSpot-shaped records and the one client interface the service uses |
| `storage/db.py` | all | the SQLite schema (mock `hs_*` tables and the service's own), and the data clock: the mock's generator and simulator set `clock`, so maturity windows and SLAs follow the replayed months; production leaves it unset and gets wall time |
| `hubspot/mock.py` | all | SQLite stand-in with HubSpot's semantics: batch 207 results, duplicated/out-of-order webhooks, compare-and-set owner, property history |
| `hubspot/real.py` | all | httpx client for the v3 API, webhook signature check; unverified against a live portal |
| `data/generate.py`, `simulate.py` | dev | 14 months of BrightPath-like history with the SDRs' cherry-picking baked in; a month replay with the system in place |
| `scoring/features.py` | 1 | the feature row; no names, emails or note text |
| `scoring/points.py` | 1 | cold-start points table (channel-aware) and the lift baseline |
| `scoring/model.py` | 1, 3 | train/evaluate/calibrate/persist; close model on post-demo rows |
| `scoring/explain.py` | 1 | contributions → reason codes → counterfactual line |
| `llm/client.py`, `signals.py`, `reason.py` | 1 | Claude CLI / API / offline; the quote checker; the rewrite checker |
| `scoring/pipeline.py` | 1 | the batch scorer, ranking, write-back, change-feed loop |
| `routing/engine.py` | 2 | capacity, exploration slice, round robin, held queue, SLA |
| `feedback/loop.py` | 3 | outcomes sync, training table, p-chart monitor, registry, approvals, shadow |
| `evaluation.py` | 3 | the report both teams read (`docs/evaluation.md`) |
| `service/app.py` + templates | all | SDR queue, lead page, manager page, model page, jobs API, webhook |
| `run.py` | all | CLI: seed, score, route, sync, monitor, train, approve, simulate, eval, serve, demo |

## Where the build departs from Blueprint's result, and why

| Blueprint said | The build does | Because |
|---|---|---|
| Three n8n workflows | One Python service exposing the three jobs as endpoints and CLI commands | The scoring step is a trained model with a retraining loop; n8n has no place to hold one. The brief's team has developers. An n8n or HubSpot workflow can still call `/jobs/*`. |
| Points table + LLM nudge, "memory" as a reference library | Calibrated logistic regression (GBT challenger), retrained on outcomes | The brief asks for scores that learn from bookings and closes and for "one shared number"; a points total isn't a probability and can't learn (L01, L04). The points table survives as the cold start and the baseline. |
| Rank = a HubSpot view sorted by score | Rank in the current unowned pool, with the capacity line explicit and precision@500 as the metric | 12 SDRs work 500 of 1,500; what matters is who is above the line (L03). |
| Feedback = a few-times-a-week weight change per channel after a p-chart | The p-chart is kept as the monitor; the model retrains on all features; two-team approval is kept | Channel weights alone can't fix title/size misranking; the chart is the right alarm, not the right fix (L09). |
| Outcomes measured among deals | Three label states; not-worked is unlabelled; an exploration slice of assigned slots | Only worked leads get labels; without a random slice the model learns the SDRs' taste (L05). |
| Routing requires a territory field | No territory; round robin only | The brief has no geography; the answer was round-robin. |
| Review of flagged leads only | Every SDR action is logged with the explanation shown: worked, skipped with reason, junk, bounded promote/demote | Reps make the final call on every lead; their choices are training data and the adoption signal (L12). |
| No time target | 4-hour task due, 24-hour first-touch SLA with reminders, speed-to-lead report | "Good leads sit untouched for days and go cold" (L07). |

## What Blueprint said that held up unchanged

Routing by channel from HubSpot's own source fields; the AI reads free text only, quotes its evidence, and may move a score by at most ±10; a code checker, not a second model, verifies the quotes; malformed AI output falls back to the deterministic score; writes keyed by contact and version; the score write must not re-trigger the scorer (the mock suppresses events on `auxo_*` writes); compare-and-set before assigning; owner and task both required for an assignment to count; a held queue that is someone's named job; a p-chart with a minimum group of 30; one approver per team, one version at a time, no batch approval; no individual named in a model proposal; least-privilege write scope.

## Personal data

The model sees role, company and behaviour, never names, emails or note text (features.py). The LLM sees note text only, returns quotes, and the quotes are the only part kept. Reasons never contain contact details (reason.py rejects them). Logs hold contact ids, not names. The real client needs only `crm.objects.contacts.read/write`, `crm.objects.companies.read`, `crm.objects.deals.read`, `crm.objects.owners.read`, `crm.schemas.contacts.write` and task scopes (H08). A lead's score and reason are HubSpot properties, so HubSpot's own GDPR deletion removes them with the contact (H10); the service's `scores` and `overrides` tables are keyed by contact id and should be purged on the same signal in production.
