"""DemoSearchProvider — fixture-backed `SearchProvider` (§11).

Same Protocol as a live search provider. `discover` returns the fixture
company roster; `fetch_sources` returns the fixture's authored sources, with
seeded latency and fixture-configured scripted retry/failure behavior keyed
by `(run_id, prospect_id, step_name)`.
"""

from __future__ import annotations

import asyncio
import random

from groundwork.models.schemas import CompanySeed, PlaySpec
from groundwork.providers.base import FAILURE_TYPES, ProviderUnavailable, SourceDocument, parse_ctx_key, stable_seed
from groundwork.providers.demo.fixtures import FixturePack

_JITTER_MIN_S = 0.03
_JITTER_MAX_S = 0.15


class DemoSearchProvider:
    name = "demo_fixture"

    def __init__(self, pack: FixturePack, seed: int) -> None:
        self.pack = pack
        self.seed = seed
        self._attempt_counts: dict[str, int] = {}

    def _jitter(self, ctx_key: str) -> float:
        rng = random.Random(stable_seed(str(self.seed), ctx_key))
        return _JITTER_MIN_S + rng.random() * (_JITTER_MAX_S - _JITTER_MIN_S)

    async def discover(self, spec: PlaySpec, limit: int) -> list[CompanySeed]:
        await asyncio.sleep(self._jitter(f"discover:{self.seed}"))
        return [c.to_company_seed() for c in self.pack.companies[:limit]]

    async def fetch_sources(self, company: CompanySeed, *, ctx_key: str) -> list[SourceDocument]:
        fixture = self.pack.company_by_slug(company.slug)
        _, _, step_name = parse_ctx_key(ctx_key)
        failure = fixture.failure_script.get(step_name)
        if failure is not None:
            attempt = self._attempt_counts.get(ctx_key, 0) + 1
            self._attempt_counts[ctx_key] = attempt
            if attempt <= failure.fail_attempts:
                exc_cls = FAILURE_TYPES.get(failure.error, ProviderUnavailable)
                raise exc_cls(
                    f"scripted {failure.error} for {company.slug}/{step_name}, attempt {attempt}"
                )

        await asyncio.sleep(self._jitter(ctx_key))
        return [
            SourceDocument(
                ref=source.ref,
                title=source.title,
                claim=source.claim,
                text=source.snippet,
                source_provider=self.name,
                signal_type=source.signal_type.value if source.signal_type else None,
                confidence=source.confidence,
            )
            for source in fixture.sources
        ]
