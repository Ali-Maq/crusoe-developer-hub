# Repository Analyst — Read-Only SQL Queries

Port of a verified SQL analyst (originally written for Fireworks) to the
Crusoe inference stack. This module exposes a single read-only SQL query
entrypoint over an in-memory snapshot of a repository dataset.

## The real Crusoe client

This project queries a repository dataset using standard SQLite SQL. It is
designed to run against fixed model weights served through Crusoe
inference. To authenticate against the Crusoe API, set the environment
variable:

```sh
export CRUSOE_API_KEY="your-key-here"
```

The key is consumed by the hosting application (see `run.py` below); this
module itself performs no network access and therefore does not read the
key directly.

## run.py usage

The `execute(payload)` function is a pure, dependency-free function. A thin
driver such as `run.py` wraps it and forwards the result to the Crusoe
client:

```python
import json
import query

payload = json.loads(sys.stdin.read())
print(json.dumps(query.execute(payload)))
```

The real `run.py` is responsible for: loading the model/client, reading
`CRUSOE_API_KEY`, decoding the request payload, calling
`query.execute(payload)`, and serializing the returned JSON object.

## Dependencies

Standard library only. `query.py` is restricted to these imports:
`sqlite3`, `json`, `re`, `typing`, `collections`, and `math` (it currently
imports only `sqlite3` and `typing`). No third-party packages are required,
and there is no network, file, or subprocess access.

## Interface

```python
query.execute(payload) -> dict
```

`payload` is a JSON object with two fields:

* `records` — 0 to 500 JSON objects. Each object has exactly eight fields:
  `repo`, `owner`, `stars`, `forks`, `issues`, `license`, `description`,
  `url`. `repo`, `owner`, and `url` are strings; `stars`, `forks`, and
  `issues` are nonnegative integers up to 2^53 - 1; `license` and
  `description` are nullable strings. String lengths are limited to 4000
  characters. Booleans are not accepted as integers, and missing/extra
  fields are invalid.
* `sql` — a nonempty string of at most 4096 characters containing exactly
  one read-only SQLite `SELECT` or `WITH` query. Comments and literal words
  such as `DROP` are allowed.

On success the return value is:

```json
{"columns": ["repo", "stars"], "rows": [["fw-ai/cookbook", 228]]}
```

Order, nulls, zeros, Unicode, and duplicate rows are preserved exactly as
SQLite returns them.

## Read-only contract

The query is run against an in-memory SQLite database (`sqlite3.connect
(":memory:")`) containing a single table `repositories`. After the data is
loaded with parameterized inserts, the connection is locked down:

* `PRAGMA query_only = ON` restricts the connection to read statements.
* A SQLite authorizer denies writes, DDL, `PRAGMA`, `ATTACH`, `DETACH`,
  extension loading (`load_extension`), file functions, and transactions.
  Denial is bound to the SQLite operation (authorizer action codes), not a
  keyword blacklist.
* A progress handler aborts expensive queries (for example, unbounded
  recursive common table expressions) by raising an interrupt.

Results are limited to 100 rows and 20 columns; exceeding either (or a
payload of more than 500 records) is rejected.

## Error codes

`execute` never raises. On failure it returns exactly `{"error": "<code>"}`
with one of:

* `invalid_request` — malformed payload (bad types, missing/extra fields,
  oversized strings, oversized SQL, bool used as integer, out-of-range
  integers).
* `query_error` — SQL syntax error, or reference to a missing table or
  column.
* `read_only` — a denied SQL operation (write, DDL, PRAGMA, ATTACH/DETACH,
  extension/file function, transaction).
* `limit_exceeded` — result exceeds 100 rows or 20 columns, more than 500
  records supplied, or an expensive query was interrupted by the progress
  handler.

## Attribution

This port is derived from the Fireworks cookbook repository:

https://github.com/fw-ai/cookbook/blob/6ada4081cf7bdb5cec7dcf066dd29d784f55b232/training/case-studies/agentic_rl_text2sql/sql_tools.py

licensed under the Apache License 2.0. Only the schema/query/result-set
workflow is ported; the Fireworks GRPO training logic and the Chroma
retriever are explicitly out of scope, as is any Fireworks training or
embeddings functionality. The target runs Crusoe inference with fixed model
weights.