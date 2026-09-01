from groundwork.domain.industry import OTHER_CATEGORY, allowed_categories, validate_category
from groundwork.models.schemas import PlaySpec


def _play_spec(**overrides) -> PlaySpec:
    defaults = dict(
        objective_text="Find AI infra companies",
        target_industries=["ai_infrastructure"],
        excluded_industries=["retail_pos"],
        adjacent_industries={"data_tooling": ["ai_infrastructure"]},
    )
    defaults.update(overrides)
    return PlaySpec(**defaults)


def test_allowed_categories_union_of_target_excluded_and_adjacent() -> None:
    allowed = allowed_categories(_play_spec())
    assert allowed == frozenset({"ai_infrastructure", "retail_pos", "data_tooling", OTHER_CATEGORY})


def test_allowed_categories_always_includes_other() -> None:
    allowed = allowed_categories(_play_spec(target_industries=[], excluded_industries=[], adjacent_industries={}))
    assert allowed == frozenset({OTHER_CATEGORY})


def test_validate_category_in_set_passes_through() -> None:
    allowed = allowed_categories(_play_spec())
    assert validate_category("ai_infrastructure", allowed) == "ai_infrastructure"
    assert validate_category("retail_pos", allowed) == "retail_pos"


def test_validate_category_out_of_set_becomes_unknown() -> None:
    allowed = allowed_categories(_play_spec())
    assert validate_category("free_text_hallucination", allowed) is None


def test_validate_category_none_stays_unknown() -> None:
    allowed = allowed_categories(_play_spec())
    assert validate_category(None, allowed) is None


def test_other_is_a_valid_served_category() -> None:
    allowed = allowed_categories(_play_spec())
    assert validate_category(OTHER_CATEGORY, allowed) == OTHER_CATEGORY
