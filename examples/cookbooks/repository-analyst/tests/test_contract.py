import hashlib
import json
import pathlib
import unittest
from query import execute

ROW = {
    "repo": "a/r",
    "owner": "a",
    "stars": 0,
    "forks": 1,
    "issues": 2,
    "license": None,
    "description": "東京",
    "url": "https://github.com/a/r",
}


class Contract(unittest.TestCase):
    def test_read(self):
        self.assertEqual(
            execute(
                {
                    "records": [ROW],
                    "sql": "SELECT repo, stars, license FROM repositories",
                }
            ),
            {"columns": ["repo", "stars", "license"], "rows": [["a/r", 0, None]]},
        )

    def test_denied(self):
        self.assertEqual(
            execute({"records": [ROW], "sql": "DELETE FROM repositories"}),
            {"error": "read_only"},
        )

    def test_literal_is_data(self):
        self.assertEqual(
            execute({"records": [ROW], "sql": "SELECT 'DROP TABLE' AS text"}),
            {"columns": ["text"], "rows": [["DROP TABLE"]]},
        )

    def test_receipt(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        receipt = json.loads((root / "verification.json").read_text())
        self.assertEqual(
            hashlib.sha256((root / "query.py").read_bytes()).hexdigest(),
            receipt["module_sha256"],
        )
        self.assertEqual(receipt["holdout"]["passed"], receipt["holdout"]["total"])
        self.assertTrue(receipt["live_checks"]["passed"])
        self.assertTrue(receipt["reference_evidence_ids"])
