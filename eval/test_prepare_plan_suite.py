import pytest

from eval.prepare_plan_suite import prepare


def _write(path, prefix, count):
    path.write_text(
        "\n".join(
            f'{{"case_id":"{prefix}-{i}","dataset_id":"snapshot-test","source_sha256":"hash"}}'
            for i in range(count)
        ) + "\n",
        encoding="utf-8",
    )


def test_prepare_suite_rejects_quota_shortfall(tmp_path):
    path = tmp_path / "core.jsonl"
    _write(path, "c", 1)
    with pytest.raises(ValueError, match="need at least"):
        prepare({"core": path}, release_id="snapshot-test", output=tmp_path / "out.jsonl")
