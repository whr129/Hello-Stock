# Evidence grounding

Research reports must distinguish stored evidence, live web sources, and missing information. Prefer a weaker answer with explicit gaps over invented sources, URLs, catalysts, or causal claims.

## Stored evidence

Preserve article title and canonical `articles.url`, source name/provider, publication or stored timestamp, snippet, source family, and trust score. Report missing links as unavailable rather than synthesizing a URL.

The analysis layer classifies candidates as strong, developing, or weak. Strong evidence normally needs multiple distinct named/link-backed sources and evidence clusters; a high-trust direct, high-impact source can qualify alone. Link validation can downgrade strength when URLs are unavailable. Candidate lists exclude weak evidence and show developing evidence only when enabled by the plan/configuration.

Price/volume gaps, stale snapshots, and limited source diversity remain explicit weaknesses even when a candidate clears the evidence gate. A high score alone is not proof of strong evidence or a recommendation to trade.

## Reports and links

The planner defaults to three candidates. Reports include why a candidate ranked, an evidence chain, score drivers, source quality, weaknesses, and next checks. `/signals <ticker>` provides a focused explanation. `RESEARCH_REPORT_MAX_EVIDENCE_ITEMS` defaults to three linked evidence items per candidate.

Link checking labels URLs available, unavailable, or missing and reuses sufficiently recent checks according to `EVIDENCE_LINK_RECHECK_HOURS`. A successful HTTP check establishes reachability, not that the page independently proves every claim. External company research must cite its own sources and keep uncertain claims explicit.

Relevant controls include `SIGNAL_MIN_STRONG_EVIDENCE_SOURCES`, `SIGNAL_ALLOW_DEVELOPING_DEFAULT`, `EVIDENCE_LINK_RECHECK_HOURS`, and `RESEARCH_REPORT_MAX_EVIDENCE_ITEMS`.

## Historical snapshots

Older signal evidence can contain only article/summary IDs and text. Retrieval prefers newer link-backed snapshots where possible. Repair recoverable historical links with:

```bash
news-agent-backfill-evidence --limit 500
```

This updates stored evidence; it cannot recover links that are absent from the underlying articles. All research answers retain the informational-only financial caveat.
