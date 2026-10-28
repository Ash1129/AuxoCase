# Knowledge base

The research behind AuxoCase. Every design decision in the service traces to a chunk here, and every chunk traces to a source that was actually read (see `sources.md`, with tiers and read dates). Format and conventions follow the Blueprint knowledge base.

```
knowledge/
  INDEX.md               one line per chunk: start every lookup here
  sources.md             every source, stable ID, tier, read date; blocked sources are listed, never cited
  chunks/01-hubspot/     H01-H12: the CRM API, webhooks, limits, routing, outcomes, auth, privacy, test accounts
  chunks/02-lead-scoring/ L01-L13: predictive scoring, evaluation at the capacity line, calibration, selection bias, routing, explanations, drift, alignment, fairness, overrides, multi-objective
```

Chunk frontmatter: `id`, `title`, `topic`, `sources`, `last_verified`. Each chunk has `## Claims` (one fact per bullet, each with a bracketed citation) and `## Implications for the build`. Module docstrings in `src/auxocase` cite chunk ids (e.g. "L05") where a choice comes from one.
