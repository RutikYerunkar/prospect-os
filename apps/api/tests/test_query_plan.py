from groundwork.domain.query_plan import (
    QUERY_PLAN_VERSION,
    QueryTemplateId,
    build_domain_resolution_query,
    build_query_plan,
    build_source_queries,
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


# --- v2.0.2 retrieval alignment: build_source_queries() ---------------------


def test_query_plan_version_is_v2() -> None:
    assert QUERY_PLAN_VERSION == "v2"


def test_source_queries_baseline_count_is_three() -> None:
    """No persona_titles, no target_technologies -> funding/careers/size
    only, always — the approved 3/4/4/5 query-count matrix's base case."""
    spec = _play_spec(persona_titles=[], target_technologies=[])
    plan = build_source_queries("Acme Robotics", spec, max_queries=5)
    assert len(plan) == 3
    assert [e.template_id for e in plan] == [
        QueryTemplateId.COMPANY_FUNDING,
        QueryTemplateId.COMPANY_CAREERS,
        QueryTemplateId.COMPANY_SIZE,
    ]


def test_source_queries_persona_only_count_is_four() -> None:
    spec = _play_spec(persona_titles=["VP of Sales"], target_technologies=[])
    plan = build_source_queries("Acme Robotics", spec, max_queries=5)
    assert len(plan) == 4
    assert plan[-1].template_id == QueryTemplateId.COMPANY_PERSONA_LEADERSHIP


def test_source_queries_technology_only_count_is_four() -> None:
    spec = _play_spec(persona_titles=[], target_technologies=["kubernetes"])
    plan = build_source_queries("Acme Robotics", spec, max_queries=5)
    assert len(plan) == 4
    assert plan[-1].template_id == QueryTemplateId.COMPANY_TECHNOLOGY


def test_source_queries_persona_and_technology_count_is_five() -> None:
    spec = _play_spec(persona_titles=["VP of Sales"], target_technologies=["kubernetes"])
    plan = build_source_queries("Acme Robotics", spec, max_queries=5)
    assert len(plan) == 5
    assert [e.template_id for e in plan] == [
        QueryTemplateId.COMPANY_FUNDING,
        QueryTemplateId.COMPANY_CAREERS,
        QueryTemplateId.COMPANY_SIZE,
        QueryTemplateId.COMPANY_PERSONA_LEADERSHIP,
        QueryTemplateId.COMPANY_TECHNOLOGY,
    ]


def test_source_queries_no_generic_leadership_fallback() -> None:
    """An empty persona_titles must never fall back to a generic
    'leadership team about' query — that template no longer exists at all."""
    spec = _play_spec(persona_titles=[], target_technologies=[])
    plan = build_source_queries("Acme Robotics", spec, max_queries=5)
    template_ids = {e.template_id for e in plan}
    assert QueryTemplateId.COMPANY_PERSONA_LEADERSHIP not in template_ids
    assert not hasattr(QueryTemplateId, "COMPANY_LEADERSHIP")


def test_source_queries_deterministic_for_same_input() -> None:
    spec = _play_spec(persona_titles=["VP of Sales"], target_technologies=["kubernetes", "pytorch"])
    first = build_source_queries("Acme Robotics", spec, max_queries=5)
    second = build_source_queries("Acme Robotics", spec, max_queries=5)
    assert [(e.template_id, e.query, e.query_digest) for e in first] == [
        (e.template_id, e.query, e.query_digest) for e in second
    ]


def test_source_queries_persona_uses_first_title_only() -> None:
    spec = _play_spec(persona_titles=["VP of Sales", "Head of Growth"], target_technologies=[])
    plan = build_source_queries("Acme Robotics", spec, max_queries=5)
    persona_entry = next(e for e in plan if e.template_id == QueryTemplateId.COMPANY_PERSONA_LEADERSHIP)
    assert "VP of Sales" in persona_entry.query
    assert "Head of Growth" not in persona_entry.query


def test_source_queries_truncation_drops_least_play_specific_first() -> None:
    spec = _play_spec(persona_titles=["VP of Sales"], target_technologies=["kubernetes"])
    full = build_source_queries("Acme Robotics", spec, max_queries=5)
    truncated = build_source_queries("Acme Robotics", spec, max_queries=3)
    assert [e.template_id for e in truncated] == [e.template_id for e in full[:3]]
    assert QueryTemplateId.COMPANY_TECHNOLOGY not in {e.template_id for e in truncated}
    assert QueryTemplateId.COMPANY_PERSONA_LEADERSHIP not in {e.template_id for e in truncated}


def test_source_queries_zero_max_queries_returns_empty() -> None:
    spec = _play_spec(persona_titles=["VP of Sales"], target_technologies=["kubernetes"])
    assert build_source_queries("Acme Robotics", spec, max_queries=0) == []
