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


def test_table_route_requires_postgres_and_qdrant_is_not_needed_for_arithmetic():
    plan = build_route_plan("BHYT thanh toán bao nhiêu phần trăm chi phí?", settings=Settings())
    assert plan.route == "table"
    assert plan.providers == ("postgres",)
