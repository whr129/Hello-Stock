from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from news_agent.settings import get_settings
from news_agent.storage.database import create_session_factory
from news_agent.storage.models import Base

GENERATED_DATA_TABLES = (
    "summary_embeddings",
    "article_embeddings",
    "market_signal_snapshots",
    "market_mentions",
    "summaries",
    "articles",
    "market_snapshots",
    "job_runs",
    "runtime_alerts",
    "runtime_errors",
    "runtime_steps",
    "runtime_runs",
)


async def reset_generated_data() -> dict[str, tuple[int, int]]:
    return await _truncate_tables(GENERATED_DATA_TABLES)


async def reset_all_app_data() -> dict[str, tuple[int, int]]:
    tables = tuple(table.name for table in reversed(Base.metadata.sorted_tables))
    return await _truncate_tables(tables)


async def _truncate_tables(tables: tuple[str, ...]) -> dict[str, tuple[int, int]]:
    if not tables:
        return {}
    session_factory = create_session_factory(get_settings())
    async with session_factory() as session:
        before = {}
        for table in tables:
            before[table] = await _count_rows(session, table)
        await session.execute(
            text(f"truncate table {', '.join(tables)} restart identity cascade")
        )
        await session.commit()
        after = {}
        for table in tables:
            after[table] = await _count_rows(session, table)
    return {table: (before[table], after[table]) for table in tables}


async def _count_rows(session, table: str) -> int:
    return int((await session.execute(text(f"select count(*) from {table}"))).scalar_one())


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset news-agent database data.")
    parser.add_argument(
        "--scope",
        choices=("generated", "all"),
        default="generated",
        help="generated preserves users, sources, and memory; all clears every app table.",
    )
    args = parser.parse_args()
    result = asyncio.run(
        reset_all_app_data() if args.scope == "all" else reset_generated_data()
    )
    for table, (before, after) in result.items():
        print(f"{table}: {before} -> {after}")


if __name__ == "__main__":
    main()
