# Blueprint result for the BrightPath brief (2026-10-27)

Run: Blueprint local dev server, AI on (Claude Haiku first, Sonnet on a failed check).
Short brief (985 characters), split into three jobs and continued as three. The system
map titled itself "Score, route and learn from inbound leads in HubSpot for BrightPath
Payroll": 3 jobs, 2 handoffs (both through HubSpot), people step in at 4 points, running
cost estimate $200 to $3,300 a month.

The system link (3,600 characters; the `p=` value holds every answer) is in
`system-link.txt`. The three kits, trimmed of the generic engineering guides, the delivery
contract and the n8n JSON, are in `kits/`.

## What Blueprint recommended

| Job | Approach | Main tool | Models | Autonomy | Cost / build |
|---|---|---|---|---|---|
| 1 Score and rank | Workflow with AI steps, 12 steps, routing + generator-and-checker | n8n | Sonnet 5.5 reads free text, Opus 5.5 checks | Level 3: approve the risky ones | $120–$2,500 a month; 1.5–5 weeks |
| 2 Route and alert | Plain automation, no AI, 11 steps, fixed pipeline | n8n | none | Level 4: runs within guardrails | $55–$730 a month; 1–3 weeks |
| 3 Feed outcomes back | Workflow with AI steps, 11 steps, routing + generator-and-checker | n8n | Sonnet 5.5 drafts, Haiku 5.5 checks; a p-chart per source | Level 2: approve every output | $22–$23 a month; 1.5–5 weeks |

Where the AI decision departed from the rules: job 1 runs in the cloud throughout (the
rules suggested moving steady bulk to local hardware later) and its checker is Opus; job 3's
checker is Haiku rather than the rules' Opus, with the stated reason that the checker must
differ from the worker. Job 2 agreed with the rules on every choice. Job 3's shortlist named
Airflow as the only alternative.

### Job 1 design (score and rank)

Lead created or updated → check the record is complete (incomplete leads tagged, not
scored) → sort by lead channel (partner referral, customer-success handoff, webinar
registrant, webinar attendee, other inbound; unknown source to a person) → apply a shared
points table (channel, job title and seniority, company size, country, past visits,
webinar attendance, open deal), writing out each rule's points → Sonnet reads form answers,
webinar questions and notes for buying signals, each with the exact quoted words and a fit
adjustment between -10 and +10 → deterministic merge: points plus adjustment, capped 0–100,
stamped with rules and reader versions; malformed AI output falls back to the rule score →
Opus checks the score against a checklist (quotes really appear in the record, adjustment
matches the signals, score in range, notes don't contradict fields) → does a person need to
look? (checker flagged, score in the band near the hot-lead cut-off, or partner referral
above a deal size) → sales rep reviews flagged leads (confirm, change with a note, send
back) → save score, version, reason and timestamp to the contact, keyed by contact ID and
version; rank is a saved HubSpot view sorted by score.

### Job 2 design (route and alert)

New scored lead → required fields present? (contact ID, numeric score, territory) → read
the SDR roster, open-lead counts, limits and the round-robin position (all pages) → find
the next SDR under capacity, starting after the last assigned → set owner and advance the
turn, checking the lead has no owner first so a retry can't assign twice → create a
HubSpot task for the SDR → if everyone is at capacity, hold the lead with a timestamp for
the sales manager's queue; the manager assigns or raises a limit with a reason.

### Job 3 design (feedback loop)

Scheduled start → pull deals that reached demo booked, closed won or closed lost since
the last run, with source fields → completeness and duplicate check (failures to a review
queue, never guessed) → sort by source (partner referral, webinar, other) with fixed field
rules → p-chart per source for demo-booked and closed-won rates with control limits from
history; a source with fewer than 30 deals in the period is held → Sonnet drafts a
plain-English proposal of weight changes using only the computed figures → Haiku checks
every figure against the computed rates (the kit's pitfalls say the number matching must
be code, not the model) → one named approver from marketing and one from sales approve each
source's change one at a time, no batch approval; a send-back cancels only that source's
write → write the approved weight per source to one shared score property, logged with
approver names and proposal version.

## What it got right (used in the build)

- **Routing by channel first**, with HubSpot's own original-source fields as the sorting
  key so the system agrees with how marketing already reports.
- **An explainable score**: every rule's points written out; AI contributes only a bounded
  adjustment; every AI signal must quote words that are really in the record; reason text
  is short, in fixed wording, and never repeats contact details.
- **A checker tied to the record**, not a second opinion; malformed AI output falls back
  to the deterministic part; unsure goes to a person.
- **Review where a wrong score costs most**: the band around the cut-off and large partner
  referrals, with the rep's confirm/change/send-back captured as data.
- **Idempotent writes** keyed by contact and version, and the warning that writing the
  score can re-fire the trigger that started the run.
- **Capacity-aware round robin** with the owner check before writing, serialisation of
  the routing step, a held queue that is someone's named job, and the failure mode "owner
  set but SDR never alerted".
- **A control chart before any weight moves**, a minimum group size, two approvers (one
  per team), per-source approval, no individual named in a proposal, least-privilege
  credentials for the write, and a retry-safe set-not-add write.
- **Good owner questions**: what counts as an open lead, each SDR's limit, the hot-lead
  cut-off and the band around it, the partner deal-size gate, whether notes need masking,
  12 months of outcomes to set control limits, which webinar campaigns count.

## What it missed against the brief

1. **No learned model.** The brief asks for scores that learn from which leads booked and
   closed. Blueprint turned "memory of past work" into a reference library of past
   examples for the prompt, and the feedback loop into a few-times-a-week adjustment of
   three channel weights after a p-chart. The score itself is a hand-tuned points table
   with an LLM nudge: a better-governed version of the HubSpot points model the brief says
   is wrong. Blueprint's catalog has no "trained statistical model" method (it has control
   chart, capability index, reliability model and risk ranking), so a design can't reach
   logistic regression or gradient boosting, calibration, or retraining.
2. **No probability, so no "one shared number".** The brief asks for likelihood to book a
   demo and close. A 0–100 points total isn't a probability and can't be compared with the
   booking rate marketing and sales both see. The research knowledge base (L-series) says a
   calibrated probability is what lets two teams read the same number the same way.
3. **The capacity cut-off is missing from the scoring design.** 12 SDRs work about 500 of
   1,500 MQLs. The quantity that matters is precision (or lift) in the top 500 by score,
   and the rank should be recomputed as the pool changes. Blueprint's rank is "a saved
   HubSpot view sorted by score" and no metric is named.
4. **Selection bias in the feedback loop.** Outcomes exist only for leads SDRs chose to
   work; job 3 measures rates among deals, which are the worked leads. Nothing holds out a
   random slice or records that a lead was never worked, so a model trained on this data
   would learn the SDRs' past preferences. Not a gotcha anywhere in the result.
5. **Label delay.** Closes arrive months after the lead; a scheduled run that compares
   this period's rates will see demo bookings long before closes. The design treats both
   as one feed.
6. **Speed to lead.** Good leads "sit untouched for days and go cold" is in the brief;
   routing has no time-to-first-touch target, no re-alert, no ageing of held leads beyond
   a timestamp.
7. **An invented required field.** Job 2 requires a territory or region value and flags
   leads without one, although the brief says the business has no geographic restriction
   and the answer was "round-robin only". Every lead would be flagged incomplete.
8. **A number drifted in the split.** "Each SDR can work about 500 leads a month" (the
   brief: 12 SDRs together work about 500). Carried through job 2's description and its
   capacity reasoning.
9. **SDR overrides as feedback.** Reps make the final call on every lead, and "worked / not
   worked / rejected as junk" is itself an outcome the model should learn from. Only the
   flagged-lead review captures a decision.
10. **"Decides or recommends something about a person"** wasn't ticked by the AI
    interview for a system that ranks identifiable people for a sales call. The result
    therefore has no identity-stripping step before the AI reads notes, and no
    across-group outcome comparison at launch, both of which AU12 to AU14 would have added.
11. **Kit text that doesn't fit a statistical task.** The owner's first step "write the
    playbook in plain language and run it in Claude on your examples with the most capable
    model" applies to a reading task, not to a scoring model; and job 3's BUILD.md, copied
    before tailoring finished, embeds "n8n/workflow.json: Not included yet" without saying
    so at the top.

## How the build departs from the result

- One Python service instead of three n8n workflows, because the scoring step is a trained
  model with a retraining loop and the brief's team has developers. The three jobs survive
  as three modules with the same boundaries and the same HubSpot handoffs (score and reason
  written to contact properties; owner and task for routing; outcomes read from deals and
  meetings), so an n8n or HubSpot-workflow front end could still call them.
- The score is a calibrated probability of booking a demo from a logistic-regression or
  gradient-boosted model on channel, firmographics and behaviour, with the points table
  kept as the cold-start baseline and the explanation layer (per-feature contributions,
  reason codes in plain English).
- The capacity cut-off is first class: the rank is the position in the current pool, the
  report shows precision and lift at 500, and a small random holdout of MQLs below the
  line is routed anyway so the next model has unbiased labels.
- The LLM (Claude via the `claude` CLI in development) does what Blueprint gave it: read
  free text for quoted buying signals with a bounded adjustment, and write the
  plain-English reason; it never sets the score.
- Everything Blueprint's safeguards asked for stays: completeness checks, idempotent
  writes, owner-check before assignment, held queue, two-approver model promotion with a
  control chart and minimum group size, no contact names in proposals, override logging.
