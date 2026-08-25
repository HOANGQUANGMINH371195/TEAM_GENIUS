from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scripts.reuse_embedding_backup import (
    input_sha256,
    input_text,
    parse_vector,
    reusable_artifact_vectors,
    reusable_vectors,
)


class ReuseEmbeddingBackupTests(unittest.TestCase):
    def test_input_digest_matches_canonical_embedding_text(self) -> None:
        text = input_text({"section_label": "Điều 1", "text": "Nội dung"})
        self.assertEqual(text, "Điều 1\n\nNội dung")
        self.assertEqual(len(input_sha256(text)), 64)

    def test_only_embedded_semantic_rows_are_reusable(self) -> None:
        vector = "[" + ",".join(["0.25"] * 1536) + "]"
        backup = {"tables": {"chunks": [
            {"chunk_id": "kept", "semantic_eligible": True,
             "embedded_input_sha256": "a" * 64, "embedding": vector},
            {"chunk_id": "lexical", "semantic_eligible": False,
             "embedded_input_sha256": "b" * 64, "embedding": vector},
        ]}}
        result = reusable_vectors(backup)
        self.assertEqual(set(result), {("kept", "a" * 64)})
        self.assertEqual(result[("kept", "a" * 64)].dtype, np.float32)

    def test_invalid_vector_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_vector("[1,2]")

    def test_local_artifact_reuses_by_input_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp)
            (artifact / "manifest.json").write_text(json.dumps({"rows": 1}), encoding="utf-8")
            np.save(artifact / "embeddings.float32.npy", np.ones((1, 1536), dtype=np.float32))
            (artifact / "passages.jsonl").write_text(
                json.dumps({"passage_id": "old-id", "input_sha256": "a" * 64}) + "\n",
                encoding="utf-8",
            )

            reused = reusable_artifact_vectors(artifact)

            self.assertEqual(set(reused), {"a" * 64})
            self.assertEqual(reused["a" * 64].shape, (1536,))


if __name__ == "__main__":
    unittest.main()
