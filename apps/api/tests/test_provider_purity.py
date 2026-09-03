"""CRITICAL BOUNDARY (Checkpoint G): `providers/` must never import a
repository, SQLAlchemy, or a DB table model — only `engine/llm.py` persists
attempt telemetry. Verified by static source inspection rather than trusting
convention, mirroring `CLAUDE.md`'s own domain-purity invariant for
`domain/`.

v2 §Part 4/§N.5 extends this: `providers/contact_base.py` and
`providers/demo/contact_enrichment.py` are covered automatically (this
module's scan already walks the whole `providers/` package recursively), and
two more static checks are added — `domain/` never imports a provider
implementation, and `providers/demo/*` never imports a live/paid provider
module.
"""

from __future__ import annotations

from pathlib import Path

import groundwork.domain as domain_pkg
import groundwork.providers as providers_pkg
import groundwork.providers.demo as providers_demo_pkg

FORBIDDEN_SUBSTRINGS = ("groundwork.repositories", "sqlalchemy", "groundwork.models.tables")


def _source_files(pkg) -> list[Path]:
    root = Path(pkg.__file__).parent
    return sorted(root.rglob("*.py"))


def _provider_source_files() -> list[Path]:
    return _source_files(providers_pkg)


def _import_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]


def test_providers_never_import_repositories_or_sqlalchemy():
    violations = []
    for path in _provider_source_files():
        text = path.read_text()
        for stripped in _import_lines(text):
            for forbidden in FORBIDDEN_SUBSTRINGS:
                if forbidden in stripped:
                    violations.append(f"{path.relative_to(Path(providers_pkg.__file__).parent.parent)}: {stripped}")
    assert violations == [], "providers/ imports a repository/SQLAlchemy/table model:\n" + "\n".join(violations)


def test_domain_never_imports_a_provider_implementation():
    """§N.5 — `domain/contact_identity.py` (and every other `domain/`
    module) must never import a provider implementation. Providers return
    observations; `domain/` derives states from them, pure and offline (D2).
    """
    violations = []
    for path in _source_files(domain_pkg):
        text = path.read_text()
        for stripped in _import_lines(text):
            if "groundwork.providers" in stripped:
                violations.append(f"{path.relative_to(Path(domain_pkg.__file__).parent.parent)}: {stripped}")
    assert violations == [], "domain/ imports a provider implementation:\n" + "\n".join(violations)


def test_demo_providers_never_import_a_live_provider_module():
    """§N.5 — 'no paid/live provider is instantiated in Demo tests', at the
    source level: `providers/demo/*` must never import `providers/live/*`,
    so a Demo run is structurally incapable of reaching Apollo/OpenAI/Tavily
    regardless of what config is set."""
    violations = []
    for path in _source_files(providers_demo_pkg):
        text = path.read_text()
        for stripped in _import_lines(text):
            if "groundwork.providers.live" in stripped:
                violations.append(f"{path.relative_to(Path(providers_pkg.__file__).parent.parent)}: {stripped}")
    assert violations == [], "providers/demo/ imports a live provider module:\n" + "\n".join(violations)
