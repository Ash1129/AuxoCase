---
id: L11
title: Fairness proxies and personal data in B2B lead scoring
topic: lead-scoring
sources: [barocas-2016-disparate-impact, gdpr-art22, wp29-2018-adm-guidelines, wachter-2018-counterfactual]
last_verified: 2026-10-27
---

## Claims

- Criteria that are genuinely relevant to a decision can also be reliable proxies for protected-class membership ("redundant encodings"), so a model can produce systematically less favourable outcomes for a protected group even when protected attributes are removed, the data are free of prejudice and the only objective is accuracy; more precise mining exposes such distributions more, not less [barocas-2016-disparate-impact pp. 691-692].
- Labelling choices and target-variable definitions are subjective and skew everything downstream; where a class is defined by thresholding a continuous quantity the threshold itself is a design decision [barocas-2016-disparate-impact pp. 677-682].
- Article 22(1) GDPR gives the data subject the right not to be subject to a decision "based solely on automated processing, including profiling, which produces legal effects concerning him or her or similarly significantly affects him or her"; exceptions are contract necessity, legal authorisation or explicit consent, and in the contract/consent cases the controller must provide at least the right to human intervention, to express a view and to contest the decision [gdpr-art22 paras 1-3].
- The Working Party reads Article 22(1) as a general prohibition, not a right to be invoked; "solely" means no meaningful human involvement, and a human who routinely applies automated outputs without actual influence does not count, so oversight must be by someone with "the authority and competence to change the decision" and the degree of involvement should be recorded in the DPIA [wp29-2018-adm-guidelines pp. 19-21].
- "Similarly significant" effects are those that significantly affect a person's circumstances, behaviour or choices, have a prolonged impact, or lead to exclusion or discrimination; the examples given are credit, health services, employment and education decisions, and many targeted-advertising decisions will not meet the threshold [wp29-2018-adm-guidelines pp. 21-22].
- Profiling is any automated processing of personal data to evaluate personal aspects of a natural person; it is covered by the GDPR whether or not Article 22 applies, so transparency, lawful basis (often legitimate interest with a balancing test) and the right to object to direct-marketing profiling apply to B2B contacts who are natural persons [wp29-2018-adm-guidelines Chapter III, pp. 15-17].
- Articles 13-15 require "meaningful information about the logic involved" and the significance and envisaged consequences; the guidance says this does not require disclosing the full algorithm but must be sufficient for the person to understand the reasons for the decision [wp29-2018-adm-guidelines p. 25].
- Counterfactual explanations (the smallest change that would alter the outcome) can be offered without revealing model internals and are proposed as the way to meet understand / contest / alter aims for data subjects [wachter-2018-counterfactual, abstract].

## Implications for the build

- Audit features for proxies before training: region, company size, job title and language-of-content features can encode nationality or gender of the contact; run a per-group outcome check (score distribution and worked-rate by inferred group) and document it, even though B2B scoring is unlikely to be a "similarly significant" decision.
- Keep the decision human: the model ranks and recommends, an SDR or manager decides who is worked, and the UI must make the human step real (visible reasons, an override control that is used), so that Article 22 does not bite and the oversight is "meaningful" in the Working Party's sense.
- Practise data minimisation at the feature layer: score on company-level firmographics and account-level behaviour where possible, keep contact-level personal data (name, email, title) out of the feature matrix, and set a retention window on behavioural logs.
- Prepare a per-lead "why ranked here" statement (reason codes + one counterfactual) that could be shown to the contact on request; it satisfies the "logic involved" guidance and doubles as the rep explanation (L08).
- Record the lawful basis and the balancing test for behavioural tracking of B2B contacts in the project docs, and honour objections to direct-marketing profiling by excluding the contact from behavioural scoring.
