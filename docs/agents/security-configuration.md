# Security and Configuration

Keep secrets in `.env`, especially `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `DATABASE_URL`, and `RUNTIME_ALERT_TELEGRAM_CHAT_ID`.

Do not commit credentials or production chat data. Conversation transcripts and learned memories are user data; treat them as sensitive.

When adding new providers, router rules, runtime alerts, or memory heuristics, prefer configuration-driven behavior over hardcoded source-specific logic.
