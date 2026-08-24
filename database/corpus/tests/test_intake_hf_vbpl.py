from __future__ import annotations

import unittest

from database.corpus.intake_hf_vbpl import normalize_document_number, select_records


class HfVbplIntakeTest(unittest.TestCase):
    def test_selects_only_requested_central_vbpl_record(self) -> None:
        row = {
            "item_id": "172923", "scope": "trung_uong", "source": "vbpl.vn",
            "source_url": "https://vbpl.vn/van-ban/chi-tiet/x--172923", "api_url": "https://vbpl.vn/api/172923",
            "doc_number": ["51/2024/QH15"], "title": "Luật BHYT", "legal_type": "Luật",
            "issuing_authority": "Quốc hội", "issue_date": "2024-11-27", "legal_area": "BHYT", "markdown": "Điều 1.",
        }
        records = select_records([row], {"51/2024/QH15"})
        self.assertEqual(records[0]["review_status"], "needs_official_status_verification")
        self.assertEqual(records[0]["promotion_status"], "review_only_not_indexable")
        self.assertEqual(records[0]["source_item_id"], "172923")

    def test_rejects_local_scope(self) -> None:
        row = {
            "item_id": "1", "scope": "dia_phuong", "source": "vbpl.vn",
            "source_url": "https://vbpl.vn/x--1", "doc_number": ["1/2025/QĐ-UBND"], "markdown": "x",
        }
        with self.assertRaisesRegex(ValueError, "non-central"):
            select_records([row], {"1/2025/QĐ-UBND"})

    def test_normalizes_only_spacing_and_case(self) -> None:
        self.assertEqual(normalize_document_number(" 51 / 2024 / qh15 "), "51/2024/QH15")


if __name__ == "__main__":
    unittest.main()
