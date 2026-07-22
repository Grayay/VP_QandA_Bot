from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import secrets
import sqlite3
from typing import Any

from faq_loader import FAQData, FAQItem, section_title_for_value


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
                    status TEXT NOT NULL DEFAULT 'pending_assignment',
                    routed_to_booker_id INTEGER,
                    routed_at TEXT,
                    after_cutoff INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT,
                    answered_at TEXT,
                    answer_text TEXT,
                    last_error TEXT
                )
                """
            )
            self._migrate_unanswered_questions(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_unanswered_questions_pending_assignment
                ON unanswered_questions(status, section_key, created_at, id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_unanswered_questions_routed
                ON unanswered_questions(routed_to_booker_id, status, id)
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ordinary_bookers (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT,
                    display_name TEXT NOT NULL,
                    added_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS faq_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    callback_token TEXT UNIQUE NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    department TEXT,
                    section TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by INTEGER,
                    updated_by INTEGER
                )
                """
            )
            self._migrate_faq_entries(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_faq_entries_active_section
                ON faq_entries(is_active, section, id)
                """
            )

    def import_bookers_from_config(
        self,
        booker_ids: set[int],
        *,
        chief_booker_ids: set[int],
    ) -> int:
        if not booker_ids:
            return 0

        with self._connect() as connection:
            if self._get_metadata(connection, "bookers_imported_from_config") == "1":
                return 0

            imported_count = 0
            now = self._now()
            for telegram_id in sorted(booker_ids - chief_booker_ids):
                if telegram_id <= 0:
                    continue

                known_profile = self._find_known_user_profile(connection, telegram_id)
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO ordinary_bookers (
                        telegram_id, username, display_name, added_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        telegram_id,
                        known_profile.get("username"),
                        known_profile.get("display_name") or f"Букер {telegram_id}",
                        now,
                    ),
                )
                imported_count += int(cursor.rowcount > 0)

            self._set_metadata(connection, "bookers_imported_from_config", "1")
            return imported_count

    def list_bookers(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT telegram_id, username, display_name, added_at
                FROM ordinary_bookers
                ORDER BY display_name COLLATE NOCASE ASC, telegram_id ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_booker(self, telegram_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT telegram_id, username, display_name, added_at
                FROM ordinary_bookers
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
            return dict(row) if row else None

    def is_booker(self, telegram_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM ordinary_bookers
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
            return bool(row)

    def add_booker(
        self,
        *,
        telegram_id: int,
        username: str | None,
        display_name: str,
        added_at: str | None = None,
    ) -> bool:
        if telegram_id <= 0:
            raise ValueError("telegram_id must be a positive integer.")

        cleaned_name = self._clean_required_text(display_name)
        cleaned_username = self._clean_optional_text(username)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO ordinary_bookers (
                    telegram_id, username, display_name, added_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (telegram_id, cleaned_username, cleaned_name, added_at or self._now()),
            )
            return bool(cursor.rowcount)

    def remove_booker(self, telegram_id: int) -> bool:
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT 1
                FROM ordinary_bookers
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
            if not existing:
                return False

            connection.execute(
                """
                DELETE FROM ordinary_bookers
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            )
            self._clear_duty_booker_for_user(connection, telegram_id)
            self._return_booker_questions_to_queue(connection, telegram_id)
            return True

    def clear_duty_booker_for_user(self, user_id: int) -> int:
        with self._connect() as connection:
            return self._clear_duty_booker_for_user(connection, user_id)

    def find_known_booker_username(self, telegram_id: int) -> str | None:
        with self._connect() as connection:
            profile = self._find_known_user_profile(connection, telegram_id)
            username = profile.get("username")
            return str(username) if username else None

    def get_active_faq_data(self) -> FAQData:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, callback_token, question, answer, department, section
                FROM faq_entries
                WHERE is_active = 1
                ORDER BY id ASC
                """,
            ).fetchall()

        items: list[FAQItem] = []
        sections: list[str] = []
        by_section: dict[str, list[FAQItem]] = {}

        for row in rows:
            item = self._faq_item_from_row(row)
            items.append(item)
            section_title = section_title_for_value(item.section)
            if section_title not in by_section:
                by_section[section_title] = []
                sections.append(section_title)
            by_section[section_title].append(item)

        return FAQData(
            items=items,
            sections=sections,
            by_section=by_section,
            by_id={item.id: item for item in items},
        )

    def get_active_faq_sections(self) -> list[str]:
        return self.get_active_faq_data().sections

    def get_active_faq_entries_by_section(self, section_title: str) -> list[FAQItem]:
        return self.get_active_faq_data().by_section.get(section_title, [])

    def get_faq_entry_by_token(
        self,
        token: str,
        *,
        active_only: bool = True,
    ) -> FAQItem | None:
        query = """
            SELECT id, callback_token, question, answer, department, section
            FROM faq_entries
            WHERE callback_token = ?
        """
        params: list[Any] = [token]
        if active_only:
            query += " AND is_active = 1"

        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
            return self._faq_item_from_row(row) if row else None

    def create_faq_entry(
        self,
        *,
        question: str,
        answer: str,
        department: str | None = None,
        section: str | None = None,
        created_by: int | None = None,
        updated_by: int | None = None,
        is_active: bool = True,
    ) -> FAQItem:
        now = self._now()
        with self._connect() as connection:
            token = self._new_faq_token(connection)
            cursor = connection.execute(
                """
                INSERT INTO faq_entries (
                    callback_token,
                    question,
                    answer,
                    department,
                    section,
                    is_active,
                    created_at,
                    updated_at,
                    created_by,
                    updated_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token,
                    self._clean_required_text(question),
                    self._clean_required_text(answer),
                    self._clean_optional_text(department),
                    self._clean_optional_text(section),
                    int(is_active),
                    now,
                    now,
                    created_by,
                    updated_by if updated_by is not None else created_by,
                ),
            )
            row = connection.execute(
                """
                SELECT id, callback_token, question, answer, department, section
                FROM faq_entries
                WHERE id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
            return self._faq_item_from_row(row)

    def update_faq_entry(
        self,
        entry_id: int,
        *,
        question: str | None = None,
        answer: str | None = None,
        department: str | None = None,
        section: str | None = None,
        updated_by: int | None = None,
    ) -> None:
        updates: list[str] = []
        params: list[Any] = []

        if question is not None:
            updates.append("question = ?")
            params.append(self._clean_required_text(question))
        if answer is not None:
            updates.append("answer = ?")
            params.append(self._clean_required_text(answer))
        if department is not None:
            updates.append("department = ?")
            params.append(self._clean_optional_text(department))
        if section is not None:
            updates.append("section = ?")
            params.append(self._clean_optional_text(section))

        if not updates:
            return

        updates.extend(["updated_at = ?", "updated_by = ?"])
        params.extend([self._now(), updated_by, entry_id])

        with self._connect() as connection:
            connection.execute(
                f"""
                UPDATE faq_entries
                SET {", ".join(updates)}
                WHERE id = ?
                """,
                params,
            )

    def soft_delete_faq_entry(
        self,
        entry_id: int,
        *,
        updated_by: int | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE faq_entries
                SET is_active = 0, updated_at = ?, updated_by = ?
                WHERE id = ?
                """,
                (self._now(), updated_by, entry_id),
            )

    def upsert_faq_entry_from_import(
        self,
        *,
        question: str,
        answer: str,
        department: str | None = None,
        section: str | None = None,
    ) -> str:
        cleaned_question = self._clean_required_text(question)
        cleaned_answer = self._clean_required_text(answer)
        cleaned_department = self._clean_optional_text(department)
        cleaned_section = self._clean_optional_text(section)

        with self._connect() as connection:
            existing = self._find_faq_entry_by_import_key(
                connection,
                question=cleaned_question,
                section=cleaned_section,
            )
            if not existing:
                token = self._new_faq_token(connection)
                now = self._now()
                connection.execute(
                    """
                    INSERT INTO faq_entries (
                        callback_token,
                        question,
                        answer,
                        department,
                        section,
                        is_active,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        token,
                        cleaned_question,
                        cleaned_answer,
                        cleaned_department,
                        cleaned_section,
                        now,
                        now,
                    ),
                )
                return "added"

            changed = (
                str(existing["question"]) != cleaned_question
                or str(existing["answer"]) != cleaned_answer
                or self._row_optional_value(existing["department"]) != cleaned_department
                or self._row_optional_value(existing["section"]) != cleaned_section
            )
            if not changed:
                return "skipped"

            connection.execute(
                """
                UPDATE faq_entries
                SET question = ?, answer = ?, department = ?, section = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    cleaned_question,
                    cleaned_answer,
                    cleaned_department,
                    cleaned_section,
                    self._now(),
                    existing["id"],
                ),
            )
            return "updated"

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
        status: str = "pending_assignment",
        routed_to_booker_id: int | None = None,
        routed_at: str | None = None,
        after_cutoff: bool = False,
    ) -> int:
        created_value = created_at or self._now()
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
                    after_cutoff,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    full_name,
                    section_key,
                    section,
                    question,
                    question,
                    created_value,
                    status,
                    routed_to_booker_id,
                    routed_at,
                    int(after_cutoff),
                    created_value,
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
            WHERE status IN ('pending', 'pending_assignment') AND after_cutoff = 1
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

    def get_pending_assignment_questions(
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
                updated_at,
                status,
                routed_to_booker_id,
                routed_at,
                after_cutoff,
                answered_at,
                answer_text,
                last_error
            FROM unanswered_questions
            WHERE status IN ('pending', 'pending_assignment')
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

    def get_question(self, question_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    user_id,
                    username,
                    full_name,
                    section_key,
                    section,
                    COALESCE(question_text, question) AS question_text,
                    created_at,
                    updated_at,
                    status,
                    routed_to_booker_id,
                    routed_at,
                    after_cutoff,
                    answered_at,
                    answer_text,
                    last_error
                FROM unanswered_questions
                WHERE id = ?
                """,
                (question_id,),
            ).fetchone()
            return dict(row) if row else None

    def mark_question_forwarded(
        self,
        *,
        question_id: int,
        routed_to_booker_id: int,
        routed_at: str | None = None,
    ) -> None:
        self.mark_question_sent_to_booker(
            question_id=question_id,
            booker_id=routed_to_booker_id,
            sent_at=routed_at,
        )

    def mark_question_sent_to_booker(
        self,
        *,
        question_id: int,
        booker_id: int,
        sent_at: str | None = None,
    ) -> None:
        sent_value = sent_at or self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE unanswered_questions
                SET
                    status = 'sent_to_booker',
                    routed_to_booker_id = ?,
                    routed_at = ?,
                    updated_at = ?,
                    last_error = NULL
                WHERE id = ?
                """,
                (booker_id, sent_value, sent_value, question_id),
            )

    def mark_question_answered(
        self,
        *,
        question_id: int,
        answer_text: str,
        answered_at: str | None = None,
    ) -> None:
        answered_value = answered_at or self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE unanswered_questions
                SET
                    status = 'answered',
                    answer_text = ?,
                    answered_at = ?,
                    updated_at = ?,
                    last_error = NULL
                WHERE id = ?
                """,
                (answer_text, answered_value, answered_value, question_id),
            )

    def mark_question_failed(
        self,
        *,
        question_id: int,
        error_details: str,
        failed_at: str | None = None,
    ) -> None:
        failed_value = failed_at or self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE unanswered_questions
                SET
                    status = 'failed',
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error_details, failed_value, question_id),
            )

    def remember_question_error(
        self,
        *,
        question_id: int,
        error_details: str,
        updated_at: str | None = None,
    ) -> None:
        updated_value = updated_at or self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE unanswered_questions
                SET
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error_details, updated_value, question_id),
            )

    def _clear_duty_booker_for_user(
        self,
        connection: sqlite3.Connection,
        user_id: int,
    ) -> int:
        cursor = connection.execute(
            """
            DELETE FROM duty_bookers
            WHERE user_id = ?
            """,
            (user_id,),
        )
        return int(cursor.rowcount)

    def _return_booker_questions_to_queue(
        self,
        connection: sqlite3.Connection,
        user_id: int,
    ) -> int:
        cursor = connection.execute(
            """
            UPDATE unanswered_questions
            SET
                status = 'pending_assignment',
                routed_to_booker_id = NULL,
                routed_at = NULL,
                updated_at = ?,
                last_error = NULL
            WHERE routed_to_booker_id = ?
              AND status IN ('sent_to_booker', 'forwarded')
            """,
            (self._now(), user_id),
        )
        return int(cursor.rowcount)

    def _find_known_user_profile(
        self,
        connection: sqlite3.Connection,
        telegram_id: int,
    ) -> dict[str, str | None]:
        booker_row = connection.execute(
            """
            SELECT username, display_name
            FROM ordinary_bookers
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()
        if booker_row:
            return {
                "username": self._row_optional_value(booker_row["username"]),
                "display_name": self._row_optional_value(booker_row["display_name"]),
            }

        duty_row = connection.execute(
            """
            SELECT username, full_name
            FROM duty_bookers
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (telegram_id,),
        ).fetchone()
        if duty_row:
            return {
                "username": self._row_optional_value(duty_row["username"]),
                "display_name": self._row_optional_value(duty_row["full_name"]),
            }

        question_row = connection.execute(
            """
            SELECT username, full_name
            FROM unanswered_questions
            WHERE user_id = ?
            ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
            LIMIT 1
            """,
            (telegram_id,),
        ).fetchone()
        if question_row:
            return {
                "username": self._row_optional_value(question_row["username"]),
                "display_name": self._row_optional_value(question_row["full_name"]),
            }

        return {"username": None, "display_name": None}

    def _get_metadata(
        self,
        connection: sqlite3.Connection,
        key: str,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT value
            FROM app_metadata
            WHERE key = ?
            """,
            (key,),
        ).fetchone()
        return str(row["value"]) if row else None

    def _set_metadata(
        self,
        connection: sqlite3.Connection,
        key: str,
        value: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO app_metadata (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
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
            "updated_at": "ALTER TABLE unanswered_questions ADD COLUMN updated_at TEXT",
            "answered_at": "ALTER TABLE unanswered_questions ADD COLUMN answered_at TEXT",
            "answer_text": "ALTER TABLE unanswered_questions ADD COLUMN answer_text TEXT",
            "last_error": "ALTER TABLE unanswered_questions ADD COLUMN last_error TEXT",
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
        connection.execute(
            """
            UPDATE unanswered_questions
            SET updated_at = COALESCE(updated_at, routed_at, created_at)
            WHERE updated_at IS NULL
            """
        )
        connection.execute(
            """
            UPDATE unanswered_questions
            SET status = 'pending_assignment'
            WHERE status = 'pending'
            """
        )
        connection.execute(
            """
            UPDATE unanswered_questions
            SET status = 'sent_to_booker'
            WHERE status = 'forwarded'
            """
        )

    def _migrate_faq_entries(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(faq_entries)").fetchall()
        }

        migrations = {
            "callback_token": "ALTER TABLE faq_entries ADD COLUMN callback_token TEXT",
            "department": "ALTER TABLE faq_entries ADD COLUMN department TEXT",
            "section": "ALTER TABLE faq_entries ADD COLUMN section TEXT",
            "is_active": (
                "ALTER TABLE faq_entries ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            ),
            "created_at": "ALTER TABLE faq_entries ADD COLUMN created_at TEXT",
            "updated_at": "ALTER TABLE faq_entries ADD COLUMN updated_at TEXT",
            "created_by": "ALTER TABLE faq_entries ADD COLUMN created_by INTEGER",
            "updated_by": "ALTER TABLE faq_entries ADD COLUMN updated_by INTEGER",
        }

        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(statement)

        now = self._now()
        connection.execute(
            """
            UPDATE faq_entries
            SET created_at = COALESCE(created_at, ?),
                updated_at = COALESCE(updated_at, ?)
            WHERE created_at IS NULL OR updated_at IS NULL
            """,
            (now, now),
        )

        rows_without_token = connection.execute(
            """
            SELECT id
            FROM faq_entries
            WHERE callback_token IS NULL OR callback_token = ''
            """
        ).fetchall()
        for row in rows_without_token:
            connection.execute(
                """
                UPDATE faq_entries
                SET callback_token = ?
                WHERE id = ?
                """,
                (self._new_faq_token(connection), row["id"]),
            )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_faq_entries_callback_token
            ON faq_entries(callback_token)
            """
        )

    def _find_faq_entry_by_import_key(
        self,
        connection: sqlite3.Connection,
        *,
        question: str,
        section: str | None,
    ) -> sqlite3.Row | None:
        rows = connection.execute(
            """
            SELECT id, question, answer, department, section, is_active
            FROM faq_entries
            """
        ).fetchall()
        question_key = self._normalize_faq_key(question)
        section_key = self._normalize_faq_key(section or "")

        for row in rows:
            if (
                self._normalize_faq_key(str(row["question"])) == question_key
                and self._normalize_faq_key(self._row_optional_value(row["section"]) or "")
                == section_key
            ):
                return row

        return None

    def _new_faq_token(self, connection: sqlite3.Connection) -> str:
        while True:
            token = secrets.token_urlsafe(9)
            exists = connection.execute(
                """
                SELECT 1
                FROM faq_entries
                WHERE callback_token = ?
                """,
                (token,),
            ).fetchone()
            if not exists:
                return token

    @staticmethod
    def _faq_item_from_row(row: sqlite3.Row) -> FAQItem:
        return FAQItem(
            id=int(row["id"]),
            token=str(row["callback_token"]),
            question=str(row["question"]),
            answer=str(row["answer"]),
            department=Database._row_optional_value(row["department"]),
            section=Database._row_optional_value(row["section"]),
        )

    @staticmethod
    def _clean_required_text(value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("FAQ question and answer must not be empty.")
        return cleaned

    @staticmethod
    def _clean_optional_text(value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None

    @staticmethod
    def _row_optional_value(value: Any) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @staticmethod
    def _normalize_faq_key(value: str) -> str:
        return " ".join(value.strip().lower().replace("ё", "е").split())

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")
