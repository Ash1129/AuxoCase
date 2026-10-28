# Blueprint split (AI) for the BrightPath brief (2026-10-27)

Run: Blueprint local dev server, AI on (Claude Haiku first, Sonnet on a failed check).
Input: `brief-short.txt` (985 characters). The full brief (`brief.txt`, 3,236 characters)
is over Blueprint's 1,000-character limit for a single job, and "Keep it as one job" on a
long brief still designs from a slice (Blueprint open item 23), so the short brief was
used from the start.

"This is three jobs."

1. **Score and rank every new or updated HubSpot lead by its likelihood to book a demo and close.**
   Read lead and activity data from HubSpot continuously as hundreds of new leads arrive
   daily from six marketing channels (about 6,000 leads a month). Score each lead and rank
   it by likelihood to book a demo and close, replacing HubSpot's points-based model, which
   currently passes about 1,500 MQLs a month and misranks leads. Partner referrals and
   webinar leads convert 2-3x better but score low today, so the model must weight them
   correctly rather than penalize them. Write the score, rank and a plain-English reason for
   each score back into HubSpot. Leads are business contacts, so personal data must be
   handled under applicable privacy law and used only for this purpose. Budget is
   $100-300k, with go-live in HubSpot within three months.
   - Hands over through HubSpot when a lead is scored and ranked and its score is written
     back to its HubSpot record.
2. **Route each scored lead to an SDR within capacity and alert them.**
   Take each newly scored lead from the scoring step and assign it to one of 12 SDRs in
   HubSpot. Each SDR can work about 500 leads a month in total, so the system must respect
   capacity and prioritize the highest-ranked leads so that good leads are not left
   uncontacted, as about 1,000 are today. Replace the current gut-feel selection with rules
   based on rank, capacity and fit. Alert the assigned SDR and write the routing decision
   back into HubSpot. SDRs make the final call on who to contact, so the system assigns and
   alerts but never contacts a lead itself. Leads are personal data and must be handled
   under applicable privacy law.
   - Hands over through HubSpot when a scored lead is assigned to an SDR and the routing
     decision is written back, creating a meeting or close outcome later.
3. **Feed demo-booked and closed outcomes back into the scoring model so marketing and sales share one number.**
   Collect outcomes from HubSpot showing which leads booked meetings and which closed as
   customers, then use them to recalibrate the lead-scoring model over time. Compare
   predicted scores with actual results by channel, including partner referrals and
   webinars, and report where the model is over- or under-scoring. Results must be visible
   to both marketing and sales so they share one scoring number. Changes to the model should
   be reviewed before they take effect in live routing. Outcome data concerns identifiable
   business contacts and must be handled under applicable privacy law.

Choice made: **Continue with 3 jobs.** The split matches the three cadences in the brief
(score on arrival, route within capacity, learn from outcomes) and the handoffs all go
through HubSpot, which is what the brief asks for.

Observations:

- One factual slip: job 2 says "Each SDR can work about 500 leads a month in total"; the
  brief says the 12 SDRs together work about 500 a month (roughly 42 each). Carried into
  the interview as written; corrected in the build.
- The split invented nothing: no new system, no extra store; every handoff is a HubSpot
  property write.
