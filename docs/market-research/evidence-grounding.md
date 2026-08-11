# Evidence Grounding

Market research answers must be grounded in stored evidence. The bot should prefer a weaker answer with explicit gaps over a complete-looking answer that invents sources, links, causes, or catalysts.

## Evidence Contract

Every evidence item used in `/research`, `/candidates`, and `/signals <ticker>` should preserve:

- article title
- article URL
- source name
- source provider
- article published time or stored created time
- evidence snippet
- source family
- trust score

`articles.url` is the canonical resource link. If a link is unavailable, the answer must say that the item is stored evidence with no link instead of inventing one.

## Confidence Rules

Use normal confidence only when evidence is recent, link-backed, and comes from more than one distinct source.

Use weak-confidence wording when evidence is:

- missing a URL or article title
- from one source only
- stale
- low-trust
- missing price or volume confirmation

If no stored evidence exists, the answer should say there is not enough stored evidence yet and avoid causal claims.

## Historical Snapshots

Signal snapshots created before evidence-link enrichment may only contain `article_id`, `summary_id`, and `text`. Candidate retrieval prefers newer link-backed snapshots over older unlinked rows for the same ticker/theme.

Run this maintenance command after deploying evidence-link changes if old snapshots still dominate answers:

```bash
PYTHONPATH=src .venv/bin/news-agent-backfill-evidence --limit 500
```

## Answer Shape

Research answers should include concise evidence blocks:

```text
Evidence:
- <article title> - <source name>, <date>: <url>
  <short snippet>
```

Limit linked evidence to three items per candidate and five candidates per answer unless a longer report format is added.

## Examples

Strong evidence:

```text
1. MU - memory chips - score 78
   Components: mentions 80, diversity 60
   Evidence: HBM demand accelerates - Example Markets, 2026-05-23: https://example.com/hbm
     HBM demand coverage accelerated.
```

Weak evidence:

```text
1. MU - memory chips - score 52
   Evidence: stored evidence, link unavailable: HBM demand coverage accelerated.
   Weakness: source links are unavailable.
```

No evidence:

```text
No market attention candidates are available yet.
```
