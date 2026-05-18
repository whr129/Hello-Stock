# Market Research Evaluation Plan

Use this plan to evaluate whether market research answers are useful, not only whether the code works.

## Evaluation Layers

Technical correctness:

- Commands route to the research path.
- Extraction writes expected mentions.
- Scoring writes signal snapshots.
- Reports include required fields.
- Guardrails are present.

Answer usefulness:

- The answer addresses the prompt.
- The answer gives concrete tickers, themes, sources, and evidence.
- The answer explains why a candidate is ranked.
- The answer is recent enough for the requested horizon.
- The answer is concise enough for Telegram.

## Prompt Set

Keep a small local eval set of 20-50 prompts:

```text
/research
/candidates
/signals NVDA
/signals MU
What names are starting to get attention?
Why is NVDA ranked?
What changed since yesterday?
Run deep research on semiconductors.
What weak signals should I watch?
```

For each prompt, write the expected answer properties before running it.

## Rubric

Score each answer from 1 to 5:

- Relevance: Did it answer the actual question?
- Specificity: Did it give concrete tickers, themes, sources, and evidence?
- Freshness: Did it use recent stored news and market context?
- Explainability: Did it explain score components clearly?
- Usefulness: Would this help market research?
- Safety: Did it avoid buy/sell advice and include caveats?
- Conciseness: Was it readable in Telegram?

Example:

```text
Prompt: /signals MU

Expected:
- Current rank or no-rank state.
- Why MU is appearing.
- Mention velocity, source diversity, recency, price, and volume components.
- 2-3 evidence snippets or article titles.
- Weak or missing evidence.
- Not-financial-advice caveat.

Scores:
Relevance: 4
Specificity: 2
Freshness: 3
Explainability: 3
Usefulness: 2
Safety: 5
Conciseness: 4

Notes:
Too generic. Needs article titles and clearer reason why MU matters.
```

## Bad Answer Tags

Tag every bad answer with one or more reasons:

- `too_generic`
- `no_evidence`
- `wrong_ticker`
- `stale_data`
- `hallucinated_source`
- `unclear_ranking_reason`
- `too_verbose`
- `missing_weak_evidence`
- `not_useful_research`

## Seeded Integration Evals

Create deterministic seed data for integration tests:

```text
Micron rises as HBM demand accelerates from AI server buildouts
Nvidia suppliers gain on cloud capex optimism
Memory chip pricing improves as data center demand grows
```

Expected result:

- MU should rank.
- Theme should be `memory chips` or `AI infrastructure`.
- Evidence should mention HBM, cloud, or data center demand.
- `/signals MU` should include score components and weak evidence.

## Regression Tests

Add deterministic tests for:

- Planner outputs.
- Extraction false positives such as `AI` and `HBM`.
- Weighted score ordering.
- Missing price or volume data staying neutral.
- Report output containing score, components, evidence, weak evidence, and guardrail text.

## Improvement Loop

1. Run the prompt set.
2. Score answers with the rubric.
3. Tag bad answers.
4. Pick the highest-frequency bad-answer tag.
5. Improve only the relevant layer.
6. Re-run the same prompt set.

Likely first improvement targets:

- Better evidence display with article titles and source names.
- Better extraction precision.
- Better scoring weights and thresholds.
- Better explanation text for why a ticker matters.
