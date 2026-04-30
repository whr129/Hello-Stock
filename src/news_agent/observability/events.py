import logging
from typing import Any

logger = logging.getLogger("news_agent.events")


def record_event(event_name: str, **details: Any) -> None:
    logger.info(event_name, extra={"details": details})
