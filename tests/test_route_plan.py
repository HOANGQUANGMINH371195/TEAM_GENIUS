from src.config import Settings
from src.domain.route_plan import build_route_plan


def test_social_route_has_no_external_providers():
    plan = build_route_plan("hi", settings=Settings())
    assert plan.route == "policy"
    assert plan.providers == ()
    assert plan.verifier_policy == "none"


def test_high_risk_temporal_route_is_bounded_and_graph_optional():
    settings = Settings(feature_graph_enabled=False)
    plan = build_route_plan("Theo luật hiện hành, văn bản nào còn hiệu lực?", settings=settings)
    assert plan.route == "temporal"
    assert plan.risk == "high"
    assert "neo4j" not in plan.providers
    assert plan.max_candidates <= 30


def test_open_currentness_question_does_not_pay_for_graph_hop():
    plan = build_route_plan(
        "Theo luật hiện hành, văn bản nào còn hiệu lực?",
        settings=Settings(feature_graph_enabled=True),
    )
    assert plan.route == "temporal"
    assert plan.providers == ("postgres", "qdrant")


def test_table_route_keeps_dense_fallback_when_no_typed_fact_exists():
    plan = build_route_plan("BHYT thanh toán bao nhiêu phần trăm chi phí?", settings=Settings())
    assert plan.route == "table"
    assert plan.providers == ("postgres", "qdrant")


def test_identifier_relationship_route_keeps_bounded_graphrag():
    plan = build_route_plan(
        "Văn bản 51/2024/QH15 sửa đổi văn bản nào và quan hệ thay thế ra sao?",
        settings=Settings(feature_graph_enabled=True),
    )
    # “thay thế” is both a temporal-status and relationship signal; the
    # temporal route still carries Neo4j so the bounded GraphRAG path is used.
    assert plan.route == "temporal"
    assert plan.providers == ("postgres", "qdrant", "neo4j")


def test_deep_route_is_reserved_for_explicit_multi_source_analysis():
    plan = build_route_plan("Phân tích sâu các quy định liên quan đến BHYT", settings=Settings())
    assert plan.route == "deep"
    assert plan.retrieval_budget_ms == 15_000
    assert "qdrant" in plan.providers


def test_global_route_is_bounded_and_keeps_graph_optional():
    settings = Settings(feature_graph_enabled=False)
    plan = build_route_plan("Tổng quan các quy định liên quan đến BHYT", settings=settings)
    assert plan.route == "global"
    assert plan.risk == "medium"
    assert "neo4j" not in plan.providers


def test_global_route_opt_in_adds_community_navigation_provider():
    settings = Settings(feature_global_search_enabled=True)
    plan = build_route_plan("Tổng quan các quy định liên quan đến BHYT", settings=settings)
    assert plan.route == "global"
    assert "community" in plan.providers
