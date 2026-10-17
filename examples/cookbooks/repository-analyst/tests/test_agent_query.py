"""Tests for query.execute(payload) — the Crusoe repository analyst.

Contract: query.execute(payload) -> JSON object.
"""

import unittest
import query


def rec(**overrides):
    base = {
        "repo": "fw-ai/cookbook",
        "owner": "fw-ai",
        "stars": 228,
        "forks": 50,
        "issues": 12,
        "license": "Apache-2.0",
        "description": "a cookbook",
        "url": "https://github.com/fw-ai/cookbook",
    }
    base.update(overrides)
    return base


class TestExecuteValid(unittest.TestCase):
    def test_select_all_returns_columns_and_rows(self):
        payload = {
            "records": [rec()],
            "sql": "SELECT repo, owner, stars FROM repositories",
        }
        result = query.execute(payload)
        self.assertEqual(result["columns"], ["repo", "owner", "stars"])
        self.assertEqual(result["rows"], [["fw-ai/cookbook", "fw-ai", 228]])

    def test_preserves_null_zero_unicode_and_duplicates(self):
        payload = {
            "records": [
                rec(repo="héllo/wörld", license=None, issues=0, stars=0),
                rec(repo="dup/x"),
                rec(repo="dup/x", stars=0),
            ],
            "sql": "SELECT license, issues, repo, stars FROM repositories",
        }
        result = query.execute(payload)
        self.assertEqual(
            result["rows"],
            [
                [None, 0, "héllo/wörld", 0],
                ["Apache-2.0", 12, "dup/x", 228],
                ["Apache-2.0", 12, "dup/x", 0],
            ],
        )

    def test_order_by_respected(self):
        payload = {
            "records": [
                rec(repo="a", stars=1),
                rec(repo="b", stars=3),
                rec(repo="c", stars=2),
            ],
            "sql": "SELECT repo FROM repositories ORDER BY stars DESC",
        }
        result = query.execute(payload)
        self.assertEqual(result["rows"], [["b"], ["c"], ["a"]])

    def test_with_cte(self):
        payload = {
            "records": [rec(), rec(repo="b/x", stars=10)],
            "sql": "WITH s AS (SELECT repo, stars FROM repositories) "
            "SELECT * FROM s WHERE stars > 500",
        }
        result = query.execute(payload)
        self.assertEqual(result["rows"], [])

    def test_empty_records(self):
        payload = {"records": [], "sql": "SELECT count(*) AS n FROM repositories"}
        result = query.execute(payload)
        self.assertEqual(result["rows"], [[0]])

    def test_null_license_description_allowed(self):
        payload = {
            "records": [rec(license=None, description=None)],
            "sql": "SELECT license, description FROM repositories",
        }
        result = query.execute(payload)
        self.assertEqual(result["rows"], [[None, None]])


class TestExecuteInvalidPayload(unittest.TestCase):
    def _bad(self, payload):
        r = query.execute(payload)
        self.assertEqual(r, {"error": "invalid_request"})

    def test_non_dict_payload(self):
        self._bad([])

    def test_missing_records(self):
        self._bad({"sql": "SELECT 1"})

    def test_missing_sql(self):
        self._bad({"records": []})

    def test_empty_sql(self):
        self._bad({"records": [], "sql": ""})

    def test_whitespace_only_sql(self):
        self._bad({"records": [], "sql": " "})

    def test_whitespace_multiple_sql(self):
        self._bad({"records": [], "sql": "   \n\t  "})

    def test_comment_only_sql(self):
        self._bad({"records": [], "sql": "-- just a comment"})

    def test_semicolon_only_sql(self):
        self._bad({"records": [], "sql": ";"})

    def test_sql_not_string(self):
        self._bad({"records": [], "sql": 5})

    def test_records_not_list(self):
        self._bad({"records": {}, "sql": "SELECT 1"})

    def test_record_not_object(self):
        self._bad({"records": [1], "sql": "SELECT 1"})

    def test_extra_field_invalid(self):
        self._bad({"records": [rec(extra=1)], "sql": "SELECT 1"})

    def test_missing_field_invalid(self):
        r = rec()
        del r["repo"]
        self._bad({"records": [r], "sql": "SELECT 1"})

    def test_bool_not_integer(self):
        self._bad({"records": [rec(stars=True)], "sql": "SELECT 1"})

    def test_negative_integer(self):
        self._bad({"records": [rec(stars=-1)], "sql": "SELECT 1"})

    def test_non_integer_stars(self):
        self._bad({"records": [rec(stars="10")], "sql": "SELECT 1"})

    def test_string_too_long(self):
        self._bad({"records": [rec(repo="x" * 4001)], "sql": "SELECT 1"})

    def test_sql_too_long(self):
        self._bad({"records": [], "sql": "SELECT " + ("1," * 2100)})


class TestExecuteQueryError(unittest.TestCase):
    def test_syntax_error(self):
        r = query.execute({"records": [], "sql": "SELECT FROM"})
        self.assertEqual(r, {"error": "query_error"})

    def test_missing_table(self):
        r = query.execute({"records": [], "sql": "SELECT * FROM nope"})
        self.assertEqual(r, {"error": "query_error"})

    def test_missing_column(self):
        r = query.execute({"records": [], "sql": "SELECT nonexistent FROM repositories"})
        self.assertEqual(r, {"error": "query_error"})


class TestExecuteReadOnly(unittest.TestCase):
    def _ro(self, sql):
        r = query.execute({"records": [rec()], "sql": sql})
        self.assertEqual(r, {"error": "read_only"})

    def test_insert_denied(self):
        self._ro("INSERT INTO repositories (repo) VALUES ('x')")

    def test_update_denied(self):
        self._ro("UPDATE repositories SET stars = 1")

    def test_delete_denied(self):
        self._ro("DELETE FROM repositories")

    def test_create_table_denied(self):
        self._ro("CREATE TABLE x (a INT)")

    def test_drop_table_denied(self):
        self._ro("DROP TABLE repositories")

    def test_pragma_denied(self):
        self._ro("PRAGMA table_info(repositories)")

    def test_attach_denied(self):
        self._ro("ATTACH DATABASE ':memory:' AS x")

    def test_detach_denied(self):
        self._ro("DETACH DATABASE x")

    def test_transaction_denied(self):
        self._ro("BEGIN")

    def test_load_extension_denied(self):
        self._ro("SELECT load_extension('/tmp/x')")

    def test_drop_literal_word_is_allowed(self):
        # a SELECT whose result contains the literal word DROP must be legal
        r = query.execute(
            {"records": [], "sql": "SELECT 'DROP' AS word"}
        )
        self.assertEqual(r["rows"], [["DROP"]])


class TestExecuteLimits(unittest.TestCase):
    def test_over_100_rows_returns_limit_exceeded(self):
        records = [
            rec(repo=f"r{i}", stars=i) for i in range(101)
        ]
        r = query.execute(
            {"records": records, "sql": "SELECT * FROM repositories"}
        )
        self.assertEqual(r, {"error": "limit_exceeded"})

    def test_exactly_100_rows_ok(self):
        records = [rec(repo=f"r{i}", stars=i) for i in range(100)]
        r = query.execute(
            {"records": records, "sql": "SELECT * FROM repositories"}
        )
        self.assertEqual(len(r["rows"]), 100)

    def test_over_20_columns_returns_limit_exceeded(self):
        cols = ", ".join(["stars"] * 21)
        r = query.execute(
            {"records": [rec()], "sql": f"SELECT {cols} FROM repositories"}
        )
        self.assertEqual(r, {"error": "limit_exceeded"})

    def test_expensive_recursive_query_terminates(self):
        r = query.execute(
            {
                "records": [rec()],
                "sql": (
                    "WITH RECURSIVE c(x) AS "
                    "(SELECT 1 UNION ALL SELECT x+1 FROM c) "
                    "SELECT * FROM c"
                ),
            }
        )
        self.assertEqual(r, {"error": "limit_exceeded"})

    def test_expensive_aggregating_recursive_query_is_limit_exceeded(self):
        # The recursive expansion is materialized inside the aggregate, so
        # the progress handler (not the row cap) must terminate it.
        r = query.execute(
            {
                "records": [],
                "sql": (
                    "WITH RECURSIVE c(x) AS "
                    "(SELECT 1 UNION ALL SELECT x+1 FROM c) "
                    "SELECT count(*) FROM c"
                ),
            }
        )
        self.assertEqual(r, {"error": "limit_exceeded"})

    def test_readfile_denied(self):
        with self.subTest("readfile"):
            r = query.execute(
                {"records": [], "sql": "SELECT readfile('/etc/passwd')"}
            )
            self.assertIn(r, [{"error": "read_only"}, {"error": "query_error"}])

    def test_writefile_denied(self):
        r = query.execute(
            {"records": [], "sql": "SELECT writefile('/tmp/x', 'x')"}
        )
        self.assertIn(r, [{"error": "read_only"}, {"error": "query_error"}])

    def test_over_500_records_rejected(self):
        records = [rec() for _ in range(501)]
        r = query.execute(
            {"records": records, "sql": "SELECT count(*) FROM repositories"}
        )
        self.assertEqual(r, {"error": "limit_exceeded"})


class TestExecuteJsonSafe(unittest.TestCase):
    def test_result_is_json_serializable(self):
        import json

        for sql, _ in [
            ("SELECT x'4142'", None),  # BLOB via hex literal
            ("SELECT 1e309", None),  # positive infinity
            ("SELECT -1e309", None),  # negative infinity
            ("SELECT zeroblob(4)", None),  # BLOB via function
            ("SELECT CAST('ab' AS BLOB)", None),  # BLOB via cast
        ]:
            with self.subTest(sql=sql):
                r = query.execute({"records": [], "sql": sql})
                json.dumps(r)  # must not raise

    def test_blob_becomes_string(self):
        r = query.execute({"records": [], "sql": "SELECT x'4142'"})
        self.assertEqual(r["rows"], [["4142"]])

    def test_infinity_becomes_null(self):
        r = query.execute({"records": [], "sql": "SELECT 1e309"})
        self.assertEqual(r["rows"], [[None]])

    def test_ordinary_float_preserved(self):
        # non-integral finite floats stay floats
        r = query.execute({"records": [], "sql": "SELECT 1.5"})
        self.assertEqual(r["rows"], [[1.5]])


if __name__ == "__main__":
    unittest.main()