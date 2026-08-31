"""`make seed` — ensure the schema exists and the fixture pack is valid.

Seeding doesn't pre-populate companies/prospects: those are created by
`run_demo` (or, later, a real Run) from the fixture pack at execution time,
same as Live Mode would create them from a real search provider.
"""

from __future__ import annotations

import asyncio

from groundwork.db import create_all
from groundwork.providers.demo.fixtures import load_fixture_pack


async def main() -> None:
    pack = load_fixture_pack()
    await create_all()
    print(f"schema ready; fixture pack loaded with {len(pack.companies)} companies")


if __name__ == "__main__":
    asyncio.run(main())
