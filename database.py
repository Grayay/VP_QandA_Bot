from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Any


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS unanswered_questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    section_key TEXT NOT NULL,
                    section TEXT NOT NULL,
                    question TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS duty_bookers (
                    section_key TEXT PRIMARY KEY,
                    section TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def save_question(
        self,
        *,
        user_id: int,
        username: str | None,
        full_name: str,
        section_key: str,
        section: str,
        question: str,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO unanswered_questions (
                    user_id, username, full_name, section_key, section, question, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    full_name,
                    section_key,
                    section,
                    question,
                    self._now(),
                ),
            )
            return int(cursor.lastrowid)

    def set_duty_booker(
        self,
        *,
        section_key: str,
        section: str,
        user_id: int,
        username: str | None,
        full_name: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO duty_bookers (
                    section_key, section, user_id, username, full_name, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(section_key) DO UPDATE SET
                    section = excluded.section,
                    user_id = excluded.user_id,
                    username = excluded.username,
                    full_name = excluded.full_name,
                    updated_at = excluded.updated_at
                """,
                (
                    section_key,
                    section,
                    user_id,
                    username,
                    full_name,
                    self._now(),
                ),
            )

    def get_duty_booker(self, section_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT section_key, section, user_id, username, full_name, updated_at
                FROM duty_bookers
                WHERE section_key = ?
                """,
                (section_key,),
            ).fetchone()
            return dict(row) if row else None

    def get_all_duty_bookers(self) -> dict[str, dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT section_key, section, user_id, username, full_name, updated_at
                FROM duty_bookers
                """
            ).fetchall()
            return {str(row["section_key"]): dict(row) for row in rows}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")
