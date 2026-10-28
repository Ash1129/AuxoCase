---
id: L07
title: Lead response time, routing rules and the queueing view of 12 SDRs
topic: lead-scoring
sources: [lrm-study-oldroyd, oldroyd-2011-hbr, aksin-2007-callcenter, jarvinen-2016-automation]
last_verified: 2026-10-27
---

## Claims

- The InsideSales.com / Oldroyd lead response study analysed three years of data from six companies (over 15,000 web-generated leads, over 100,000 call attempts) and reports that the odds of contacting a lead fall more than tenfold within the first hour, and that the odds of qualifying a lead fall 21-fold between a 5-minute and a 30-minute response and more than sixfold over the first hour [lrm-study-oldroyd, findings and methodology].
- The same study reports best-performing contact windows (4-6 PM; 8-9 AM for qualification) and that additional call attempts after about 20 hours were associated with lower contact success [lrm-study-oldroyd, findings].
- The 2011 HBR follow-up audit's published introduction states that "most companies are not responding nearly fast enough" to online leads; the detailed audit figures sit behind the paywall and were not read [oldroyd-2011-hbr, introduction].
- The simplest queueing model of an inbound centre is the M/M/s (Erlang-C) queue; inbound managers typically want both short waits and high agent utilisation ("quality and efficiency driven"), and the square-root safety-staffing rule says staffing at R + β√R servers (R = offered load = arrival rate × mean service time) achieves both [aksin-2007-callcenter §2.3.1, p. 669].
- Routing in multi-skill settings (skills-based routing) determines how effectively staff are used, staffing and routing decisions are hierarchically dependent, and even in isolation optimal routing is hard; linear programming, fluid/diffusion approximations and simulation-plus-optimisation are the standard approaches [aksin-2007-callcenter §2.3.3, p. 671].
- Marketing-generated leads are frequently left unworked because reps judge them to lack potential, and poor follow-up loses sales-ready buyers; the authors call for qualification methods that are "transparent to sales representatives" [jarvinen-2016-automation p. 11].

## Implications for the build

- Rank and route in the same pass: a high-probability lead that waits a day for a human to pick it has already lost most of its value according to the only response-time evidence available, so routing must be event-driven on MQL creation, not a daily batch.
- Model the 12 SDRs as an s=12 service system: with ~1,500 arrivals and ~500 served per month the system is overloaded by design, so the routing problem is admission control (which 500) plus assignment, not waiting-time minimisation; publish the implied monthly offered load and utilisation per SDR.
- Default to capacity-aware assignment (each SDR has a daily/weekly slot budget; a lead goes to the eligible SDR with the most remaining capacity and matching territory/segment skill), fall back to weighted round-robin only among equally eligible reps, and never assign to a rep marked unavailable.
- Log timestamps for MQL-created, routed, first-touch and demo-booked so speed-to-lead can be measured per channel and per SDR and later entered as a feature in the close model.
- Caveat for the docs: the response-time evidence is vendor/practitioner-grade (Tier D); no peer-reviewed replication was found, so present it as a strong prior to be checked against the company's own first-touch data.
