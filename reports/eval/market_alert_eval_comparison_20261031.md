# Jev alert evaluation: v1, v2, and fresh holdout

The v2 routing criteria remove an overlap: supported routine financial/operating facts belong in the daily report; unrelated, unsupported, administrative, stale or duplicate items are ignored. Expected labels were fixed before provider calls. The original 36 labels were not changed. A separately reviewed 24-case holdout was labeled before testing the preselected 0.70 candidate threshold.

| Run | Cases | Host confidence | Route accuracy | Raw urgent precision / recall | Host urgent precision / recall | Provider failures | Actual cost |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: |
| v1, corrected numerical parser | 36 | 0.85 | 80.56% | 100% / 100% | 100% / 41.67% | 0 | $0.002171316 |
| v2, same original corpus | 36 | 0.85 | 94.44% | 100% / 100% | 100% / 66.67% | 0 | $0.002280180 |
| v2, final revision-aware context | 36 | 0.70 | 94.44% | 100% / 100% | 100% / 91.67% | 0 | $0.002289252 |
| v2, fresh holdout | 24 | 0.70 | 100% | 100% / 100% | 100% / 100% | 0 | $0.001520316 |
| v2, same holdout repeated 2026-11-05 | 24 | 0.70 | 100% | 100% / 100% | 100% / 100% | 0 | $0.001520316 |

The 2026-11-05 PR verification repeated the existing 24-case holdout; it is not a new blind
sample. All 24 requests completed, with unchanged routing results. Raw sentiment accuracy
was 72.73% (16/22), reinforcing the sentiment-quality limitation below. This repeat cost
$0.001520316 in addition to the original evaluation totals. The clean staged PR snapshot
also passed 539 tests (1 skipped), including real PostgreSQL migration and delivery workflow
checks, plus Ruff. Telegram delivery in those workflow checks used a controlled adapter.

The 0.70 candidate met the preselected >=90% urgent precision and >=80% host recall targets in the holdout (8 true urgent predictions, no false urgent predictions among 16 nonurgent cases). The original final-context run missed one multi-company urgent event at confidence 0.67. This small synthetic sample does not establish production error rates or calibrated probabilities. No production threshold was changed by the evaluation runner.

The host policy now permits a material correction through an event cooldown only when the durable revision is newer than prior accepted deliveries and Jev independently chooses `immediate_alert` with `material_new_event`. Confidence, freshness, trusted evidence, quiet hours and recipient hourly limits still apply. The observed-new-content time is not treated as publisher freshness.

After reviewing the locked original and fresh holdout results, integration selected 0.70 as the configured host default. This is a bounded operating choice supported by a small synthetic sample, not a claim of production calibration.

## Sentiment limitations

Final original-corpus sentiment accuracy was 91.89% (34/37 labels); holdout accuracy was 77.27% (17/22). Holdout mismatches were:

- A small lease-cost increase and small energy saving were labeled neutral instead of negative/positive.
- An unverified screenshot and explicit satire were labeled negative instead of unknown, even though both were correctly routed to ignore.
- An unchanged stock-split reminder was labeled unknown instead of neutral.

News tone is not truth verification. Trust/primary-source annotations in this benchmark are assumptions about synthetic evidence, not independently verified sources. Aggregating unsupported or satirical tone as verified company sentiment would be misleading. The production aggregate therefore excludes ignored Jev decisions, `insufficient_evidence`/`stale_or_duplicate` reasons, and evidence with trust below 0.70. Individual raw tone labels remain in the audit record. This policy does not change the holdout labels or the reported 77.27% raw sentiment accuracy.

## Cost and evidence

The three v2 runs cost $0.006089748 in total, below the additional $0.10 cap. Including earlier v1 and diagnostic runs, the total recorded evaluation cost is $0.010619658. The final original run averaged about $0.0000636 per assessment, or $0.064 per 1,000 similarly sized assessments; actual cost varies with context size and question count.

No database or Telegram calls occurred during live quality evaluations. PostgreSQL workflow tests are separate. Raw responses retain only model, answers and usage; credentials and headers are excluded.

Artifacts:

- Baseline: `market_alert_eval_20261031T022429781780Z.{json,jsonl,md}`
- v2 at 0.85: `market_alert_eval_20261031T033515444295Z.{json,jsonl,md}`
- v2 original at 0.70: `market_alert_eval_20261031T033759518276Z.{json,jsonl,md}`
- Fresh holdout at 0.70: `market_alert_eval_20261031T033756673544Z.{json,jsonl,md}`
- PR verification repeat: `market_alert_eval_20261105T235809369120Z.{json,jsonl,md}`
