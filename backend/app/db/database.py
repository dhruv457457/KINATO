"""
================================================================================
FILE: app/db/database.py
MODULE: Module 1 - SQLite Database Connection Manager
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Provides thread-safe SQLite connection management and transactional context
for the Kinato platform. Stores the database file locally in .data/kinato.db.
================================================================================
"""
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Generator

# Database file location: backend/.data/kinato.db
DATA_DIR = Path(__file__).parent.parent.parent / ".data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "kinato.db"


def get_db_connection() -> sqlite3.Connection:
    """Returns a configured SQLite connection with foreign keys and row dict factory."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # Write-Ahead Logging for high concurrency
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager ensuring transactions commit on success and rollback on error."""
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
