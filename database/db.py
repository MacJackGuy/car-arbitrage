"""
SQLite connection management and schema initialisation.

Usage:
    from database.db import get_conn, init_db

    init_db()                    # first run: creates all tables
    with get_conn() as conn:
        conn.execute(...)
"""
import json
import os
import sqlite3
from pathlib import Path

import config


def get_conn(db_path: str = config.DB_PATH) -> sqlite3.Connection:
    """
    Return a SQLite connection with row_factory set for dict-like row access.
    WAL mode and foreign-key enforcement are enabled on every connection.
    """
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str = config.DB_PATH) -> None:
    """Apply schema.sql to create all tables (idempotent — uses IF NOT EXISTS)."""
    schema_path = Path(__file__).parent / "schema.sql"
    schema = schema_path.read_text()
    with get_conn(db_path) as conn:
        conn.executescript(schema)
        _apply_migrations(conn)
    print(f"[db] Initialised at {db_path}")


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """
    Apply additive schema migrations for databases created before the current schema.
    Each ALTER TABLE is wrapped in try/except so it is safe to run repeatedly.
    """
    # Phase 3 additions
    _alter(conn, "ALTER TABLE listings ADD COLUMN mileage_miles INTEGER")
    _alter(conn, "ALTER TABLE listings ADD COLUMN price_low_gbp REAL")
    _alter(conn, "ALTER TABLE listings ADD COLUMN price_high_gbp REAL")
    _alter(conn, "ALTER TABLE listings ADD COLUMN current_bid_gbp REAL")


# ── Listing helpers ───────────────────────────────────────────────────────────

def upsert_listing(conn: sqlite3.Connection, listing: dict) -> int:
    """
    Insert a new listing or update last_seen_at on an existing one.
    Tracks price changes automatically in price_history.
    Returns the listing id.
    """
    existing = conn.execute(
        "SELECT id, price_sgd, price_gbp FROM listings WHERE source_url = ?",
        (listing["source_url"],),
    ).fetchone()

    if existing:
        listing_id = existing["id"]
        new_sgd = listing.get("price_sgd")
        new_gbp = listing.get("price_gbp")

        if existing["price_sgd"] != new_sgd or existing["price_gbp"] != new_gbp:
            conn.execute(
                "INSERT INTO price_history (listing_id, price_sgd, price_gbp) VALUES (?,?,?)",
                (listing_id, new_sgd, new_gbp),
            )

        conn.execute(
            """UPDATE listings SET
               price_sgd   = ?,
               price_gbp   = ?,
               last_seen_at = datetime('now'),
               is_active   = 1,
               updated_at  = datetime('now')
             WHERE id = ?""",
            (new_sgd, new_gbp, listing_id),
        )
        return listing_id

    # New listing
    image_urls = listing.get("image_urls", [])
    if isinstance(image_urls, list):
        listing = {**listing, "image_urls": json.dumps(image_urls)}

    cols = list(listing.keys())
    placeholders = ", ".join("?" * len(cols))
    col_names = ", ".join(cols)
    conn.execute(
        f"INSERT INTO listings ({col_names}) VALUES ({placeholders})",
        [listing[c] for c in cols],
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def save_profit_calculation(conn: sqlite3.Connection, calc: dict) -> int:
    """Insert a profit_calculations row. Returns the new id."""
    cols = list(calc.keys())
    placeholders = ", ".join("?" * len(cols))
    col_names = ", ".join(cols)
    conn.execute(
        f"INSERT INTO profit_calculations ({col_names}) VALUES ({placeholders})",
        [calc[c] for c in cols],
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _alter(conn: sqlite3.Connection, sql: str) -> None:
    """Run an ALTER TABLE statement, silently ignoring 'duplicate column' errors."""
    try:
        conn.execute(sql)
    except Exception:
        pass  # column already exists or other benign migration skip


def get_latest_fx(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Return today's FX rate row, or None if not yet fetched."""
    return conn.execute(
        "SELECT * FROM fx_rates WHERE date = date('now') LIMIT 1"
    ).fetchone()


def save_fx_rate(conn: sqlite3.Connection, gbp_to_sgd: float) -> None:
    """Upsert today's FX rate."""
    sgd_to_gbp = 1.0 / gbp_to_sgd
    conn.execute(
        """INSERT OR REPLACE INTO fx_rates (date, sgd_to_gbp, gbp_to_sgd)
           VALUES (date('now'), ?, ?)""",
        (sgd_to_gbp, gbp_to_sgd),
    )
