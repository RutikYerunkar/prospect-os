from groundwork.domain.query_plan import (
    QueryTemplateId,
    build_domain_resolution_query,
    build_query_plan,
)
from groundwork.models.schemas import PlaySpec


def _play_spec(**overrides) -> PlaySpec:
    defaults = dict(
        objective_text="Find AI infra companies",
        target_industries=["ai_infrastructure"],
        persona_titles=["VP of Sales"],
        target_technologies=["kubernetes", "pytorch"],
    )
    defaults.update(overrides)
    return PlaySpec(**defaults)


def test_query_plan_bounded_by_max_queries() -> None:
    plan = build_query_plan(_play_spec(), max_queries=2)
    assert len(plan) == 2


def test_query_plan_deterministic_for_same_input() -> None:
    spec = _play_spec()
    first = build_query_plan(spec, max_queries=4)
    second = build_query_plan(spec, max_queries=4)
    assert [(e.template_id, e.query) for e in first] == [(e.template_id, e.query) for e in second]


def test_query_plan_highest_signal_templates_first() -> None:
    plan = build_query_plan(_play_spec(), max_queries=4)
    assert plan[0].template_id == QueryTemplateId.INDUSTRY_FUNDING


def test_query_plan_truncation_drops_least_specific_last_entries() -> None:
    full = build_query_plan(_play_spec(), max_queries=4)
    truncated = build_query_plan(_play_spec(), max_queries=1)
    assert truncated[0].template_id == full[0].template_id


def test_query_digest_matches_query_content() -> None:
    plan = build_query_plan(_play_spec(), max_queries=1)
    entry = plan[0]
    assert entry.query_digest == build_query_plan(_play_spec(), max_queries=1)[0].query_digest


def test_domain_resolution_query_is_own_template() -> None:
    entry = build_domain_resolution_query("Acme Corp")
    assert entry.template_id == QueryTemplateId.OFFICIAL_SITE_DOMAIN
    assert "Acme Corp" in entry.query


def test_query_plan_zero_max_queries_returns_empty() -> None:
    assert build_query_plan(_play_spec(), max_queries=0) == []
