"""
================================================================================
FILE: app/db/database.py
MODULE: Module 1 - Unified Resilient Database Connection Manager
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Provides thread-safe, async-friendly connection pooling for PostgreSQL (via psycopg2)
with an automatic zero-config local SQLite fallback for offline demo and test runs.

KEY FEATURES:
  1. Thread-safe and async-safe (WAL mode, busy_timeout, check_same_thread=False).
  2. Cursor context manager support (`with conn.cursor() as cur:`) for both DBs.
  3. String-literal-aware PostgreSQL %s -> SQLite ? parameter marker translation
     (skips %s that appears inside a quoted string literal, e.g. a LIKE pattern).
  4. Automatic SQL dialect normalization (ILIKE -> LIKE, SERIAL -> AUTOINCREMENT,
     TIMESTAMPTZ/BOOLEAN/BIGINT -> SQLite-compatible types, NOW() -> CURRENT_TIMESTAMP).
  5. Async helper `run_db_async` to offload blocking DB queries to worker threads.
  6. Row objects return dict-like rows across both engines.
  7. `dialect()` reports which engine is active - schema/DDL code should write
     Postgres-dialect SQL once and rely on this module to translate for SQLite,
     rather than branching on `type(conn)` inline (that pattern is why the old
     schema became two full copies of every table definition).
================================================================================
"""
import os
import re
import sqlite3
import logging
import asyncio
from contextlib import contextmanager
from typing import Generator, Any, Callable, TypeVar
from pathlib import Path
from app.core.config import settings

# Graceful import of psycopg2
try:
    import psycopg2
    from psycopg2 import pool
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

logger = logging.getLogger("kinato.db")

db_pool = None
USE_SQLITE = False
SQLITE_PATH = Path(__file__).parent.parent / "data" / "kinato_local.db"

T = TypeVar("T")


def init_pool():
    global db_pool, USE_SQLITE
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if PSYCOPG2_AVAILABLE and settings.DATABASE_URL and not settings.DATABASE_URL.startswith("sqlite"):
        try:
            db_pool = psycopg2.pool.ThreadedConnectionPool(
                1, 20, dsn=settings.DATABASE_URL, connect_timeout=4
            )
            # Test connection
            conn = db_pool.getconn()
            db_pool.putconn(conn)
            USE_SQLITE = False
            logger.info("Connected to PostgreSQL database pool.")
            return
        except Exception as e:
            logger.warning(f"PostgreSQL connection failed ({e}). Falling back to local SQLite.")
            db_pool = None
            USE_SQLITE = True
    else:
        USE_SQLITE = True
        logger.info("Using local SQLite database.")


init_pool()


def dialect() -> str:
    """'sqlite' or 'postgres' - whichever engine get_db() is actually using.
    Schema/DDL code should write Postgres-dialect SQL once and call this only
    to decide whether a Postgres-only clause is safe to include, rather than
    inspecting connection types."""
    return "sqlite" if USE_SQLITE else "postgres"


def _split_sql_literals(sql: str):
    """Splits `sql` into (is_string_literal, chunk) pieces, so translation
    passes can skip the contents of single-quoted string literals (handling
    '' as an escaped quote). This is what keeps a literal '%s' inside a LIKE
    pattern or a text default from being corrupted into a phantom bind param."""
    pieces = []
    buf = []
    in_string = False
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            if in_string and i + 1 < n and sql[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            pieces.append((in_string, "".join(buf)))
            buf = [ch]
            in_string = not in_string
            i += 1
            continue
        buf.append(ch)
        i += 1
    pieces.append((in_string, "".join(buf)))
    return pieces


def _count_placeholders(sql: str) -> int:
    """Counts %s occurrences outside string literals - used to sanity-check
    that a translated query's placeholder count still matches len(params)."""
    count = 0
    for in_string, chunk in _split_sql_literals(sql):
        if not in_string:
            count += chunk.count("%s")
    return count


def _translate_sql_for_sqlite(sql: str) -> str:
    """Translates PostgreSQL-dialect DDL/DML into SQLite-compatible SQL."""
    # Convert %s placeholders to ? - skipping any %s inside a string literal.
    translated_parts = []
    for in_string, chunk in _split_sql_literals(sql):
        translated_parts.append(chunk if in_string else chunk.replace("%s", "?"))
    translated = "".join(translated_parts)

    translated = re.sub(r'\bILIKE\b', 'LIKE', translated, flags=re.IGNORECASE)
    translated = re.sub(r'\bSERIAL PRIMARY KEY\b', 'INTEGER PRIMARY KEY AUTOINCREMENT', translated, flags=re.IGNORECASE)
    translated = re.sub(r'\bJSONB\b', 'TEXT', translated, flags=re.IGNORECASE)
    translated = re.sub(r'\bTIMESTAMP WITH TIME ZONE\b', 'TEXT', translated, flags=re.IGNORECASE)
    translated = re.sub(r'\bTIMESTAMPTZ\b', 'TEXT', translated, flags=re.IGNORECASE)
    translated = re.sub(r'\bBOOLEAN\b', 'INTEGER', translated, flags=re.IGNORECASE)
    translated = re.sub(r'\bBIGINT\b', 'INTEGER', translated, flags=re.IGNORECASE)

    # PostgreSQL NOW() - INTERVAL 'X days' -> SQLite datetime('now', '-X days')
    translated = re.sub(
        r"NOW\(\)\s*-\s*INTERVAL\s*'\? days'",
        "datetime('now', '-' || ? || ' days')",
        translated,
        flags=re.IGNORECASE
    )
    # Any remaining bare NOW() -> CURRENT_TIMESTAMP
    translated = re.sub(r'\bNOW\(\)', 'CURRENT_TIMESTAMP', translated, flags=re.IGNORECASE)
    return translated


class SQLiteCursorWrapper:
    """Wraps sqlite3 cursor to support context managers, SQL translation, and dict outputs."""
    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._cursor.close()
        except Exception:
            pass

    def execute(self, sql: str, params: Any = None):
        translated_sql = _translate_sql_for_sqlite(sql)
        if params is not None:
            if isinstance(params, (list, tuple)):
                expected = _count_placeholders(sql)
                if expected != len(params):
                    raise ValueError(
                        f"Placeholder/param count mismatch: SQL has {expected} "
                        f"'%s' placeholders but {len(params)} params were given. "
                        f"SQL: {sql!r}"
                    )
                return self._cursor.execute(translated_sql, params)
            return self._cursor.execute(translated_sql, (params,))
        return self._cursor.execute(translated_sql)

    def executemany(self, sql: str, seq_of_params: Any):
        translated_sql = _translate_sql_for_sqlite(sql)
        return self._cursor.executemany(translated_sql, seq_of_params)

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [dict(r) for r in rows]

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return self._cursor.lastrowid


class SQLiteConnectionWrapper:
    """Wraps sqlite3 connection to return context-managed wrapped cursors."""
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def cursor(self):
        return SQLiteCursorWrapper(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


@contextmanager
def get_db() -> Generator[Any, None, None]:
    """
    Context manager ensuring transactions commit on success and rollback on error.
    Thread-safe and supports cursor context management across both PostgreSQL & SQLite.
    """
    global db_pool, USE_SQLITE

    if not USE_SQLITE and db_pool is not None:
        conn = db_pool.getconn()
        try:
            conn.cursor_factory = RealDictCursor
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            db_pool.putconn(conn)
    else:
        # SQLite with WAL mode, busy timeout, and check_same_thread=False for async safety
        conn = sqlite3.connect(
            str(SQLITE_PATH),
            timeout=30.0,
            check_same_thread=False
        )
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        wrapped_conn = SQLiteConnectionWrapper(conn)
        try:
            yield wrapped_conn
            wrapped_conn.commit()
        except Exception:
            wrapped_conn.rollback()
            raise
        finally:
            wrapped_conn.close()


async def run_db_async(fn: Callable[..., T], *args, **kwargs) -> T:
    """
    Runs a synchronous database operation in an asynchronous threadpool worker.
    Ensures that blocking DB operations never stall the FastAPI async event loop.
    """
    return await asyncio.to_thread(fn, *args, **kwargs)
