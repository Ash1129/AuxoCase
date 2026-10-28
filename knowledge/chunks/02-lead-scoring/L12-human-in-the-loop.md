---
id: L12
title: Human-in-the-loop: algorithm aversion, automation bias and overrides
topic: lead-scoring
sources: [dietvorst-2015-aversion, dietvorst-2018-overcoming, bucinca-2021-cognitive-forcing, parasuraman-2010-automation-bias, wp29-2018-adm-guidelines]
last_verified: 2026-10-27
---

## Claims

- People are especially averse to algorithmic forecasts after seeing them perform, even when the algorithm outperforms a human, because they lose confidence in an algorithm faster than in a human after seeing the same mistake; across five incentivised studies, participants who saw the algorithm err were less likely to choose it over an inferior human forecaster [dietvorst-2015-aversion abstract].
- Letting people modify an algorithm's forecast, even by a severely restricted amount, made them considerably more likely to choose the algorithm and to perform better; the decision to use it was insensitive to how much adjustment was allowed, and restricted adjustment led to better long-term performance than unlimited adjustment [dietvorst-2018-overcoming abstract; Studies 1-4].
- Giving people freedom to modify the algorithm also made them more satisfied with the process, more tolerant of its errors and more likely to believe it superior [dietvorst-2018-overcoming abstract].
- In AI-assisted decision making people over-rely on the AI, accepting its suggestion even when wrong; simply adding explanations did not reduce over-reliance and may increase it; cognitive forcing functions (e.g. making people decide first, or delaying the AI suggestion) reduced over-reliance significantly more than two simple explainable-AI designs, but were rated least favourably by participants, and helped high Need-for-Cognition users most [bucinca-2021-cognitive-forcing abstract].
- Automation complacency arises under multi-task load when manual tasks compete with the automated task for attention and is not removed by practice; automation bias produces omission errors (missing an event the aid did not flag) and commission errors (following a wrong recommendation) when aids are imperfect, in both novices and experts, and cannot be prevented by training or instructions alone [parasuraman-2010-automation-bias abstract].
- For human involvement to count as oversight, the reviewer must have the authority and competence to change the decision and consider all relevant data; the controller should "identify and record the degree of any human involvement in the decision-making process and at what stage" [wp29-2018-adm-guidelines p. 21].

## Implications for the build

- Give SDRs a bounded override: let them promote or demote a lead by a limited number of rank positions or re-order within a band, rather than an unlimited free-form list; Dietvorst et al. show even a small, bounded adjustment buys adoption and protects performance.
- Expect aversion to spike after visible misses (a top-ranked lead that no-shows); show the model's hit rate for the period alongside the miss so the comparison is against the points model's record, not against perfection.
- Do not rely on explanations alone to stop over-reliance; add a light cognitive-forcing step for the first weeks (rep marks their own pick before the rank is revealed on a sample of leads) and drop it once logs show calibrated reliance.
- Log every override with: lead id, model score and rank, the rep's action (promote/demote/skip), the reason code chosen from a short list plus free text, the explanation shown, timestamp, and the eventual outcome; this is both the Working Party's "degree of human involvement" record and the training signal for learning from reps.
- Report override rate and override accuracy (did overridden leads book more than the leads they displaced?) monthly; a very low override rate signals complacency as much as a very high one signals aversion.
