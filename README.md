# AuxoCase

Predictive lead scoring, capacity-aware routing and an outcome feedback loop for HubSpot, built for the BrightPath Payroll case: 6,000 leads a month, 1,500 MQLs, 12 SDRs who can work 500, and a points model that under-scores the two channels that convert best.

Every lead gets **one calibrated number** ("62" means about a 62% chance of booking a demo if an SDR works it), a **rank in the current pool**, a **plain-English reason** with the exact words from the lead's notes that moved it, and an **SDR assignment within capacity**. Which leads booked and closed flows back monthly into a retrained model that goes live only when **one person from marketing and one from sales approve it**, with its numbers beside the live model's. SDRs still make the final call on every lead, and every call is logged.

> Status: a working system on synthetic data against a mock HubSpot that has HubSpot's semantics (webhooks that duplicate and reorder, batch writes with per-record status, compare-and-set owners, property history). The real-API client is written from the official docs but has not been run against a live portal; see `src/auxocase/hubspot/real.py`.

## The numbers so far (synthetic data, `docs/evaluation.md`)

On 14 months of BrightPath-like history plus three simulated months with the system in place:

| Ranker, at K = 500 (one month of SDR capacity) | Precision@K |
|---|---|
| AuxoCase live model (`demo-v1`, logistic regression, Platt-calibrated) | **0.76** |
| The service's own channel-aware points table (cold start) | 0.74 |
| HubSpot's current points model (title, size, pages) | 0.40 |
| Random (base rate among worked leads) | 0.41 |

Time-ordered validation before any simulated month: precision@305 0.518 vs 0.416 for HubSpot's points (lift 1.24×), ECE 0.016, referral and webinar predicted at 0.66 and 0.49 against 0.28 for paid ads, the brief's complaint. Calibration holds across bins (predicted 0.3–0.4 → observed 0.344; 0.7–0.8 → 0.739). In the simulated months the ranked slice booked 72% of worked leads against 26% in the random exploration slice, which is why the exploration slice exists: without it the model would learn only from leads it chose.

Two honest caveats. The generator's hidden truth rewards the same things the points table rewards, so the gap between the trained model and the cold-start table is small here; on real data the model earns its keep on the interactions a table can't hold (title × size × channel). And the speed-to-lead median of about a month is what 45 open leads per rep at 42 worked a month implies (Little's law), not a bug in the SLA code: the first fix for BrightPath is a smaller open-lead limit, which the manager page makes visible.

## Run it

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install -e .
python run.py demo            # seeds 14 months of history, trains and approves a model, scores, routes, serves http://127.0.0.1:8000
```

Step by step:

| Command | What it does |
|---|---|
| `python run.py seed` | 14 months of BrightPath-like history in the mock, with the SDRs' cherry-picking baked in |
| `python run.py sync` | pulls meetings, deal stages and lead status into outcome labels (booked / worked-no-demo / not-worked) |
| `python run.py train` | trains a candidate on 12 months of mature labels; prints precision@500, lift over the points model, AUC-PR, ECE, by channel |
| `python run.py approve demo-v1 marketing "Name"` then `... sales "Name"` | the two-team approval; both → live |
| `python run.py score [--all]` | scores changed leads from the change feed (or the whole pool) and writes `auxo_*` properties back |
| `python run.py route` | assigns within capacity, keeps an 8% exploration slice, holds the rest, creates tasks |
| `python run.py monitor` | the per-channel p-chart of observed vs predicted demo rate |
| `python run.py simulate --months 2` | replays months with the system in place: new leads, scoring, routing, SDR actions, outcomes, retrain |
| `python run.py eval` | writes `docs/evaluation.md` |
| `python run.py serve` | the pages: SDR queues, lead detail, manager's held queue, model & approvals; `/jobs/*` endpoints; `/webhooks/hubspot` |
| `pytest -q` | the test suite (offline; no model calls) |

**The reader.** Free-text notes are read by Claude for buying signals. `AUXO_LLM=cli` uses the Claude Code CLI headlessly (`claude -p`), `AUXO_LLM=api` the Anthropic API, and the default `offline` a scripted reader so everything runs without a model. Whatever answers, code verifies every quoted signal against the notes, drops what isn't there, and caps the effect at ±10 points; the model never sets the score.

## Where things are

- `docs/blueprint/` — the brief and Blueprint's run: the split into three jobs, every interview answer, the result with what it got right and what it missed, the three starter kits.
- `docs/ARCHITECTURE.md` — the three jobs as modules, every departure from Blueprint's result with its reason, the safeguards kept.
- `docs/evaluation.md` — the numbers (written by `run.py eval`).
- `knowledge/` — the research: 12 chunks on HubSpot integration (52 sources) and 13 on predictive lead scoring and routing (32 sources, 25 peer-reviewed). `INDEX.md` first.
- `src/auxocase/` — the service. Module docstrings cite the knowledge chunks (e.g. L05) behind each choice.

## Going live in HubSpot

1. Create the `auxo_*` contact properties (`ensure_properties()` does it through the API) and a private app with the scopes in `docs/ARCHITECTURE.md`.
2. Set `HUBSPOT_PRIVATE_APP_TOKEN`; point a HubSpot webhook subscription (contact creation and property changes) at `/webhooks/hubspot` with `HUBSPOT_APP_SECRET` for signature checks, or run `score` on a schedule to poll by last-modified.
3. Confirm the property internal names listed in `hubspot/real.py` against `GET /crm/v3/properties/contacts` on the portal; they vary by portal.
4. Run the points model for the first weeks while outcomes accumulate, with the exploration slice on from day one, then train; the first model needs at least 60 demo-booked leads with mature labels.
5. Put the SDR queue in a HubSpot CRM card or a saved view sorted by `auxo_rank` with `auxo_reason` as a column; the service's pages do the same standalone.
