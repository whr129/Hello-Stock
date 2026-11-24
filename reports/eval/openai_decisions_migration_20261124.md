# OpenAI Decisions migration evaluation

On November 24, 2026 UTC, the alert evaluator called OpenAI's native Decisions endpoint
with `gpt-6-luna`, prompt version `market-alert-openai-v1`, and alert confidence threshold
`0.70`. The two existing corpora were unchanged. The new holdout was labeled before the
model saw its cases. Each run had a USD $0.10 cost cap.

| Corpus | Correct routes | Host urgent precision / recall | Raw sentiment accuracy | Request p95 | Estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original locked set | 33/36 | 100% / 100% | 34/37 | 0.40 s | $0.0048406 |
| Existing locked holdout | 24/24 | 100% / 100% | 21/22 | 0.32 s | $0.0032093 |
| Fresh [synthetic holdout](../../tests/evaluation/market_alert_openai_holdout_cases.jsonl) | 23/24 | 100% / 100% | 20/24 | 0.24 s | $0.0032720 |

All 84 responses parsed, 28 urgent cases passed the host delivery policy, and no nonurgent
case became an eligible immediate alert. The four routing errors were daily-report items
the model ignored. The fresh holdout's recurring sentiment error was neutral in place of
unknown when ticker-specific evidence was thin. The runs used 113,219 input tokens for an
estimated USD $0.0113219. These synthetic runs support a shadow rollout. Production news
coverage, false alerts, and delivery latency need separate verification.

Jev scored 34/36 and 24/24 routes on the prior matching corpora, with host urgent recall
of 11/12 and 8/8, respectively. OpenAI preserved urgent precision and improved recall on the
original set but ignored one additional routine item. OpenAI matched Jev's raw sentiment
score on the original corpus and improved it on the existing holdout. See the [Jev comparison](market_alert_eval_comparison_20261031.md).

The current local project key also passed direct chat, 1,536-dimensional embedding, web
search, and the new credential diagnostic. A separate synthetic announcement exercised the
live OpenAI adapter and isolated PostgreSQL. OpenAI chose immediate alert and the host
suppressed delivery because confidence was below 0.70. A second synthetic test confirmed
the allowed path. OpenAI classified a bankruptcy notice as immediate at 0.94 confidence,
queued one outbox row in PostgreSQL, and dispatched one message through a simulated Telegram
adapter. Automated PostgreSQL tests also cover shadow behavior and duplicate suppression
across providers.

To repeat a bounded run from the project directory with `OPENAI_API_KEY` configured:

```bash
python -m news_agent.evaluation.alerts --provider openai --live \
  --host-confidence 0.70 --max-cost-usd 0.10 \
  --cases tests/evaluation/market_alert_openai_holdout_cases.jsonl
```
