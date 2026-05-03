# Overview

The application is refactored around a LangGraph supervisor that routes each incoming request to one or more specialized subagents:

- `news_agent`: personalized news briefs, source management, topics, local preferences, recap settings, and memory operations
- `market_agent`: watchlist management, live quote lookup, stored snapshot fallback, and technical analysis
- `runtime_agent`: runtime inspection, alerting, trace lookup, refresh debugging, and execution-history analysis

The Telegram bot, scheduler, and persistence model remain in place. The active chat path no longer uses the previous generic tool registry or free-form ReAct fallback.

