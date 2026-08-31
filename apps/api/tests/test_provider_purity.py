"""CRITICAL BOUNDARY (Checkpoint G): `providers/` must never import a
repository, SQLAlchemy, or a DB table model — only `engine/llm.py` persists
attempt telemetry. Verified by static source inspection rather than trusting
convention, mirroring `CLAUDE.md`'s own domain-purity invariant for
`domain/`.
"""

from __future__ import annotations

from pathlib import Path

import groundwork.providers as providers_pkg

FORBIDDEN_SUBSTRINGS = ("groundwork.repositories", "sqlalchemy", "groundwork.models.tables")


def _provider_source_files() -> list[Path]:
    root = Path(providers_pkg.__file__).parent
    return sorted(root.rglob("*.py"))


def test_providers_never_import_repositories_or_sqlalchemy():
    violations = []
    for path in _provider_source_files():
        text = path.read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("import ") or stripped.startswith("from ")):
                continue
            for forbidden in FORBIDDEN_SUBSTRINGS:
                if forbidden in stripped:
                    violations.append(f"{path.relative_to(Path(providers_pkg.__file__).parent.parent)}: {stripped}")
    assert violations == [], "providers/ imports a repository/SQLAlchemy/table model:\n" + "\n".join(violations)
