---
id: L09
title: Concept drift, monitoring cadence and training with delayed labels
topic: lead-scoring
sources: [gama-2014-drift-survey, lu-2018-drift-review, ktena-2019-delayed-feedback]
last_verified: 2026-10-27
---

## Claims

- Real concept drift is a change in p(y|X) (which may or may not come with a change in p(X)); virtual drift is a change in p(X) without a change in p(y|X); only real drift moves the decision boundary and makes the old model obsolete, and it "is not visible from the input data distribution" so detecting it requires feedback on predictive performance [gama-2014-drift-survey §2.1].
- Drift patterns are sudden/abrupt, incremental, gradual and reoccurring; outliers are not drift and need no adaptation; most adaptive methods implicitly assume sudden non-reoccurring drift while real data show mixtures [gama-2014-drift-survey §2.2].
- The online adaptive loop is predict, diagnose (after the true label arrives), update; the survey explicitly notes variations where feedback "comes with a delay or is not available at all" and new examples arrive before labels for earlier ones, in which case model update is delayed but the principle is unchanged [gama-2014-drift-survey §2.4].
- Adaptive systems need a memory/forgetting policy (which data to learn from, how old data are discarded), change detection, a learning mode, and loss estimation; evaluation uses holdout or prequential (interleaved test-then-train) error [gama-2014-drift-survey §3.1, §4.2].
- Lu et al. define drift as a change in the joint distribution P_t(X, y) and organise handling into detection, understanding and adaptation; detection algorithms are error-rate based (DDM, EDDM, ADWIN), data-distribution based (distance metrics on windows, higher cost but can locate the drift) or multiple-hypothesis tests [lu-2018-drift-review §II-A, §III-B].
- Adaptation strategies are retraining a new model on recent data (often with an adaptive window), keeping an ensemble of models for recurring drift, or partially adjusting an existing model for regional drift [lu-2018-drift-review §V].
- The review flags "high verification latency" (labels arriving late) as a setting where methods that need fewer true labels are useful, and lists learning when true labels are limited or unavailable as an open problem [lu-2018-drift-review §III-B3, §VII-C].
- In continuously trained conversion models, positive labels arrive after a long random delay; treating a not-yet-converted example as negative ("fake negative") biases the predicted rate downward; five losses were compared (log loss, fake-negative weighted, fake-negative calibration, positive-unlabeled, delayed-feedback loss), the latter two groups relying on importance sampling or an explicit delay model [ktena-2019-delayed-feedback abstract, §3.2].
- Offline, the best delayed-feedback losses improved relative cross-entropy by 3% over the prior state of the art on 668M examples, and online the top methods yielded a 55% revenue-per-request gain over naive log loss [ktena-2019-delayed-feedback abstract].

## Implications for the build

- Run two monitors: a label-free one on p(X) (channel mix, firmographic mix, behaviour volumes per week, via a distribution distance on sliding windows) and a label-based one on prequential error of the demo model once labels mature; alert on either, retrain only on the second or on a planned cadence.
- Define label maturity windows explicitly: demo-booked is observable within ~30 days of MQL, closed-won within the typical sales cycle (months); never include leads younger than the window as negatives in training (they are "fake negatives").
- For the close model, either train only on cohorts past the maturity window or use a delayed-feedback / PU-style loss that models the delay; document which one is used and the assumed delay distribution.
- Retrain the demo model on a fixed monthly cadence with a sliding window of ~12 months, plus an unscheduled retrain when the error-rate detector fires; keep the previous model as a shadow for one cycle to catch regressions.
- Keep recurring-concept awareness: seasonality (quarter-end, summer) is reoccurring drift, so store models per season and compare before retraining from scratch.
