"""Локальное хранилище путешествий и расходов (SQLite)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from config import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                active_trip_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                from_country TEXT NOT NULL,
                to_country TEXT NOT NULL,
                home_currency TEXT NOT NULL,
                dest_currency TEXT NOT NULL,
                rate REAL NOT NULL,
                balance_home REAL NOT NULL,
                balance_dest REAL NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trip_id INTEGER NOT NULL,
                amount_dest REAL NOT NULL,
                amount_home REAL NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE
            );
            """
        )


def ensure_user(telegram_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id, active_trip_id) VALUES (?, NULL)",
            (telegram_id,),
        )


def create_trip(
    user_id: int,
    from_country: str,
    to_country: str,
    home_currency: str,
    dest_currency: str,
    rate: float,
    balance_home: float,
    balance_dest: float,
) -> int:
    ensure_user(user_id)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO trips (
                user_id, from_country, to_country,
                home_currency, dest_currency, rate,
                balance_home, balance_dest, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                from_country,
                to_country,
                home_currency.upper(),
                dest_currency.upper(),
                rate,
                balance_home,
                balance_dest,
                now,
            ),
        )
        trip_id = int(cur.lastrowid)
        conn.execute(
            "UPDATE users SET active_trip_id = ? WHERE telegram_id = ?",
            (trip_id, user_id),
        )
        return trip_id


def list_trips(user_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM trips
            WHERE user_id = ?
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()
        return list(rows)


def get_trip(trip_id: int, user_id: int | None = None) -> sqlite3.Row | None:
    with get_conn() as conn:
        if user_id is None:
            return conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        return conn.execute(
            "SELECT * FROM trips WHERE id = ? AND user_id = ?",
            (trip_id, user_id),
        ).fetchone()


def get_active_trip(user_id: int) -> sqlite3.Row | None:
    ensure_user(user_id)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT t.*
            FROM users u
            JOIN trips t ON t.id = u.active_trip_id
            WHERE u.telegram_id = ?
            """,
            (user_id,),
        ).fetchone()
        return row


def set_active_trip(user_id: int, trip_id: int) -> bool:
    trip = get_trip(trip_id, user_id)
    if trip is None:
        return False
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET active_trip_id = ? WHERE telegram_id = ?",
            (trip_id, user_id),
        )
    return True


def update_rate(trip_id: int, user_id: int, rate: float) -> bool:
    """rate = сколько home за 1 dest."""
    trip = get_trip(trip_id, user_id)
    if trip is None:
        return False
    balance_dest = float(trip["balance_dest"])
    balance_home = balance_dest * rate
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE trips
            SET rate = ?, balance_home = ?
            WHERE id = ? AND user_id = ?
            """,
            (rate, balance_home, trip_id, user_id),
        )
    return True


def add_expense(trip_id: int, user_id: int, amount_dest: float) -> dict[str, Any] | None:
    trip = get_trip(trip_id, user_id)
    if trip is None:
        return None

    rate = float(trip["rate"])
    amount_home = amount_dest * rate
    new_dest = float(trip["balance_dest"]) - amount_dest
    new_home = float(trip["balance_home"]) - amount_home
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO expenses (trip_id, amount_dest, amount_home, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (trip_id, amount_dest, amount_home, now),
        )
        conn.execute(
            """
            UPDATE trips
            SET balance_dest = ?, balance_home = ?
            WHERE id = ? AND user_id = ?
            """,
            (new_dest, new_home, trip_id, user_id),
        )

    return {
        "amount_dest": amount_dest,
        "amount_home": amount_home,
        "balance_dest": new_dest,
        "balance_home": new_home,
        "home_currency": trip["home_currency"],
        "dest_currency": trip["dest_currency"],
    }


def list_expenses(trip_id: int, user_id: int, limit: int = 20) -> list[sqlite3.Row]:
    trip = get_trip(trip_id, user_id)
    if trip is None:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM expenses
            WHERE trip_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (trip_id, limit),
        ).fetchall()
        return list(rows)
