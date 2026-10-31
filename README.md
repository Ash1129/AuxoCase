# AuxoCase

Predictive lead scoring, capacity-aware routing and an outcome feedback loop for HubSpot, built for the BrightPath Payroll case: 6,000 leads a month, 1,500 MQLs, 12 SDRs who can work 500, and a points model that under-scores the two channels that convert best.

Every lead gets **one calibrated number** ("62" means about a 62% chance of booking a demo if an SDR works it), a **rank in the current pool**, a **plain-English reason** with the exact words from the lead's notes that moved it, and an **SDR assignment within capacity**. Which leads booked and closed flows back monthly into a retrained model that goes live only when **one person from marketing and one from sales approve it**, with its numbers beside the live model's. SDRs still make the final call on every lead, and every call is logged.

> Status: a working system on synthetic data against a mock HubSpot that has HubSpot's semantics (webhooks that duplicate and reorder, batch writes with per-record status, compare-and-set owners, property history). The real-API client is written from the official docs but has not been run against a live portal; see `src/auxocase/hubspot/real.py`.

## The numbers so far (synthetic data, `docs/evaluation.md`)

Three years of BrightPath-like history, generated to match the brief and BrightPath's follow-up answers (`docs/blueprint/brief-answers.md`), then three simulated months with the system in place at the same 12 SDRs and ~500 touches a month. The "after" includes the 8% exploration slice: random leads that cost a few demos a month and are what keeps the model's labels unbiased.

| | Before (history) | After (AuxoCase) |
|---|---|---|
| Demos booked per month | 172 | **274** (+59%) |
| Median hours from lead to first touch | 71 | **17** |
| Leads touched within 24 hours | 21% | **64%** |

Where the gain comes from, one simulated month per setup, averaged over five seeds (`python run.py queue-limits`; ranges in `docs/blueprint/brief-answers.md`):

| Routing | Touched within 24 h | Demos |
|---|---|---|
| Model ranking, deep queues (3 leads per SDR), no freshness | 15% | 205 |
| + freshness discount (a lead past its first day is worth half) | 31% | 224 |
| + two waiting leads per SDR | 49% | 249 |
| + one waiting lead per SDR, refilled as they touch it | 64% | 274 |

The ranking alone is worth about a fifth more demos than the history's 172 a month. The freshness discount doubles how many leads are touched within a day and adds about 20 demos even while queues are three deep. The rest of the gain comes from small queues, which let SDRs reach the best leads inside their first day. On the mock, "a lead touched within a day books twice as often" is BrightPath's answer 4 built into the generator. On the real portal it is measured: the report estimates the factor from outcomes (0.46 here, against the 0.5 routing uses).

How good is the ranking? Every number here is on months the model never trained on: it is fitted on the oldest months, chosen and calibrated on the next two, and tested once on the newest two, and the model that goes live is the one tested. On leads the SDRs chose to work, it beats HubSpot's points at the capacity line (precision@299 0.448 against 0.375), but barely differs from the service's own channel-aware cold-start table (0.445). The worked-leads number understates the gap, because the SDRs' past choices hide it (L05). On the generator's truth, for every MQL worked or not across six held-out months, the model's 500 a month would book **348** demos if worked promptly, against **292** for HubSpot's points (+55 a month, +53 to +58 across months), 345 for the cold-start table and 262 at random, with 376 the best possible. Calibration holds (expected calibration error 0.012). The exploration slice is how real data closes the worked-leads gap over time.

Ranking every lead, not only the MQLs, would add about **48 demos a month** on the same 500 (+45 to +51): 358 of them would be leads HubSpot's gate keeps out, almost all referrals and webinars. The model has never seen an outcome below the gate, though, so this is phase 2, in two switches, both off by default. `python run.py below-gate --explore 0.5` sends half of the 8% exploration slice to plain leads drawn at random from below the gate, scored as they are handed out; the evaluation report and `python run.py below-gate` then show how many of them have matured and how their demo rate compares with what the model predicted. `python run.py below-gate --rank on` adds plain leads to the ranked pool and has the rescore job score the ones already waiting, best first; it refuses until 100 of those labels exist (`--force` overrides). Either way an assigned plain lead moves to SQL, counts against the SDR's capacity, and carries a "below old threshold" badge (`auxo_below_gate` in HubSpot). A portal set up before this property existed needs `ensure_properties()` run again (go-live step 1).

What the data says for BrightPath beyond the scores:

- **The 1,000 MQLs a month nobody contacts are the biggest number in the brief.** On the generator's truth each extra 100 touches a month would book about 59 more demos at first, falling to about 37 by the time every MQL is worked, so the pool is far from exhausted. Phase 3 reaches them without adding SDRs: an automated HubSpot sequence for leads below the capacity line, with a reply handed to an SDR at once, run as an experiment with its own control arm and priced against what it brings (`docs/ARCHITECTURE.md`, "Phase 3"; `python run.py tier2`, `python run.py costs`). Off by default. Three simulated months with half the leads below the line in the sequence: 10.7 demos per 100 emails against 0.7 for the control arm, but each reply takes SDR time from a ranked lead, so the net is about 20 more demos a month at a 10-minute reply and nothing at a 20-minute one; the reply's real length is the number to measure (`docs/evaluation.md`, "Cost against outcome").
- **Most referral and webinar leads never reach the pool.** 9% of referrals and 8% of webinar leads pass HubSpot's MQL gate, against 49% from the website, because half of them have no title or size and HubSpot's points give nothing for a blank. Letting the model replace that gate is phase 2 (`docs/ARCHITECTURE.md`).
- **Larger companies leave for missing features.** Missing features account for 29% of losses at companies over 500 employees and about 12% under 300 (loss reasons read into categories, quotes checked).
- **Enrichment fills gaps.** Asked about 394 pool leads missing a field, the stand-in enrichment tool filled 280. The real tool's match rate replaces that number.
- **Faster follow-up makes the live model under-predict.** The channel monitor flags observed demo rates above prediction. Leads booked more because they were touched sooner, so the model must be retrained and approved.

## Run it

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install -e .
python run.py demo            # seeds 36 months of history, trains and approves a model, scores, routes, serves http://127.0.0.1:8000
```

Step by step:

| Command | What it does |
|---|---|
| `python run.py seed` | 36 months of BrightPath-like history in the mock: the SDRs' cherry-picking, blank titles and sizes, a 3-day first response, messy loss reasons |
| `python run.py sync` | pulls meetings, deal stages and lead status into outcome labels (booked / worked-no-demo / not-worked) |
| `python run.py train` | trains a candidate on 12 months of mature labels; prints precision@500, lift over the points model, AUC-PR, ECE, by channel |
| `python run.py approve demo-v1 marketing "Name"` then `... sales "Name"` | the two-team approval; both → live |
| `python run.py score [--all]` | scores changed leads from the change feed (or the whole pool) and writes `auxo_*` properties back |
| `python run.py route` | fills free SDR slots with the leads worth most now, keeps an 8% exploration slice, creates tasks; strong leads unplaced after 24 h go to the manager |
| `python run.py monitor` | the per-channel p-chart of observed vs predicted demo rate |
| `python run.py losses` | reads new closed-lost reasons into fixed categories (quote checked) |
| `python run.py simulate --months 3` | replays months in six-hour steps with the system in place: new leads, scoring, routing, SDR touches at the brief's capacity, outcomes, loss reasons, retrain |
| `python run.py eval` | writes `docs/evaluation.md` |
| `python run.py below-gate [--explore 0.5] [--rank on\|off]` | phase 2: send part of the exploration slice below HubSpot's MQL gate, and rank there once enough of those leads have outcomes; prints what is known there |
| `python run.py tier2 [--explore 0.2] [--band on\|off]` | phase 3: put a share of the leads below the capacity line in an automated sequence, the rest as the control arm; the band once enough of those have matured labels; prints the experiment so far |
| `python run.py costs [--revenium]` | what a demo costs by the way the lead was reached, with the service's own Claude calls metered per call; `--revenium` adds the product's cost from Revenium's analytics API (`REVENIUM_API_KEY`) |
| `python run.py queue-limits [--seeds 5]` | builds the demo's starting point, then replays one month per routing setup (freshness discount on or off, queue limit 3, 2 or 1) and seed in parallel; prints the speed and demos table with the range across seeds |
| `python run.py serve` | the pages: SDR queues, lead detail, manager's held queue, model & approvals; `/jobs/*` endpoints; `/webhooks/hubspot`; the jobs on timers (below) |
| `pytest -q` | the test suite (offline; no model calls) |

**The scheduler.** `run.py serve` also runs the jobs on timers inside the server: scoring of new or changed leads and routing every 5 minutes, first-touch detection, outreach and (after a model goes live) rescoring the pool every minute, reminders and outcome sync hourly, the channel monitor daily, and a candidate model every 30 days. Each run is one row in the Overview's "Recent runs", a failed job never stops the others, and training only produces a candidate; it still needs both approvals. Once both approve, the pool is rescored on the new model, best-ranked leads first, so the leads routing takes next carry the new number first. With Claude reading notes that is about five seconds a lead, so the whole pool takes a couple of hours; `run.py approve` runs it to the end. `AUXO_SCHEDULER=off` in `.env` turns it off, and `AUXO_EVERY_<JOB>_MIN` changes an interval (see `.env.example`). On the demo data the service clock is frozen, so leads never age into new holds or reminders there.

**The SDR works in HubSpot.** When a lead is assigned, the outreach job drafts the first email, adds the SDR's booking link, writes it to the lead (`auxo_email_subject`, `auxo_email_body`) and enrols the lead in the HubSpot sequence set by `AUXO_SEQUENCE_ID`, sending from the SDR's inbox. The sequence's first step is a manual email task built from those two properties, so the draft waits in the SDR's HubSpot tasks to be read, edited and sent. The first email or call the SDR logs in HubSpot counts as the first touch: within a minute the lead is marked worked, its first-touch time recorded, and the SDR's slot refilled, with no click in AuxoCase. The lead page shows that activity and drafts the follow-up or the last email from it; a lead that replied gets no draft. Calling instead? The queue and lead pages prepare a **call brief** on request, from the same record as the email: an opener, two or three questions drawn from what moved the score (a competing vendor becomes "when does that contract renew?", a blank company size becomes a size question), the objection to expect from the loss reasons read for companies of that size, the ask with the booking link, and a voicemail under 60 words. It is read, not recited, and checked by code like the emails. HubSpot's call outcome (connected, no answer, left voicemail, busy, wrong number) comes in with the first touch and shows on the lead page. On the mock, buttons on the lead page stand in for HubSpot (send, log a call with its outcome, the lead replies).

**Safe from other sites.** The pages answer only to the host names in `AUXO_ALLOWED_HOSTS` (this machine by default), every write coming from another site's page is refused, and every reply carries a content security policy and no-framing headers. Forms that act on a lead or approve a model carry the state the person saw; if someone else acted first or HubSpot moved the lead on, the action is not applied and the page says so. With the real HubSpot client, webhooks must be signed (`service/security.py`).

**The reader.** Free-text notes are read by Claude for buying signals. `AUXO_LLM=cli` uses the Claude Code CLI headlessly (`claude -p`), `AUXO_LLM=api` the Anthropic API, and the default `offline` a scripted reader so everything runs without a model. Whatever answers, code verifies every quoted signal against the notes, drops what isn't there, and caps the effect at ±10 points; the model never sets the score.

## Where things are

- `docs/blueprint/` — the brief and Blueprint's run: the split into three jobs, every interview answer, the result with what it got right and what it missed, the three starter kits; `brief-answers.md` has BrightPath's follow-up answers and what each changed.
- `docs/ARCHITECTURE.md` — the three jobs as modules, every departure from Blueprint's result with its reason, the safeguards kept.
- `docs/evaluation.md` — the numbers (written by `run.py eval`).
- `knowledge/` — the research: 12 chunks on HubSpot integration (52 sources) and 13 on predictive lead scoring and routing (32 sources, 25 peer-reviewed). `INDEX.md` first.
- `src/auxocase/` — the service. Module docstrings cite the knowledge chunks (e.g. L05) behind each choice.

## Going live in HubSpot

1. Create the `auxo_*` contact properties (`ensure_properties()` does it through the API; run it again after an upgrade that adds one) and a private app with the scopes in `docs/ARCHITECTURE.md`.
2. Set `HUBSPOT_PRIVATE_APP_TOKEN`; point a HubSpot webhook subscription (contact creation and property changes) at `/webhooks/hubspot` and set `HUBSPOT_APP_SECRET` (with the real client, unsigned webhooks are refused), or run `score` on a schedule to poll by last-modified. Add the deployment's host name to `AUXO_ALLOWED_HOSTS`.
3. Confirm the property internal names listed in `hubspot/real.py` against `GET /crm/v3/properties/contacts` on the portal; they vary by portal.
4. Train the first model on HubSpot's three years of history (the last 12 months, mature labels only; at least 60 demos), with the exploration slice on from day one. Set `stale_lead_factor` from the report's measured speed effect, and set the SDRs' `capacity_limit` to 1.
5. Connect the enrichment tool behind `scoring/enrich.py`'s `Enricher` interface (set `AUXO_ENRICH`), and run `losses` on the closed-lost history.
6. Create the SDRs' sequence: step one a manual email task whose subject and body are the `auxo_email_subject` and `auxo_email_body` contact tokens, later steps your own follow-ups. Set `AUXO_SEQUENCE_ID` to its ID, connect each SDR's inbox, and give the private app the email, call, sequence-enrolment and meeting-link scopes (`docs/ARCHITECTURE.md`). For phase 3, a second sequence whose step one is an automated email of the same two tokens, its ID in `AUXO_TIER2_SEQUENCE_ID`; leave it unset until the experiment is wanted.
7. Put the SDR queue in a HubSpot CRM card or a saved view sorted by `auxo_rank` with `auxo_reason` as a column; the service's pages do the same standalone.
