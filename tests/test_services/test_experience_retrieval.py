import pytest

from src.services.experience_retrieval import ExperienceIndex, ReviewedTrajectory, deidentify


def test_deidentify_redacts_pii_and_secrets():
    value = deidentify("mail a@b.example phone 0912345678 api_key=secret-value")
    assert "a@b.example" not in value
    assert "0912345678" not in value
    assert "secret-value" not in value


def test_experience_index_is_release_scoped_and_navigation_only(tmp_path):
    path = tmp_path / "experience.jsonl"
    path.write_text(
        '{"trajectory_id":"t1","release_id":"snapshot-test","query":"mức hưởng BHYT","resolution_pattern":"hỏi ngày hiệu lực rồi truy hồi điều kiện","tags":["BHYT"],"reviewer":"reviewer-a","approved":true}\n',
        encoding="utf-8",
    )
    index = ExperienceIndex.load(path, release_id="snapshot-test")
    hits = index.search("quyền lợi BHYT")
    assert hits[0]["navigation_only"] is True
    assert "query" not in hits[0]


def test_unapproved_trajectory_is_rejected():
    row = ReviewedTrajectory("t1", "snapshot-test", "q", "p", (), "reviewer-a", False)
    with pytest.raises(ValueError, match="approved"):
        row.validate()


def test_redaction_marker_does_not_mask_remaining_pii():
    row = ReviewedTrajectory(
        "t1", "snapshot-test", "[EMAIL] and b@c.example", "pattern", (), "reviewer-a", True
    )
    with pytest.raises(ValueError, match="PII"):
        row.validate()
