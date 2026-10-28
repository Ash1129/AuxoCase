---
id: L08
title: Explaining a rank to a sales rep: SHAP, reason codes, counterfactuals
topic: lead-scoring
sources: [lundberg-2017-shap, binns-2018-justice, wachter-2018-counterfactual, wp29-2018-adm-guidelines]
last_verified: 2026-10-27
---

## Claims

- Additive feature attribution methods explain a prediction as a sum of per-feature contributions; SHAP values are the unique attribution satisfying local accuracy (contributions sum to the prediction), missingness (absent features get zero) and consistency (if a feature's marginal contribution never decreases when the model changes, its attribution does not decrease) [lundberg-2017-shap §2, §3, Theorem 1].
- Kernel SHAP estimates SHAP values model-agnostically with fewer model evaluations than Shapley sampling, and LIME's values can differ significantly from SHAP values that satisfy local accuracy and consistency [lundberg-2017-shap §4, §5.1].
- In Mechanical Turk studies where participants assigned credit for a simple model's output, SHAP agreed much more strongly with human explanations than LIME or DeepLIFT [lundberg-2017-shap §5.2].
- In three experiments on algorithmic decisions (loans, insurance, CV filtering) with four explanation styles (input influence, sensitivity, case-based, demographic), explanation style changed perceived fairness only when people saw several styles side by side; case-based explanations ("people like you were rejected") consistently lowered perceived appropriateness and fair process compared with sensitivity-style explanations, and with repeated exposure to one style the scenario dominated the explanation [binns-2018-justice, Results; Discussion].
- Participants reacted to statistical explanations with concerns about arbitrariness, generalisation and dignity ("reducing a human being to a percentage") [binns-2018-justice, abstract and qualitative results].
- A counterfactual explanation states the smallest change to the inputs that would have produced a different outcome; the authors argue explanations should serve three aims, to understand the decision, to contest it, and to learn what to change for a better future outcome, and that counterfactuals serve all three without opening the black box [wachter-2018-counterfactual, abstract and §I].
- GDPR Articles 13-15 require "meaningful information about the logic involved" and the significance and envisaged consequences of processing, which the authors and the Article 29 Working Party read as a general overview of system functionality rather than a per-decision explanation of internal logic [wachter-2018-counterfactual §V.A; wp29-2018-adm-guidelines p. 25].
- The Working Party says controllers should find "simple ways to tell the data subject about the rationale behind, or the criteria relied on in reaching the decision", and that complexity is no excuse for not explaining [wp29-2018-adm-guidelines p. 25, fn. 40].

## Implications for the build

- Compute Tree SHAP (or Kernel SHAP for the LR baseline) for every scored lead and store the top-k signed contributions with the score; derive plain-English reason codes from a fixed template per feature ("Came in via partner referral (+)", "No site visit in 30 days (−)").
- Prefer sensitivity / input-influence phrasing over case-based phrasing in the rep UI: Binns et al. found case-based comparisons ("similar leads rarely booked") were judged least fair, and reps will be comparing leads side by side, which is exactly the condition under which style matters.
- Add one counterfactual line per lead where cheap to compute ("would move into the top 500 if the contact replied to the sequence email"), since it tells the rep what action would change the rank, which is what Wachter et al. argue explanations are for.
- Keep the explanation at the level of criteria and direction, not model internals; that is what the regulator's guidance asks for and what reps can act on.
- Log which explanation the rep saw with each decision (worked / skipped / overridden) so explanation formats can be A/B tested on adoption and on outcomes rather than on preference surveys.
