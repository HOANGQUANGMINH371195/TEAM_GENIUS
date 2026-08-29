from eval.ablations.auditor.evaluate import run_ablation


def test_auditor_ablation_requires_reviewed_panel_and_reports_catastrophic_errors(tmp_path):
    path = tmp_path / "claims.jsonl"
    path.write_text(
        "\n".join([
            '{"claim_id":"c1","source_sha256":"a","evidence_support":1,"faithfulness":0.9,"factuality":0.9,"completeness":0.9,"review_labels":[{"reviewer":"r1","accepted":true},{"reviewer":"r2","accepted":true}]}',
            '{"claim_id":"c2","source_sha256":"b","evidence_support":0,"faithfulness":0.1,"factuality":0.1,"completeness":0.1,"review_labels":[{"reviewer":"r1","accepted":false},{"reviewer":"r2","accepted":false}]}',
        ]) + "\n",
        encoding="utf-8",
    )
    report = run_ablation(path)
    assert report["variants"]["no_auditor"]["catastrophic_errors"] == 1
    assert report["variants"]["calibrated_auditor"]["catastrophic_errors"] == 0
