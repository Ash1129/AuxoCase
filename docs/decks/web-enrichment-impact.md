# Web enrichment: what changed, and what it does to the CEO deck

Read against `AuxoCase_CEO.pptx` (17 slides, 2 Nov 2026). Everything below is in the code now; the deck has not been rebuilt.

## What was built

A second enricher behind the same `Enricher` interface (`src/auxocase/scoring/web_enrich.py`). When a lead arrives without a job title or a company size, the licensed tool is asked first; for what it misses, a Claude reader with web search switched on looks the lead up and must answer with the value, the page it came from and a quote copied word for word from that page. Code then fetches the page itself and keeps the value only if the quote is on the page and carries the fact: the title quote has to name the person and the title, the headcount quote has to contain the number. Anything else is dropped and the lead stays blank, which the model scores as "unknown", never as junior. Public hosts only (no local or private addresses), one lookup per lead ever, the page kept is logged beside the value in `enrichment_log`, and the reader never touches HubSpot or a score. Switch on with `AUXO_ENRICH=<tool>,web`; offline it finds nothing, so the demo and its figures are unchanged. Eighteen test cases cover the keep/drop rules, the chain, the setting and the write-back; the whole suite passes (193).

The reader sends out the person's name, the company's name and its domain, and asks for a business role and a headcount: company facts and the role, nothing personal. The CLI runs the lookup with `WebSearch,WebFetch` and no other tool; every other task still runs with no tools at all. On the API path it is Anthropic's server-side web search, capped at four searches a lookup.

## Cost of running it

A lookup is one Sonnet call carrying search results, about 6,000 tokens in and 300 out, plus two or three searches. At the metered Sonnet rate that is about 2.5 cents of tokens and 2 to 3 cents of search (Anthropic bills web search at $10 per 1,000 searches), call it 5 cents a lookup.

| Who fills blanks | Lookups a month | Cost a month |
| --- | --- | --- |
| Tool first, web for its misses (recommended) | ~180 (about 600 leads a month miss a field; the tool finds ~70%) | ~$10 |
| Web only, no tool | ~600 | ~$30 |
| Worst case, 8 cents a lookup | ~600 | ~$45 |

Running cost goes from about $180 to about $190 a month; model spend per demo from about 13 cents to about 15. Nothing changes in the one-off $65,000: the code is written, and checking the first 50 kept values against their pages during the measured month is a few hours inside the existing 80-hour measured-month line. Caveat for the deck: the lookup is not simulated, so no demo or revenue figure moves; its effect is on the rank of the ~180 leads a month the tool misses, mostly referral and webinar leads, and the measured month reports its fill rate and how many answers code rejected.

## Slide by slide

| Slide | Change |
| --- | --- |
| 1 Executive summary | No figure changes. Notes: "uses five AI steps to take chores off the SDRs" stays; add "and a web lookup, quote-checked by code, for a missing title or size". |
| 2 The problem | Unchanged. The last sentence of the notes (referral and webinar forms leave title and size blank) is now the setup for slide 4's step 1. |
| 3 Our approach | The "AI to oversee" card gets a fourth line: "Fills a blank title or size from the web; code keeps it only with a quote from the page". The subtitle still holds: AI works at the edges, code checks everything it does. |
| 4 The ML layer | Step 1 "Enrich: fill missing title or size" becomes "Enrich: the licensed tool, then a web lookup for what it misses". Outline step 1 in gold as well as step 4, and change the strap "GOLD OUTLINE = ONLY AI STEP" to "GOLD OUTLINE = AI STEPS". Notes: after "Enrichment fills a missing job title or company size", add "the licensed tool first; what it misses goes to a Claude reader with web search, which must cite a page and a verbatim quote, and code fetches the page and keeps the value only if the quote is on it". |
| 5 Seven candidates | Unchanged. |
| 6 AI steps | Unchanged: the table is SDR chores and the lookup is not one. Optional footnote: "A sixth AI use, the web lookup for blanks, feeds the model and saves no SDR time." |
| 7 Live demo | Unchanged. With `AUXO_ENRICH=simulated,web` and the CLI reader, an SDR's reason line can read "Job title from the enrichment tool"; the demo's offline reader finds nothing, so the pages look the same. |
| 8 Money | Unchanged. Do not claim more demos from the lookup; it is not simulated. |
| 9 SDR time | Unchanged. |
| 10 Cost | Add a row to the running table: "Web lookups for blanks \| Sonnet + search \| 180 \| 6,300 \| ~$10". Totals become ~9,900 calls, ~12M tokens, ~$190. Last line: "About 15 cents of model spend per demo, against $26 of SDR time; one month of added revenue repays the build about 35 times." Notes: "HubSpot needs no new seats and the enrichment tool is already licensed" stays; add "the web lookup behind it costs about 5 cents a lead it is asked about, about $10 a month". |
| 11 Risks | Add a sixth row and retitle "Six risks": "AI invents a title or size \| Must cite a page and quote it word for word; code fetches the page and keeps the value only if the quote is there". Notes: add it to the AI-risk sentence. |
| 12 Next steps | Month 1 "Connect": "Real HubSpot link; scores written to contacts; tool and web lookup switched on". Month 2 "Measure": the measured month also reports the lookup's fill rate and rejected answers. |
| 13 The ask | Unchanged. |
| 14 Appendix: points tables | Unchanged tables. Under "Why it matters", add: "Fewer blanks now: the licensed tool, then a quote-checked web lookup." |
| 15 to 17 Appendix | Unchanged. |

## The CTO deck

The reader table (where Claude is used and what code checks) gets a row: task `enrich`, Sonnet with web search, input the lead's name, company and domain, output title and headcount with page and quote, check: page fetched, quote found, name or number in the quote, public host only, value else dropped. The scoring-pipeline slide's step 1 gets the same wording as slide 4 above, the scheduler and roles slides do not change, and the known-gaps slide gains one line: the lookup is untested on real leads; its fill rate and rejection rate come from the measured month.
