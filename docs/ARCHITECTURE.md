# Architecture

AuxoCase is one Python service with three jobs inside it, the three Blueprint drew for the BrightPath brief (`docs/blueprint/`), with the same boundaries and the same HubSpot handoffs. Where the build departs from Blueprint's result, the reason is given and traced to the knowledge base (`knowledge/`).

```
HubSpot (mock in development, real API in production)
   │ contact created / property changed (webhook or search polling, H04)
   ▼
┌───────────────────────────── Job 1: score ─────────────────────────────┐
│ enrichment: fill a blank title or size once, from the licensed tool      │
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
│ pool = unowned MQLs created in the last 30 days (older: dispositioned)   │
│ order by value now: probability × stale_lead_factor once past a day      │
│ free SDR slots = Σ (limit − open leads); limit 1, refilled on each touch │
│ top of the pool fills (1 − e) of them; e = 8% drawn at random from below │
│ the line (the exploration slice, marked auxo_holdout); runs as leads come│
│ round robin skipping full SDRs; compare-and-set owner; task due in 4 h   │
│ above the line, unplaced 24 h → held queue with timestamp → manager page │
│ no first touch in 24 h → reminder task                                    │
│ on assignment: first email drafted, checked, booking link added;         │
│ written to the lead and enrolled in the SDR's HubSpot sequence (step 1   │
│ waits for the SDR to approve); follow-ups drafted from the activity      │
│ first email or call logged in HubSpot → worked, slot refilled (1 min)    │
└──────────────────────────────────────────────────────────────────────────┘
   │ hubspot_owner_id + task; SDR works / skips / junks / nudges rank (logged)
   ▼
┌───────────────────────────── Job 3: learn ─────────────────────────────┐
│ outcomes: meetings, deal stages, lead status → booked / worked-no-demo / │
│ not-worked (never a negative) + first-touch time                          │
│ monitor: p-chart per channel, observed vs predicted demo rate, n<30 held │
│ loss reasons: free text → fixed category with a checked quote, by size   │
│ retrain on a sliding 12-month window of mature labels; LR vs GBT on      │
│ train / calibrate / test months: fit on the oldest, choose the family    │
│ and Platt-calibrate on the next two, report the newest two once; the     │
│ model tested is the model that goes live (no refit)                      │
│ candidate → metrics beside the live model → marketing AND sales approve  │
│ → live; previous model kept as shadow for one cycle                      │
│ → pool rescored on the new model, best-ranked leads first                │
└──────────────────────────────────────────────────────────────────────────┘
```

## Modules

| Path | Job | What it holds |
|---|---|---|
| `hubspot/models.py`, `client.py` | all | HubSpot-shaped records and the one client interface the service uses |
| `storage/db.py` | all | the SQLite schema (with a small column migration) (mock `hs_*` tables and the service's own), and the data clock: the mock's generator and simulator set `clock`, so maturity windows and SLAs follow the replayed months; production leaves it unset and gets wall time |
| `hubspot/mock.py` | all | SQLite stand-in with HubSpot's semantics: batch 207 results, duplicated/out-of-order webhooks, compare-and-set owner, property history |
| `hubspot/real.py` | all | httpx client for the v3 API, webhook signature check; unverified against a live portal |
| `data/generate.py`, `simulate.py` | dev | 14 months of BrightPath-like history with the SDRs' cherry-picking baked in; a month replay with the system in place |
| `capacity.py` | all | first touches a month from SDR minutes: SDRs × hours on first touches ÷ minutes a touch (settings; defaults give the brief's 500), read by the capacity line, the Overview, Leadership, the simulator and the cost report |
| `data/rebase.py` | dev | shifts the mock's demo-clock dates onto the present by whole years, leaves wall-time logs, and sets `clock_live` so `storage/db.clock` is the wall clock |
| `pool.py` | 1, 2 | the pool both jobs share: unowned leads in the pool stages, inside the age window |
| `scoring/enrich.py` | 1 | one enrichment lookup per lead missing a title or size, written back and logged |
| `scoring/features.py` | 1 | the feature row; no names, emails or note text |
| `scoring/points.py` | 1 | cold-start points table (channel-aware) and the lift baseline |
| `scoring/model.py` | 1, 3 | train/evaluate/calibrate/persist; close model on post-demo rows |
| `scoring/explain.py` | 1 | contributions → reason codes → counterfactual line |
| `llm/client.py`, `signals.py`, `reason.py` | 1 | Claude CLI / API / offline; the quote checker; the rewrite checker |
| `llm/loss_reasons.py` | 3 | free-text closed-lost reasons into fixed categories, quote checked |
| `llm/emails.py`, `calls.py` | 2 | the SDR's three emails and the call brief (opener, questions from the score's drivers, the objection from the loss reasons, ask, voicemail): template fallback, Claude when on, every draft code-checked |
| `routing/outreach.py`, `touches.py` | 2 | the first email drafted and enrolled on assignment; first touch, HubSpot's call outcome and replies read from the contact's activity |
| `scoring/pipeline.py` | 1 | the batch scorer, ranking, write-back, change-feed loop |
| `routing/engine.py` | 2 | capacity, exploration slice, round robin, held queue, SLA |
| `feedback/loop.py` | 3 | outcomes sync, training table, p-chart monitor, registry, approvals, shadow |
| `evaluation.py` | 3 | the report both teams read (`docs/evaluation.md`) |
| `service/app.py` + templates | all | SDR queue, lead page, manager page, model page, the Next phases page (phase 2 and 3 switches, recorded), jobs API, webhook |
| `run.py` | all | CLI: seed, score, route, sync, monitor, train, approve, simulate, rebase, eval, serve, demo |

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
| (After the follow-up answers) | Each SDR holds one waiting lead, refilled on each touch; the pool is ordered by probability × a freshness discount (0.5 after a day for BrightPath) | Median first response was 3 days and a lead touched within a day converts about twice as often (answer 4); the win asked for is faster follow-up at the same headcount (answer 9). |
| (After the follow-up answers) | Enrichment before scoring; blank kept as its own value | ~40% of leads miss a title or size, referral and webinar most; the licensed tool was used inconsistently (answers 1–2). |

## What Blueprint said that held up unchanged

Routing by channel from HubSpot's own source fields; the AI reads free text only, quotes its evidence, and may move a score by at most ±10; a code checker, not a second model, verifies the quotes; malformed AI output falls back to the deterministic score; writes keyed by contact and version; the score write must not re-trigger the scorer (the mock suppresses events on `auxo_*` writes); compare-and-set before assigning; owner and task both required for an assignment to count; a held queue that is someone's named job; a p-chart with a minimum group of 30; one approver per team, one version at a time, no batch approval; no individual named in a model proposal; least-privilege write scope.

## Phase 2: scoring below the MQL gate

The pool holds MQLs only, because that is the process the brief describes and the only leads with outcome labels. But HubSpot's gate gives nothing for a blank title or size, so most referral and webinar leads never reach MQL, and ranking within the MQLs cannot bring them back. The evaluation's MQL-gate table shows where that loss falls, and its capacity head to head sizes it: on the generator's truth, the same 500 a month drawn from every lead instead of the MQLs would book about 48 more demos (+45 to +51 across held-out months), with about 358 of the 500 coming from below the gate, almost all referrals and webinars. Once the exploration slice has run for a few months, setting `pool_stages` to `marketingqualifiedlead,lead` lets the model replace the gate. Do that only after the exploration slice includes leads from below the gate: until then the model has no labels there.

How it runs, both switches off by default (`run.py below-gate`):

1. Explore. `explore_below_gate_share` sends that share of the exploration slots to plain leads drawn at random from below the gate (unowned, inside the pool's age window, with an email and a channel). They are scored as they are picked, with the live model. The ranked pool is untouched. How many slots the exploration slice gets, and how many of those go below the gate, comes from a running credit kept in settings, not a per-pass coin flip: most passes free one slot, and a fresh router on each of them would otherwise round the same way every time. Which lead is drawn is random.
2. Judge. The routing log records, at hand-out, whether a lead was below the gate and the score it had. `below_gate_readiness` (in the evaluation report and the command's output) counts the random ones, whichever way they were drawn: handed out, worked, matured, booked, and their score at hand-out beside what happened. 100 matured labels is the bar for a first look; a predicted rate well away from the observed one means the model does not yet understand those leads. Leads handed out below the gate by rank are counted apart, since the SDRs' choice biases them, and the evaluation's exploration table gives below-gate leads rows of their own.
3. Rank. Adding `lead` to `pool_stages` puts plain leads in the ranked pool. The command refuses until the bar is met, unless forced, and marks the pool for rescoring, so the plain leads already waiting (never scored, since they were not in the pool) are scored by the rescore job, best first, newest first among the unranked.

In every case an assigned plain lead moves to salesqualifiedlead (HubSpot allows lead to SQL directly), so it counts against the SDR's capacity, outcome sync and the training table see it, and the exploration flag marks it as an unbiased label. The assignment also sets `auxo_below_gate` on it, which stays true after it moves on; the "below old threshold" badge shows for that flag or for a lead still at the lead stage, so a lead HubSpot's own gate passes later loses it. The MQL-gate table and the capacity head to head count a lead as through the gate only if its stage says so and the flag does not. What this changes for marketing: those leads skip the MQL stage, so an MQL count no longer counts every lead sales worked; the report's MQL-gate table and the below-gate counts are the numbers to watch instead.

## Phase 3: automated outreach below the capacity line

The brief's largest number is the one nothing above touches: of 1,500 MQLs a month the 12 SDRs reach about 500, and the other 1,000 are never contacted. Phases 1 and 2 choose and order the 500. On the generator's truth the pool is far from exhausted: each extra 100 touches a month would book about 59 more demos at first, falling to about 37 by the time every MQL is worked. Phase 3 reaches the rest without adding SDRs, by giving leads below the capacity line an automated HubSpot sequence, and spends SDR time only on the ones that answer. The brief says SDRs should still make the final call on who to contact and what to say, so this ships as an experiment with its own control arm, behind two switches that are off by default (`run.py tier2`, `routing/tier2.py`).

1. Draw. Each routing pass (and the route job) looks at the scored pool leads below the capacity line that are fresh enough to email (`tier2_max_age_days`, 7), have an address and have not been drawn. The day's limit (`tier2_daily_limit`, 60) bounds the draw, not only the sends, so both arms come from the same best-first slice. Each drawn lead lands in an arm at random: `emailed` with the send share, else `control`. Both rows keep the score, rank and line the lead had, so the comparison has a denominator.
2. Send. For an emailed lead the first email is drafted as on assignment (Claude when on, checked by code, else the template), written to `auxo_email_subject` and `auxo_email_body`, and the lead is enrolled in the sequence set by `AUXO_TIER2_SEQUENCE_ID`, whose first step sends those tokens itself, from an SDR's inbox in rotation. `auxo_route` becomes `automated` and the lead leaves the SDRs' pool while the sequence runs. The mock has no sequence engine, so it logs the outgoing email itself; a real portal sends nothing until the sequence exists.
3. Reply. The activity read that finds first touches also finds replies: an incoming email on a lead in the sequence marks it replied and hands it to the SDR it heard from if they have a slot, else the next in rotation, else the held queue, with a task that says why. HubSpot unenrols a replying contact itself. A sequence unanswered for `tier2_sequence_days` (14) ends, and the lead returns to the pool (usually to age out of it).
4. Judge. `readiness` (the evaluation report, the Leadership page, the command's output) compares the arms: leads, replies, matured labels (30 days after the draw), demos, the observed rate beside the model's prediction. The prediction is the chance if an SDR worked the lead promptly, so the emailed arm's shortfall against it is what an automated email gives up against a person; the control arm says what those leads book when left alone. `--band on` emails every eligible lead but a holdback (`tier2_holdback`, 10%) that keeps the control alive, and is refused until 200 emailed leads have matured labels, unless forced.
5. Learn, carefully. Emailed leads are kept out of the model's training table: an automated email is a different treatment from an SDR's touch, which is what the model predicts. The control arm is the ordinary process and stays in.

What it costs is reported beside what it brings (`costs.py`, the report's cost section, the Leadership page). SDR time is the scarce input, so the comparison is demos per SDR hour by route: an SDR's cold touch on a ranked lead against the sequence, whose SDR time is only the replies. Unit costs (an SDR's hour, minutes a touch, minutes a reply) are settings with stated defaults; the contract value and close rate are the brief's. The CLI reader runs on the owner's Claude Code plan, Sonnet for the short checked answers (signals, reasons, loss reasons) and Opus for what a lead reads or hears, the emails and the call brief (`AUXO_CLI_MODEL`, `AUXO_CLI_DRAFT_MODEL`; anything else is refused); a metered Opus draft costs about two cents. Drafting is priced from the service's own model calls: the Claude CLI reports each call's cost in its JSON, the same per-call figure it exports to Revenium when the owner's `revenium-metering` setup is in the shell, and `llm/usage.py` records it with the task and, for a draft, the lead. The calls go out tagged with Revenium's `revenium.product.name` (AuxoCase), `revenium.task.type` (signals, reason, outreach_email, call_brief, loss_reason) and `revenium.agent.name`, so Revenium's cost-by-product and cost-by-task views show the service apart from the owner's own Claude Code sessions, and `run.py costs --revenium` reads the product's figure back with a read key. The control arm alone overstates the sequence: every reply takes SDR time that would have gone to a ranked lead, so the report also counts the ranked demos the replies displaced (replies, times the reply's share of a cold touch, times the ranked route's demo rate), the net after that, the reply time at which the two cancel, and demos booked a month before and with the sequence, which is the plain test when the 12 SDRs are fixed. On the mock a lead answers the sequence at `TIER2_REPLY_FACTOR` (0.45) of its hidden demo chance and a replied lead books at `TIER2_DEMO_GIVEN_REPLY` (0.65) once an SDR follows up, and the simulator charges a reply the same share of a touch the cost settings do (`sdr_minutes_reply` over `sdr_minutes_touch`, half by default): assumptions, flagged as such, that a real portal replaces with measurements. With a reply charged as a full touch, three simulated months with the sequence booked no more demos than the months before it (the ranked route gave up what the sequence added); at half a touch they booked about 20 more a month, 313, 322 and 320 against about 300 before, with 60 to 70 a month coming through the sequence.

## HubSpot's MCP server

BrightPath noted HubSpot has MCP servers. They suit read-only assistant use, such as an SDR asking about a lead's history. Scores, ranks, owners and tasks are written through the v3 API, whose batch and multi-status semantics the service relies on (H03, H05).

## Personal data

The model sees role, company and behaviour, never names, emails or note text (features.py). The enrichment step should send the tool only what it needs to match a lead (email domain, company) and ask only for title and size. The LLM sees note text only, returns quotes, and the quotes are the only part kept. Reasons never contain contact details (reason.py rejects them). Logs hold contact ids, not names. The real client needs only `crm.objects.contacts.read/write`, `crm.objects.companies.read`, `crm.objects.deals.read`, `crm.objects.owners.read`, `crm.schemas.contacts.write` and task scopes (H08), plus `crm.objects.emails.read` and `crm.objects.calls.read` to see the first touch, `automation.sequences.enrollments.write` to enrol leads in a sequence and `scheduler.meetings.meeting-link.read` for booking links. Drafts carry no score, rank or note text beyond what the checker allows, and nothing is sent without an SDR. A lead's score and reason are HubSpot properties, so HubSpot's own GDPR deletion removes them with the contact (H10); the service's `scores` and `overrides` tables are keyed by contact id and should be purged on the same signal in production.

## The service's own surface

The pages are reached from browsers, the webhook from HubSpot (`service/security.py`). Only the host names in `AUXO_ALLOWED_HOSTS` are served, which stops a hostile domain from re-pointing its DNS at the service. A write whose Origin or Referer is another site is refused, so no other page can approve a model, release a held lead or act on a lead through a visitor's browser. The webhook is exempt from that check and verified by HubSpot's signature instead, which the real client requires. Replies carry a content security policy (scripts from this service only), nosniff and no framing. Lead actions and approvals carry a token for the state the person saw and are refused, with a note on the page, if the lead or the model changed in between. What this is not: sign-in. Approver names are typed, not authenticated; a deployment for more than one office puts the service behind the company's single sign-on and takes the approver from it.
