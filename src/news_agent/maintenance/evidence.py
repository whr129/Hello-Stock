from __future__ import annotations

import argparse
import asyncio

from news_agent.research.scheduler import backfill_signal_evidence_links
from news_agent.settings import get_settings
from news_agent.storage.database import create_session_factory


async def backfill_evidence_links(*, limit: int = 500) -> int:
    session_factory = create_session_factory(get_settings())
    async with session_factory() as session:
        updated = await backfill_signal_evidence_links(session, limit=limit)
        await session.commit()
        return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair generated evidence metadata.")
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="maximum recent signal snapshots to inspect",
    )
    args = parser.parse_args()
    updated = asyncio.run(backfill_evidence_links(limit=args.limit))
    print(f"market_signal_snapshots updated: {updated}")


if __name__ == "__main__":
    main()
