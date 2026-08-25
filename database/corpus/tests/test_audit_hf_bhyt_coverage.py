from __future__ import annotations

import unittest

from database.corpus.audit_hf_bhyt_coverage import is_current_central_bhyt, missing_signatures


class HfBHYTCoverageAuditTest(unittest.TestCase):
    def test_selects_current_central_bhyt_only(self) -> None:
        self.assertTrue(is_current_central_bhyt({
            "pham_vi": "Trung ương", "tinh_trang_hieu_luc": "Còn hiệu lực", "title": "Quy định bảo hiểm y tế",
        }))
        self.assertFalse(is_current_central_bhyt({
            "pham_vi": "Địa phương", "tinh_trang_hieu_luc": "Còn hiệu lực", "title": "bảo hiểm y tế",
        }))
        self.assertFalse(is_current_central_bhyt({
            "pham_vi": "Trung ương", "tinh_trang_hieu_luc": "Hết hiệu lực", "title": "bảo hiểm y tế",
        }))

    def test_signature_variants_prevent_false_missing_result(self) -> None:
        source = [{
            "id": "172923", "so_ky_hieu": "Luật số 51/2024/QH15", "pham_vi": "Trung ương",
            "tinh_trang_hieu_luc": "Còn hiệu lực", "title": "Luật Bảo hiểm y tế",
        }]
        candidate = [{"so_ky_hieu": "51/2024/QH15"}]
        self.assertEqual(missing_signatures(source, candidate), [])

    def test_reports_missing_central_document(self) -> None:
        source = [{
            "id": "x", "so_ky_hieu": "188/2025/NĐ-CP", "pham_vi": "Trung ương",
            "tinh_trang_hieu_luc": "Còn hiệu lực", "title": "Bảo hiểm y tế",
        }]
        self.assertEqual(len(missing_signatures(source, [])), 1)


if __name__ == "__main__":
    unittest.main()
