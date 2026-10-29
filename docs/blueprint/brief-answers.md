# Follow-up answers from BrightPath (received 2026-10-28)

The questions were asked after the first build. The answers are verbatim; under each is what it changed.

## Data and funnel

**1. Channels.** Average monthly lead volume: website forms 35%, content and webinars 25%, paid ads 20%, events 10%, partner referrals 10%. Partner referrals and webinar attendees close at 2 to 3 times the average rate but score low, because their forms capture little data (no title, no company size).

> The generator's channel mix follows it (content and webinars split as webinars 15%, email nurture 10%), and referral and webinar forms now leave title and size blank most often. That is the mechanism behind the complaint: HubSpot's points model gives nothing for a blank, so these leads score low and often never reach MQL. The generator is checked against the answer itself: on its hidden truth, referrals close at 2.4x the average and webinar attendees at 2.2x.
>
> The model keeps a blank as its own value (`seniority=unknown`, `size_band=unknown`), so a missing title is not read as a junior one. The evaluation now has a table of how often each channel reaches MQL and how often its leads miss each field.

**2. History.** 3 years of closed-won and closed-lost data in HubSpot. Loss reasons are free text and messy. About 40% of leads are missing job title or company size. A data enrichment tool is licensed but used inconsistently.

> `run.py seed` now generates 36 months. Training still uses the last 12 months (older leads describe an older market); the close label and the loss reasons use what is there.
>
> Loss reasons: `llm/loss_reasons.py` reads each free-text reason into one of seven fixed categories (price, competitor, timing, missing feature, no decision, poor fit, other) and must quote the words that show it; code drops any category whose quote is not in the text. The categories are reported by company size. Nothing about a loss touches a lead's score.
>
> Enrichment: `scoring/enrich.py` asks the enrichment tool once for every pool lead missing a title or a size, writes back what it finds, leaves blank what it does not, logs both, and the lead's reason says which fields came from the tool. The tool plugs in behind one interface; on the mock a stand-in finds 60% of missing titles and 75% of missing sizes (an assumption until the tool's own match report is in).
>
> About 40%: the generated history has 41% of leads missing one or the other.

**3. Unworked MQLs.** The lead records remain dispositioned in HubSpot.

> The pool the service ranks now has an age window (30 days by default, `pool_max_age_days`). Older unworked leads stay in HubSpot with their disposition and drop out of the ranking, instead of piling up behind the new ones.

## Sales motion

**4. Speed to lead.** Median first response is 3 days. Leads contacted within a day convert at roughly double the rate.

> The history now has a 3-day median first touch, and a lead touched after its first day books half as often as one touched inside it.
>
> Routing changed with it, in two ways. First, demand runs at about three times capacity, so the pool always holds a backlog of good older leads. Ranked on probability alone they crowd out the new ones, so routing now orders the pool by probability × a freshness discount: `stale_lead_factor`, 0.5 once a lead is past its first day, per this answer. The evaluation measures that factor from outcomes, so it can be kept honest. Second, each SDR holds one waiting lead, refilled the moment they touch it. On the simulator, one month at the brief's capacity, averaged over five seeds with the range in brackets:
>
> | Freshness discount | Queue limit | Touched within 24 h | Demos |
> |---|---|---|---|
> | off | 3 | 15% (13–17%) | 209 (200–219) |
> | on | 3 | 30% (28–31%) | 223 (206–229) |
> | on | 2 | 50% (48–51%) | 249 (234–262) |
> | on | 1 | 64% (61–66%) | 281 (270–296) |
>
> `python run.py queue-limits --seeds 5` reproduces this: it builds the demo's starting point, then replays one month per setup with seeds 101–105 and the offline reader. On its own, the freshness discount doubles the share of leads touched within a day but adds only a few demos while each SDR holds three waiting leads (223 against 209, with overlapping ranges). The demos come from the small queues, which let the SDRs reach the leads it puts first while those leads are still fresh.
>
> Speed is now measured from the lead's creation to the first touch. Before this, the routed-lead figure subtracted a wall-clock assignment time from a simulated touch time, so the "1,148 hours" in the first report was not a measurement.

**5. Sales cycle.** There is a 30-day business cycle for deals.

> A close is now counted as known 45 days after the lead (it was 120), and the generator closes deals 10 to 40 days after the demo. Closed-won results become measurable inside the first quarter of running.

**6. Best customers.** Larger companies need more features and have bigger deal sizes.

> Deal amounts already scale with company size, and size is a feature of both the demo and the close model. The loss-reason table splits by size band, so "larger companies leave for missing features" can be checked against BrightPath's own closed-lost reasons rather than assumed.

## Constraints and success

**7. Systems.** Yes, HubSpot is the CRM for both teams, and HubSpot has relevant APIs / MCPs.

> No change. Scores and routing are written through the v3 API, which has the batch, multi-status and signature semantics the knowledge base documents. HubSpot's MCP server fits read-only use: for example, letting an SDR ask an assistant about a lead's history. It is not the write path.

**8. Guardrails.** Sales is nervous about hallucinations / unexplainable behavior.

> This confirms the design. No AI ever contacts a prospect. The only generative steps read text (notes, loss reasons), must quote the record, and are checked by code. The reason an SDR sees is a template filled from the model's own contributions. `AUXO_LLM=offline` turns the reader off, and the reader can never move a score by more than 10 points.

**9. Success.** No clear target but more SQLs and faster follow-up without adding SDR headcount.

> The evaluation now opens with exactly that, month by month at the same 12 SDRs and the same ~500 touches a month: demos booked, demo rate, median hours to first touch and the share touched within 24 hours. History months and routed months sit side by side.
