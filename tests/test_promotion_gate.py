from scripts.verify_promotion_gate import _status_rows


def test_promotion_gate_reads_current_vietnamese_status_ledger() -> None:
    plan = """
| Hạng mục | Trạng thái | Điều kiện đóng |
|---|---|---|
| Accuracy/latency | **Đang fail live gate** | Chạy lại |
| API | Đã có | Smoke |

## Next section
"""

    rows = _status_rows(plan)

    assert rows == [
        {
            "area": "Accuracy/latency",
            "evidence": "",
            "status": "**Đang fail live gate**",
        },
        {"area": "API", "evidence": "", "status": "Đã có"},
    ]


def test_promotion_gate_keeps_legacy_english_ledger_compatible() -> None:
    plan = """
| Area | Current evidence | Status |
|---|---|---|
| Accuracy | live | partial |

Do not run promotion
"""

    assert _status_rows(plan) == [
        {"area": "Accuracy", "evidence": "live", "status": "partial"}
    ]
