#!/usr/bin/env python3
"""Versioned SQLite schema management for Hermes Tech.

Production upgrades are intentionally separated from normal collector startup.
Use ``tools/sqlite_schema.py`` for read-only preflight and evidence-producing
application. New, empty databases may be initialized automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

CURRENT_SCHEMA_VERSION = 3


class SchemaError(RuntimeError):
    """Base class for schema validation and migration failures."""


class SchemaUpgradeRequired(SchemaError):
    """Raised when a non-empty database needs an approved upgrade."""


class UnexpectedSchemaError(SchemaError):
    """Raised when a database does not match a supported schema."""


@dataclass(frozen=True)
class Migration:
    from_version: int
    to_version: int
    name: str
    statements: tuple[str, ...]


ARTICLE_COLUMNS_BY_VERSION: dict[int, tuple[str, ...]] = {
    1: (
        "id", "source", "title", "link", "published", "summary",
        "fetched_at", "digest_date",
    ),
    2: (
        "id", "source", "title", "link", "published", "summary",
        "fetched_at", "digest_date", "category", "content",
    ),
    3: (
        "id", "source", "title", "link", "published", "summary",
        "fetched_at", "digest_date", "category", "content",
        "primary_category", "topic_key", "routed_at",
    ),
}

SOURCE_COLUMNS = (
    "name", "fetch_ok", "fetch_fail", "collected", "picked",
)

ARTICLE_COLUMN_SPECS_BY_VERSION: dict[
    int, dict[str, tuple[str, int, str | None, int]]
] = {
    version: {
        "id": ("INTEGER", 0, None, 1),
        "source": ("TEXT", 1, None, 0),
        "title": ("TEXT", 1, None, 0),
        "link": ("TEXT", 1, None, 0),
        "published": ("TEXT", 0, None, 0),
        "summary": ("TEXT", 0, None, 0),
        "fetched_at": ("TEXT", 1, None, 0),
        "digest_date": ("TEXT", 0, None, 0),
        **(
            {
                "category": ("TEXT", 0, "'devops'", 0),
                "content": ("TEXT", 0, None, 0),
            }
            if version >= 2
            else {}
        ),
        **(
            {
                "primary_category": ("TEXT", 0, None, 0),
                "topic_key": ("TEXT", 0, None, 0),
                "routed_at": ("TEXT", 0, None, 0),
            }
            if version >= 3
            else {}
        ),
    }
    for version in ARTICLE_COLUMNS_BY_VERSION
}

SOURCE_COLUMN_SPECS: dict[str, tuple[str, int, str | None, int]] = {
    "name": ("TEXT", 0, None, 1),
    "fetch_ok": ("INTEGER", 0, "0", 0),
    "fetch_fail": ("INTEGER", 0, "0", 0),
    "collected": ("INTEGER", 0, "0", 0),
    "picked": ("INTEGER", 0, "0", 0),
}

REQUIRED_INDEXES_BY_VERSION: dict[int, dict[str, tuple[str, ...]]] = {
    1: {"idx_articles_fetched": ("fetched_at",)},
    2: {
        "idx_articles_fetched": ("fetched_at",),
        "idx_articles_cat": ("category",),
    },
    3: {
        "idx_articles_fetched": ("fetched_at",),
        "idx_articles_cat": ("category",),
        "idx_articles_primary_cat": ("primary_category",),
        "idx_articles_topic_key": ("topic_key",),
    },
}

MIGRATIONS: dict[int, Migration] = {
    0: Migration(
        0,
        1,
        "create-base-schema",
        (
            """CREATE TABLE articles (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT NOT NULL UNIQUE,
                published TEXT,
                summary TEXT,
                fetched_at TEXT NOT NULL,
                digest_date TEXT
            )""",
            """CREATE TABLE sources (
                name TEXT PRIMARY KEY,
                fetch_ok INTEGER DEFAULT 0,
                fetch_fail INTEGER DEFAULT 0,
                collected INTEGER DEFAULT 0,
                picked INTEGER DEFAULT 0
            )""",
            "CREATE INDEX idx_articles_fetched ON articles(fetched_at)",
        ),
    ),
    1: Migration(
        1,
        2,
        "add-category-and-content",
        (
            "ALTER TABLE articles ADD COLUMN category TEXT DEFAULT 'devops'",
            "ALTER TABLE articles ADD COLUMN content TEXT",
            "CREATE INDEX idx_articles_cat ON articles(category)",
        ),
    ),
    2: Migration(
        2,
        3,
        "add-routing-columns",
        (
            "ALTER TABLE articles ADD COLUMN primary_category TEXT",
            "ALTER TABLE articles ADD COLUMN topic_key TEXT",
            "ALTER TABLE articles ADD COLUMN routed_at TEXT",
            "CREATE INDEX idx_articles_primary_cat ON articles(primary_category)",
            "CREATE INDEX idx_articles_topic_key ON articles(topic_key)",
        ),
    ),
}


def _pragma_int(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute(f"PRAGMA {name}").fetchone()
    if row is None:
        raise SchemaError(f"PRAGMA {name} neatgrieza vērtību")
    return int(row[0])


def user_version(conn: sqlite3.Connection) -> int:
    return _pragma_int(conn, "user_version")


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    if version < 0 or version > CURRENT_SCHEMA_VERSION:
        raise ValueError(f"nederīgs schema version: {version}")
    conn.execute(f"PRAGMA user_version = {version}")


def _user_tables(conn: sqlite3.Connection) -> tuple[str, ...]:
    rows = conn.execute(
        """SELECT name FROM sqlite_master
           WHERE type='table' AND name NOT LIKE 'sqlite_%'
           ORDER BY name"""
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _column_rows(
    conn: sqlite3.Connection,
    table: str,
) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(row) for row in conn.execute(f"PRAGMA table_info({table})"))


def _column_names(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in _column_rows(conn, table))


def _index_specs(
    conn: sqlite3.Connection,
    table: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in conn.execute(f"PRAGMA index_list({table})"):
        name = str(row[1])
        columns = tuple(
            str(info[2])
            for info in conn.execute(f"PRAGMA index_info({json.dumps(name)})")
        )
        result[name] = {
            "unique": bool(row[2]),
            "origin": str(row[3]) if len(row) > 3 else "",
            "partial": bool(row[4]) if len(row) > 4 else False,
            "columns": columns,
        }
    return result


def _has_unique_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> bool:
    return any(
        spec["unique"]
        and spec["columns"] == columns
        and not spec["partial"]
        for spec in _index_specs(conn, table).values()
    )


def _normalize_default(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    while len(text) >= 2 and text[0] == "(" and text[-1] == ")":
        text = text[1:-1].strip()
    return text


def _assert_column_specs(
    rows: tuple[tuple[Any, ...], ...],
    expected: dict[str, tuple[str, int, str | None, int]],
    *,
    table: str,
) -> None:
    actual = {
        str(row[1]): (
            str(row[2]).upper(),
            int(row[3]),
            _normalize_default(row[4]),
            int(row[5]),
        )
        for row in rows
    }
    if actual != expected:
        raise UnexpectedSchemaError(
            f"{table} kolonnu tipi/constraints neatbilst līgumam: {actual}"
        )


def _assert_core_constraints(conn: sqlite3.Connection) -> None:
    article_rows = _column_rows(conn, "articles")
    source_rows = _column_rows(conn, "sources")
    article_by_name = {str(row[1]): row for row in article_rows}
    source_by_name = {str(row[1]): row for row in source_rows}

    for name in ("source", "title", "link", "fetched_at"):
        if int(article_by_name[name][3]) != 1:
            raise UnexpectedSchemaError(f"articles.{name} nav NOT NULL")
    if int(article_by_name["id"][5]) != 1:
        raise UnexpectedSchemaError("articles.id nav PRIMARY KEY")
    if int(source_by_name["name"][5]) != 1:
        raise UnexpectedSchemaError("sources.name nav PRIMARY KEY")
    if not _has_unique_columns(conn, "articles", ("link",)):
        raise UnexpectedSchemaError("articles.link UNIQUE līgums nav atrasts")


def _assert_required_indexes(conn: sqlite3.Connection, version: int) -> None:
    specs = _index_specs(conn, "articles")
    for name, columns in REQUIRED_INDEXES_BY_VERSION[version].items():
        spec = specs.get(name)
        if spec is None:
            raise UnexpectedSchemaError(f"trūkst indeksa {name}")
        if spec["columns"] != columns or spec["unique"] or spec["partial"]:
            raise UnexpectedSchemaError(
                f"indekss {name} neatbilst līgumam: {spec}"
            )


def quick_check(conn: sqlite3.Connection) -> tuple[str, ...]:
    rows = tuple(str(row[0]) for row in conn.execute("PRAGMA quick_check"))
    if rows != ("ok",):
        raise SchemaError(f"PRAGMA quick_check neizdevās: {rows}")
    return rows


def assert_schema(conn: sqlite3.Connection, version: int) -> None:
    if version not in ARTICLE_COLUMNS_BY_VERSION:
        raise UnexpectedSchemaError(f"neatbalstīts schema version {version}")
    tables = _user_tables(conn)
    if tables != ("articles", "sources"):
        raise UnexpectedSchemaError(
            f"negaidīts tabulu komplekts: {tables}; "
            "gaidīts ('articles', 'sources')"
        )
    article_columns = _column_names(conn, "articles")
    if article_columns != ARTICLE_COLUMNS_BY_VERSION[version]:
        raise UnexpectedSchemaError(
            f"articles kolonnas neatbilst v{version}: {article_columns}"
        )
    source_columns = _column_names(conn, "sources")
    if source_columns != SOURCE_COLUMNS:
        raise UnexpectedSchemaError(
            f"sources kolonnas neatbilst līgumam: {source_columns}"
        )
    _assert_column_specs(
        _column_rows(conn, "articles"),
        ARTICLE_COLUMN_SPECS_BY_VERSION[version],
        table="articles",
    )
    _assert_column_specs(
        _column_rows(conn, "sources"),
        SOURCE_COLUMN_SPECS,
        table="sources",
    )
    _assert_core_constraints(conn)
    _assert_required_indexes(conn, version)


def infer_legacy_version(conn: sqlite3.Connection) -> int:
    tables = _user_tables(conn)
    if not tables:
        return 0
    if tables != ("articles", "sources"):
        raise UnexpectedSchemaError(
            f"nevar noteikt legacy shēmu: tabulas={tables}"
        )
    article_columns = _column_names(conn, "articles")
    matching = [
        version
        for version, columns in ARTICLE_COLUMNS_BY_VERSION.items()
        if article_columns == columns
    ]
    if len(matching) != 1:
        raise UnexpectedSchemaError(
            "nevar noteikt legacy shēmu pēc articles kolonnām: "
            f"{article_columns}"
        )
    version = matching[0]
    assert_schema(conn, version)
    return version


def _snapshot_existing_data(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = set(_user_tables(conn))
    snapshot: dict[str, Any] = {}
    if "articles" in tables:
        columns = _column_names(conn, "articles")
        quoted = ", ".join(f'"{name}"' for name in columns)
        snapshot["articles_columns"] = columns
        snapshot["articles_rows"] = tuple(
            tuple(row)
            for row in conn.execute(
                f"SELECT {quoted} FROM articles ORDER BY id"
            ).fetchall()
        )
    if "sources" in tables:
        snapshot["sources_rows"] = tuple(
            tuple(row)
            for row in conn.execute(
                """SELECT name, fetch_ok, fetch_fail, collected, picked
                   FROM sources ORDER BY name"""
            ).fetchall()
        )
    return snapshot


def _assert_snapshot_unchanged(
    conn: sqlite3.Connection,
    before: dict[str, Any],
) -> None:
    if "articles_rows" in before:
        columns = before["articles_columns"]
        quoted = ", ".join(f'"{name}"' for name in columns)
        after = tuple(
            tuple(row)
            for row in conn.execute(
                f"SELECT {quoted} FROM articles ORDER BY id"
            ).fetchall()
        )
        if after != before["articles_rows"]:
            raise SchemaError("migrācija mainīja esošās articles vērtības")
    if "sources_rows" in before:
        after = tuple(
            tuple(row)
            for row in conn.execute(
                """SELECT name, fetch_ok, fetch_fail, collected, picked
                   FROM sources ORDER BY name"""
            ).fetchall()
        )
        if after != before["sources_rows"]:
            raise SchemaError(
                "migrācija mainīja sources skaitītājus vai rindas"
            )


def migration_plan(conn: sqlite3.Connection) -> dict[str, Any]:
    version = user_version(conn)
    if version > CURRENT_SCHEMA_VERSION:
        raise UnexpectedSchemaError(
            f"DB schema version {version} ir jaunāks par atbalstīto "
            f"{CURRENT_SCHEMA_VERSION}"
        )
    tables = _user_tables(conn)
    adopted: int | None = None
    start = version
    if version == 0 and tables:
        adopted = infer_legacy_version(conn)
        start = adopted
    elif version > 0:
        assert_schema(conn, version)
    elif not tables:
        start = 0

    steps: list[str] = []
    if adopted is not None:
        steps.append(f"adopt-legacy-v{adopted}")
    cursor = start
    while cursor < CURRENT_SCHEMA_VERSION:
        migration = MIGRATIONS.get(cursor)
        if migration is None:
            raise SchemaError(f"nav migrācijas no v{cursor}")
        steps.append(
            f"v{migration.from_version}->v{migration.to_version}:"
            f"{migration.name}"
        )
        cursor = migration.to_version
    return {
        "user_version": version,
        "inferred_legacy_version": adopted,
        "target_version": CURRENT_SCHEMA_VERSION,
        "steps": steps,
        "needs_change": bool(steps),
        "empty_database": not tables,
    }


def migrate_to_current(
    conn: sqlite3.Connection,
    *,
    logger: Callable[[str], None] | None = None,
    before_migration: Callable[[sqlite3.Connection], None] | None = None,
) -> tuple[str, ...]:
    """Apply the complete supported migration chain atomically."""
    if conn.in_transaction:
        raise SchemaError("migrāciju nevar sākt jau aktīvā transakcijā")
    emit = logger or (lambda _message: None)
    applied: list[str] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        before = _snapshot_existing_data(conn)
        if before_migration is not None:
            before_migration(conn)
        version = user_version(conn)
        if version > CURRENT_SCHEMA_VERSION:
            raise UnexpectedSchemaError(
                f"DB schema version {version} ir jaunāks par atbalstīto "
                f"{CURRENT_SCHEMA_VERSION}"
            )
        tables = _user_tables(conn)
        if version == 0 and tables:
            inferred = infer_legacy_version(conn)
            _set_user_version(conn, inferred)
            version = inferred
            marker = f"adopt-legacy-v{inferred}"
            applied.append(marker)
            emit(marker)
        elif version > 0:
            assert_schema(conn, version)

        while version < CURRENT_SCHEMA_VERSION:
            migration = MIGRATIONS.get(version)
            if migration is None:
                raise SchemaError(f"nav migrācijas no v{version}")
            emit(
                f"v{migration.from_version}->v{migration.to_version}:"
                f"{migration.name}"
            )
            for statement in migration.statements:
                conn.execute(statement)
            _set_user_version(conn, migration.to_version)
            assert_schema(conn, migration.to_version)
            marker = (
                f"v{migration.from_version}->v{migration.to_version}:"
                f"{migration.name}"
            )
            applied.append(marker)
            version = migration.to_version

        assert_schema(conn, CURRENT_SCHEMA_VERSION)
        _assert_snapshot_unchanged(conn, before)
        quick_check(conn)
        conn.commit()
        return tuple(applied)
    except Exception:
        conn.rollback()
        raise


def ensure_current_schema(
    conn: sqlite3.Connection,
    *,
    allow_initialize: bool = False,
) -> None:
    """Validate current schema without upgrading an existing database."""
    version = user_version(conn)
    tables = _user_tables(conn)
    if version == CURRENT_SCHEMA_VERSION:
        assert_schema(conn, CURRENT_SCHEMA_VERSION)
        quick_check(conn)
        return
    if version == 0 and not tables and allow_initialize:
        migrate_to_current(conn)
        return
    plan = migration_plan(conn)
    raise SchemaUpgradeRequired(
        "SQLite shēmas jauninājums vajadzīgs; palaid read-only preflight un "
        f"atsevišķi apstiprinātu apply soli. Plāns: {plan['steps']}"
    )


def database_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_connection(conn: sqlite3.Connection) -> dict[str, Any]:
    plan = migration_plan(conn)
    tables = _user_tables(conn)
    report: dict[str, Any] = {
        **plan,
        "quick_check": list(quick_check(conn)),
        "journal_mode": str(
            conn.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower(),
        "tables": list(tables),
    }
    if "articles" in tables:
        report["articles"] = {
            "columns": list(_column_names(conn, "articles")),
            "indexes": _index_specs(conn, "articles"),
            "row_count": int(
                conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            ),
            "digest_count": int(
                conn.execute(
                    "SELECT COUNT(*) FROM articles "
                    "WHERE digest_date IS NOT NULL"
                ).fetchone()[0]
            ),
        }
    if "sources" in tables:
        row = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(fetch_ok),0),
                      COALESCE(SUM(fetch_fail),0), COALESCE(SUM(collected),0),
                      COALESCE(SUM(picked),0)
               FROM sources"""
        ).fetchone()
        report["sources"] = {
            "row_count": int(row[0]),
            "fetch_ok": int(row[1]),
            "fetch_fail": int(row[2]),
            "collected": int(row[3]),
            "picked": int(row[4]),
        }
    return report


def open_readonly(path: Path, *, timeout: float = 30.0) -> sqlite3.Connection:
    resolved = path.resolve(strict=True)
    conn = sqlite3.connect(
        resolved.as_uri() + "?mode=ro",
        uri=True,
        timeout=timeout,
    )
    conn.execute("PRAGMA query_only = ON")
    return conn


def preflight(path: Path) -> dict[str, Any]:
    before_hash = database_sha256(path)
    before_size = path.stat().st_size
    conn = open_readonly(path)
    try:
        report = inspect_connection(conn)
    finally:
        conn.close()
    after_hash = database_sha256(path)
    after_size = path.stat().st_size
    if (before_hash, before_size) != (after_hash, after_size):
        raise SchemaError("read-only preflight laikā DB fails mainījās")
    report.update(
        {
            "path": str(path.resolve()),
            "sha256": before_hash,
            "size_bytes": before_size,
            "read_only_unchanged": True,
        }
    )
    return report
