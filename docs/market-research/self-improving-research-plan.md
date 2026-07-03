# Self-Improving Research Plan

This plan refines the market research flow into a measured improvement loop. The target output is fewer, stronger recommendations with longer evidence-backed explanations, verified links, and clear reasoning for why each source matters.

## Target Behavior

- Show at most three candidate ideas by default, not five.
- Prefer one high-confidence candidate over several weak ones.
- Require either multiple independent evidence items or one high-trust, high-impact source before presenting a candidate as strong.
- Expand each candidate into a longer explanation that covers thesis, evidence chain, score drivers, source quality, gaps, and what would invalidate the signal.
- Verify attached evidence URLs before using them as links in the final answer.

## Loop

1. Measure every research answer.

   Add a post-answer evaluation pass that scores the response with the existing rubric in [evaluation.md](evaluation.md), then records structured results under the runtime trace or eval output. The required dimensions are evidence quality, source attribution, source-link validity, ranking reason clarity, freshness, ticker correctness, usefulness, and concision. Add two missing judge tags: `broken_link` and `thin_evidence`.

2. Improve the weakest layer.

   Use the highest-frequency failed tag to choose exactly one improvement target per iteration. `broken_link` maps to retrieval/evidence validation, `thin_evidence` maps to source expansion or candidate filtering, `unclear_ranking_reason` maps to reporting, `wrong_ticker` maps to extraction, and `stale_data` maps to source freshness. Re-run the same eval cases after each change so regressions are visible.

3. Enforce stronger evidence gates.

   Before a candidate reaches `format_candidates`, classify its evidence as `strong`, `developing`, or `weak`. Strong means at least two distinct named/link-backed sources, or one source with a very high trust score and direct market-moving evidence such as company filings, official company news, regulator data, or major primary-source breaks. Developing candidates can appear with cautious wording. Weak candidates should be suppressed unless the user asks for weak signals.

4. Produce a richer but shorter report.

   Change the default report from five short bullets to at most three detailed candidates. For each candidate, include: why it ranked, evidence chain with working links, how each link supports the thesis, score component explanation, source diversity/trust summary, missing evidence, and next checks. Keep `/signals <ticker>` as a single detailed drill-down.

## Implementation Map

- Planning: change `ResearchConstraints.max_candidates` defaults in `src/news_agent/research/planner.py` from five to three for `/research`, `/candidates`, and `/researchstatus`.
- Analysis: extend `src/news_agent/research/analysis.py` so `CandidateExplanation` includes evidence strength, distinct source count, linked source count, max trust score, and suppression reasons.
- Reporting: update `src/news_agent/research/reporting.py` to use a long candidate block with `Evidence chain`, `Why this matters`, `Score drivers`, and `Weaknesses` sections.
- Link validation: add a link-check step before final formatting. It should open each candidate URL with a short timeout, mark unavailable links, and never print a URL that failed validation unless labeled as unavailable.
- Evaluation: extend `src/news_agent/evaluation/runner.py` and `docs/market-research/evals/market_research_cases.jsonl` to check fewer candidates, longer explanations, multiple sources or high-trust evidence, and working links.
- Tests: update `tests/test_research_planner.py`, `tests/test_research_reporting.py`, and add focused tests for evidence strength and link validation behavior.

## Report Shape

```text
Market research candidates

1. NVDA - AI infrastructure - score 86 - strong evidence
   Why this ranked: direct evidence connects cloud capex, accelerator demand, and supplier commentary to the same theme.
   Evidence chain:
   - Nvidia earnings release - Nvidia, 2026-05-20: https://...
     Supports demand and revenue acceleration.
   - Cloud capex filing/commentary - Microsoft, 2026-05-22: https://...
     Confirms buyer-side spend.
   Score drivers: mentions 88, diversity 82, recency 90, trust 95.
   Weaknesses: price/volume confirmation is neutral.
   Next checks: look for follow-through from hyperscaler capex and supplier backlog.
```

## Acceptance Criteria

- `/research` and `/candidates` return no more than three candidates by default.
- Strong candidates have either two or more distinct linked sources, or one high-trust direct source with an explicit reason.
- Every printed evidence URL has been checked or is labeled unavailable.
- The report explains why each link supports the candidate, not just that the link exists.
- Eval output records pass/fail tags that identify the next improvement target.
- A failed eval run can be used to choose the next code or source-pack change without manually rereading every answer.
