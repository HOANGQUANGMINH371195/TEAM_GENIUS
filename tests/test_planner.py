from src.models.graph import RetrievalResult
from src.services.planner import GroundedPlan, evidence_gap_plan, followup_queries


def test_planner_only_reports_query_derived_gaps():
    evidence = [RetrievalResult(chunk_id="c", document_id="d", content="mức hưởng 80%")]
    plan = evidence_gap_plan("mức hưởng và điều kiện", evidence)
    assert plan.enabled is True
    assert {"điều", "kiện"}.issubset(plan.missing_facts)
    assert plan.fanout == 3 and plan.depth == 2


def test_planner_can_be_disabled_for_fast_route():
    plan = evidence_gap_plan("câu hỏi", [], enabled=False)
    assert plan.enabled is False
    assert plan.missing_facts == ()


def test_followups_are_query_derived_and_bounded():
    plan = GroundedPlan(True, ("điều kiện", "ngoại lệ", "mức hưởng", "thừa"), fanout=99)
    queries = followup_queries("BHYT hiện hành", plan)
    assert len(queries) == 3
    assert all(query.startswith("BHYT hiện hành ") for query in queries)
    assert followup_queries("", plan) == ()
