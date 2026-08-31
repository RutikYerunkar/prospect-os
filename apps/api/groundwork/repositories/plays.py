from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from groundwork.models.tables import PlayRow


class PlayRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def create(self, *, name: str, objective_text: str, icp_spec: dict[str, Any], mode: str) -> str:
        play_id = str(uuid.uuid4())
        async with self._session_factory() as session:  # type: AsyncSession
            session.add(
                PlayRow(id=play_id, name=name, objective_text=objective_text, icp_spec=icp_spec, mode=mode)
            )
            await session.commit()
        return play_id
