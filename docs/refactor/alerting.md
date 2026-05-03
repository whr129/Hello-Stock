# Alerting

Runtime alerts should be delivered to a configured Telegram admin chat, not to every end user. Alerts should include:

- workflow type
- run id
- failing step
- concise error summary
- related source, ticker, or user when relevant

User-facing command failures still return normal responses in-chat; operator alerts are separate.

