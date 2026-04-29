"""
Live FX rate fetching from exchangerate-api.com with daily SQLite cache.

Usage:
    from engine.fx import get_gbp_sgd_rate

    rate = get_gbp_sgd_rate()   # 1 GBP = <rate> SGD
"""
import sqlite3
from datetime import date

import requests

import config
from database.db import get_conn, save_fx_rate


def get_gbp_sgd_rate(db_path: str = config.DB_PATH) -> float:
    """
    Return today's GBP→SGD rate.
    Serves from the DB cache if today's rate already exists;
    otherwise fetches from exchangerate-api.com and caches it.
    """
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT gbp_to_sgd FROM fx_rates WHERE date = date('now') LIMIT 1"
        ).fetchone()
        if row:
            return float(row["gbp_to_sgd"])

        rate = _fetch_live_gbp_sgd()
        save_fx_rate(conn, rate)
        return rate


def get_rate_for_date(rate_date: str, db_path: str = config.DB_PATH) -> float | None:
    """Return the cached GBP→SGD rate for a specific date, or None if not available."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT gbp_to_sgd FROM fx_rates WHERE date = ? LIMIT 1",
            (rate_date,),
        ).fetchone()
        return float(row["gbp_to_sgd"]) if row else None


def _fetch_live_gbp_sgd() -> float:
    """Hit exchangerate-api.com and return the GBP/SGD conversion rate."""
    if not config.EXCHANGERATE_API_KEY:
        raise EnvironmentError(
            "EXCHANGERATE_API_KEY is not set. "
            "Add it to .env or pass --fx-rate manually."
        )

    url = (
        f"https://v6.exchangerate-api.com/v6/{config.EXCHANGERATE_API_KEY}"
        "/pair/GBP/SGD"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("result") != "success":
        raise ValueError(f"FX API error: {data.get('error-type', 'unknown error')}")

    return float(data["conversion_rate"])
