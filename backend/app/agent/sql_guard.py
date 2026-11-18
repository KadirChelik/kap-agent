"""
Strict SQL validation and rewrite sandbox for `run_sql`.

Rules enforced in code before any query touches SQLite:
  - Must be a SINGLE statement (no embedded `;`).
  - Comments (`--`, `/*`, `*/`) are forbidden.
  - Must start with `SELECT` (CTEs `WITH`, mutations, DDL, `PRAGMA`, `ATTACH` rejected).
  - Table whitelist enforced (`disclosures`, `disclosures_fts`, `dividend_events`,
    `financial_reports`, `material_events`). Access to `review_flags` or
    `sqlite_master` / `sqlite_schema` is rejected.
  - `LIMIT <= max_limit` (50) is mandatory (added if absent, clamped if larger).
"""

from __future__ import annotations

import re

ALLOWED_SQL_TABLES: frozenset[str] = frozenset(
    {
        "disclosures",
        "disclosures_fts",
        "dividend_events",
        "financial_reports",
        "material_events",
    }
)

FORBIDDEN_SQL_IDENTIFIERS: frozenset[str] = frozenset(
    {
        "review_flags",
        "sqlite_master",
        "sqlite_schema",
        "sqlite_temp_master",
        "sqlite_temp_schema",
        "sqlite_stat1",
        "sqlite_stat4",
    }
)

FORBIDDEN_SQL_KEYWORDS: frozenset[str] = frozenset(
    {
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "create",
        "attach",
        "detach",
        "pragma",
        "vacuum",
        "replace",
        "reindex",
        "into",
        "load_extension",
        "transaction",
        "begin",
        "commit",
        "rollback",
        "savepoint",
    }
)

_SINGLE_QUOTED_STR_RE = re.compile(r"'(?:''|[^'])*'")
_DOUBLE_QUOTED_IDENT_RE = re.compile(r'"(?:""|[^"])*"')
_WORD_TOKEN_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")
_TABLE_REF_RE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)", re.I)
_TRAILING_LIMIT_RE = re.compile(
    r"\blimit\s+(-?\d+)(\s*(?:,|offset)\s*-?\d+)?\s*$",
    re.I,
)


class SQLGuardError(ValueError):
    """Raised when an agent-generated SQL query violates read-only policy."""


def validate_and_rewrite_sql(raw_sql: str, max_limit: int = 50) -> str:
    """Validate single-SELECT read-only policy and enforce `LIMIT <= max_limit`."""
    if not raw_sql or not raw_sql.strip():
        raise SQLGuardError("SQL query cannot be empty.")

    cleaned = raw_sql.strip()
    if "--" in cleaned or "/*" in cleaned or "*/" in cleaned:
        raise SQLGuardError("SQL comments (-- or /* */) are not allowed.")

    # Strip at most one trailing semicolon, then forbid any remaining semicolon.
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if ";" in cleaned:
        raise SQLGuardError("Only a single SELECT statement is allowed (no multiple statements).")

    if not re.match(r"^select\b", cleaned, re.I):
        raise SQLGuardError("Only a single SELECT statement starting with SELECT is allowed.")

    # Strip quoted literals before scanning tokens so text values do not false-trigger.
    unquoted = _SINGLE_QUOTED_STR_RE.sub(" '' ", cleaned)
    tokens = {tok.lower() for tok in _WORD_TOKEN_RE.findall(unquoted)}

    bad_kw = sorted(tokens & FORBIDDEN_SQL_KEYWORDS)
    if bad_kw:
        raise SQLGuardError(f"Forbidden SQL keyword(s): {', '.join(bad_kw)}.")

    bad_ident = sorted(tokens & FORBIDDEN_SQL_IDENTIFIERS)
    if bad_ident:
        raise SQLGuardError(f"Access to restricted table/catalog is forbidden: {', '.join(bad_ident)}.")

    # Also check quoted identifiers just in case someone wrote "review_flags"
    for qmatch in _DOUBLE_QUOTED_IDENT_RE.findall(cleaned):
        inner = qmatch[1:-1].strip().lower()
        if inner in FORBIDDEN_SQL_IDENTIFIERS:
            raise SQLGuardError(f"Access to restricted table is forbidden: {inner}.")

    referenced_tables = [m.group(1).split(".")[-1].lower() for m in _TABLE_REF_RE.finditer(unquoted)]
    if not referenced_tables:
        raise SQLGuardError("SELECT query must reference at least one allowed FROM table.")

    disallowed_tables = [t for t in referenced_tables if t not in ALLOWED_SQL_TABLES]
    if disallowed_tables:
        raise SQLGuardError(
            f"Table(s) not in allowlist ({sorted(ALLOWED_SQL_TABLES)}): {', '.join(disallowed_tables)}."
        )

    limit_match = _TRAILING_LIMIT_RE.search(unquoted)
    if limit_match:
        limit_val = int(limit_match.group(1))
        if limit_val <= 0:
            raise SQLGuardError("LIMIT must be a positive integer.")
        if limit_val > max_limit:
            # Replace only the trailing LIMIT number safely from cleaned SQL
            cleaned = _TRAILING_LIMIT_RE.sub(
                lambda m: f"LIMIT {max_limit}{m.group(2) or ''}",
                cleaned,
                count=1,
            )
    else:
        cleaned = f"{cleaned} LIMIT {max_limit}"

    return cleaned
