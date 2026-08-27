import json

import pytest

from eval.prepare_plan_suite import _read, prepare


def _write(path, prefix, count):
    path.write_text(
        "\n".join(
            json.dumps({
                "case_id": f"{prefix}-{i}",
                "dataset_id": "snapshot-test",
                "source_sha256": "hash",
                "review_status": "accepted",
                "review_labels": [{"reviewer": "legal-a"}, {"reviewer": "legal-b"}],
            })
            for i in range(count)
        ) + "\n",
        encoding="utf-8",
    )


def test_prepare_suite_rejects_quota_shortfall(tmp_path):
    path = tmp_path / "core.jsonl"
    _write(path, "c", 1)
    with pytest.raises(ValueError, match="need at least"):
        prepare({"core": path}, release_id="snapshot-test", output=tmp_path / "out.jsonl")


def test_prepare_suite_rejects_machine_only_rows(tmp_path):
    path = tmp_path / "core.jsonl"
    path.write_text(
        '{"case_id":"c-1","dataset_id":"snapshot-test","source_sha256":"hash"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="review_status=accepted"):
        _read(path, release_id="snapshot-test")
