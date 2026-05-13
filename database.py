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
                    question_text TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    routed_to_booker_id INTEGER,
                    routed_at TEXT,
                    after_cutoff INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._migrate_unanswered_questions(connection)
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
        created_at: str | None = None,
        status: str = "pending",
        routed_to_booker_id: int | None = None,
        routed_at: str | None = None,
        after_cutoff: bool = False,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO unanswered_questions (
                    user_id,
                    username,
                    full_name,
                    section_key,
                    section,
                    question,
                    question_text,
                    created_at,
                    status,
                    routed_to_booker_id,
                    routed_at,
                    after_cutoff
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    full_name,
                    section_key,
                    section,
                    question,
                    question,
                    created_at or self._now(),
                    status,
                    routed_to_booker_id,
                    routed_at,
                    int(after_cutoff),
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
        updated_at: str | None = None,
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
                    updated_at or self._now(),
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

    def get_pending_after_cutoff_questions(
        self,
        *,
        section_keys: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT
                id,
                user_id,
                username,
                full_name,
                section_key,
                section,
                COALESCE(question_text, question) AS question_text,
                created_at,
                status,
                routed_to_booker_id,
                routed_at,
                after_cutoff
            FROM unanswered_questions
            WHERE status = 'pending' AND after_cutoff = 1
        """
        params: tuple[Any, ...] = ()

        if section_keys:
            placeholders = ", ".join("?" for _ in section_keys)
            query += f" AND section_key IN ({placeholders})"
            params = tuple(sorted(section_keys))

        query += " ORDER BY created_at ASC, id ASC"

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def mark_question_forwarded(
        self,
        *,
        question_id: int,
        routed_to_booker_id: int,
        routed_at: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE unanswered_questions
                SET
                    status = 'forwarded',
                    routed_to_booker_id = ?,
                    routed_at = ?
                WHERE id = ?
                """,
                (routed_to_booker_id, routed_at or self._now(), question_id),
            )

    def _migrate_unanswered_questions(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(unanswered_questions)").fetchall()
        }

        migrations = {
            "question_text": "ALTER TABLE unanswered_questions ADD COLUMN question_text TEXT",
            "status": (
                "ALTER TABLE unanswered_questions "
                "ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
            ),
            "routed_to_booker_id": (
                "ALTER TABLE unanswered_questions ADD COLUMN routed_to_booker_id INTEGER"
            ),
            "routed_at": "ALTER TABLE unanswered_questions ADD COLUMN routed_at TEXT",
            "after_cutoff": (
                "ALTER TABLE unanswered_questions "
                "ADD COLUMN after_cutoff INTEGER NOT NULL DEFAULT 0"
            ),
        }

        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(statement)

        connection.execute(
            """
            UPDATE unanswered_questions
            SET question_text = question
            WHERE question_text IS NULL
            """
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")
