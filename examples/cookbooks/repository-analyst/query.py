"""Read-only SQLite query executor for the Crusoe repository analyst.

Implements query.execute(payload) -> JSON object per the contract:

  * Build an in-memory SQLite table ``repositories`` with eight columns
    using parameterized inserts.
  * Execute exactly one read-only SELECT or WITH query.
  * Enforce read-only access with a SQLite authorizer (no keyword
    blacklist): deny writes, DDL, PRAGMA, ATTACH, DETACH, load_extension,
    file functions (readfile/writefile) and transactions.
  * Cap results at 100 rows and 20 columns; abort expensive queries with
    a progress handler.

Only stdlib modules are used. sqlite3.connect is always ':memory:'.
"""

import sqlite3
from typing import Any, Dict, List

# Column definition: SQLite type name used for CREATE TABLE.
_COLUMNS = [
    ("repo", "TEXT"),
    ("owner", "TEXT"),
    ("stars", "INTEGER"),
    ("forks", "INTEGER"),
    ("issues", "INTEGER"),
    ("license", "TEXT"),
    ("description", "TEXT"),
    ("url", "TEXT"),
]

_INT_COLUMNS = frozenset({"stars", "forks", "issues"})

MAX_RECORDS = 500
MAX_STRING = 4000
MAX_SQL = 4096
MAX_ROWS = 100
MAX_COLUMNS = 20

# Progress handler: SQLite counts VM instructions; this threshold bounds
# total work per query so pathological queries terminate quickly.
_PROGRESS_LIMIT = 200_000

_INT64_MAX = 2 ** 53 - 1

# SQL functions that read/write files or load extensions; denied by name.
_DENIED_FUNCTIONS = frozenset({"readfile", "writefile", "load_extension"})


def _validate_record(record: Any) -> Dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    if set(record.keys()) != set(c for c, _ in _COLUMNS):
        raise ValueError("record field set mismatch")
    out: Dict[str, Any] = {}
    for name, _type in _COLUMNS:
        value = record[name]
        if name in _INT_COLUMNS:
            # bool is a subclass of int; reject it explicitly.
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if value < 0 or value > _INT64_MAX:
                raise ValueError(f"{name} out of range")
            out[name] = value
        else:
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or null")
            if value is not None and len(value) > MAX_STRING:
                raise ValueError(f"{name} too long")
            out[name] = value
    return out


def _authorizer(action, arg1, arg2, db_name, trigger):
    # Return SQLITE_DENY for anything that is not a plain read.
    if action == sqlite3.SQLITE_FUNCTION:
        # arg2 is the function name. Deny file-function and extension
        # SQL functions by name (defense in depth: they read/write files
        # even on builds with SQLITE_ENABLE_SQL_FUNCTION compiled in).
        if arg2 is not None and arg2.lower() in _DENIED_FUNCTIONS:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    read_only_codes = frozenset({
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        # SQLITE_RECURSIVE is emitted for recursive CTEs (still a read).
        sqlite3.SQLITE_RECURSIVE,
    })
    if action in read_only_codes:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _progress_handler():
    # Returning a non-zero value aborts the running statement.
    return 1


def _prepare_db(records: List[Dict[str, Any]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    # Initialize data FIRST with no restrictions, then lock the connection.
    cols_sql = ", ".join(f"{name} {type_}" for name, type_ in _COLUMNS)
    conn.execute(f"CREATE TABLE repositories ({cols_sql})")  # init only
    placeholders = ", ".join("?" * len(_COLUMNS))
    rows = [tuple(r[name] for name, _type in _COLUMNS) for r in records]
    conn.executemany(
        f"INSERT INTO repositories VALUES ({placeholders})", rows
    )
    conn.commit()

    # Lock down: query_only restricts to read statements; the authorizer
    # denies everything except plain reads.
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(_authorizer)
    conn.set_progress_handler(_progress_handler, _PROGRESS_LIMIT)
    return conn


def _serialize(value: Any) -> Any:
    # SQLite returns None, int, float, str or bytes. Normalize to JSON-safe
    # types: BLOBs to a hex string, and non-finite floats (inf/-inf/nan) to
    # None, since they have no legal JSON representation.
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        if value.is_integer():
            return int(value)
        return value
    return value


def execute(payload: Any) -> Dict[str, Any]:
    # --- payload shape validation ---
    if not isinstance(payload, dict):
        return {"error": "invalid_request"}

    records = payload.get("records")
    sql = payload.get("sql")

    if not isinstance(records, list):
        return {"error": "invalid_request"}
    if len(records) > MAX_RECORDS:
        return {"error": "limit_exceeded"}
    if not isinstance(sql, str) or not sql:
        return {"error": "invalid_request"}
    if len(sql) > MAX_SQL:
        return {"error": "invalid_request"}

    # Optional payload keys beyond records/sql? Contract defines only two;
    # extra keys are invalid.
    if set(payload.keys()) - {"records", "sql"}:
        return {"error": "invalid_request"}

    try:
        validated = [_validate_record(r) for r in records]
    except (ValueError, TypeError):
        return {"error": "invalid_request"}

    # --- build DB ---
    try:
        conn = _prepare_db(validated)
    except sqlite3.Error:
        return {"error": "invalid_request"}

    try:
        cursor = conn.execute(sql)
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        # The progress handler's interrupt is surfaced by sqlite3 as
        # OperationalError('interrupted').
        if "interrupt" in msg:
            return {"error": "limit_exceeded"}
        # A function-name authorizer deny surfaces as OperationalError
        # 'not authorized to use function: ...'.
        if "authoriz" in msg or "not authorized" in msg:
            return {"error": "read_only"}
        return {"error": "query_error"}
    except sqlite3.DatabaseError as e:
        msg = str(e).lower()
        # Reads denied by the authorizer surface as DatabaseError
        # 'not authorized' / 'authorization denied'.
        if "authoriz" in msg or "not authorized" in msg:
            return {"error": "read_only"}
        return {"error": "query_error"}

    # Reject non-row-returning statements (shouldn't reach here given
    # query_only, but be safe).
    try:
        columns = [d[0] for d in cursor.description or []]
    except Exception:
        return {"error": "query_error"}

    # An empty statement (whitespace, comment-only, or bare semicolon)
    # produces a cursor with no description; that is a malformed payload,
    # not a valid SELECT/WITH query.
    if cursor.description is None:
        return {"error": "invalid_request"}

    if len(columns) > MAX_COLUMNS:
        return {"error": "limit_exceeded"}

    try:
        rows = cursor.fetchmany(MAX_ROWS + 1)
    except sqlite3.OperationalError as e:
        if "interrupt" in str(e).lower():
            return {"error": "limit_exceeded"}
        return {"error": "query_error"}
    except sqlite3.DatabaseError:
        return {"error": "query_error"}

    if len(rows) > MAX_ROWS:
        return {"error": "limit_exceeded"}

    serialized = [[_serialize(v) for v in row] for row in rows]
    return {"columns": columns, "rows": serialized}