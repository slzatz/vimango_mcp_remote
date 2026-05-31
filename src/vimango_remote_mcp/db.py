"""Database operations for vimango PostgreSQL database."""

import psycopg2
from psycopg2 import sql
from pathlib import Path
from typing import Any, Optional, Tuple
import json

# Default UUIDs for "none" containers (matches vimango schema defaults)
DEFAULT_CONTEXT_UUID = "00000000-0000-0000-0000-000000000001"
DEFAULT_FOLDER_UUID = "00000000-0000-0000-0000-000000000002"
# Default tids for "none" containers
DEFAULT_CONTEXT_TID = 1
DEFAULT_FOLDER_TID = 1


class VimangoDatabase:
    """Handle operations on vimango PostgreSQL database."""

    def __init__(self, host: str, port: str, user: str, password: str, dbname: str,
                 ssl_mode: str = "disable", ssl_ca_cert: Optional[str] = None):
        """
        Initialize database connection parameters.

        Args:
            host: PostgreSQL host
            port: PostgreSQL port
            user: Database user
            password: Database password
            dbname: Database name
            ssl_mode: SSL mode (disable, require, verify-ca, verify-full)
            ssl_ca_cert: Path to CA certificate (for verify-ca/verify-full)
        """
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.dbname = dbname
        self.ssl_mode = ssl_mode
        self.ssl_ca_cert = ssl_ca_cert
        self.conn: Optional[psycopg2.extensions.connection] = None

    def connect(self):
        """Establish database connection."""
        conn_params = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "dbname": self.dbname,
            "sslmode": self.ssl_mode,
        }
        if self.ssl_ca_cert and self.ssl_mode in ("verify-ca", "verify-full"):
            conn_params["sslrootcert"] = self.ssl_ca_cert

        self.conn = psycopg2.connect(**conn_params)
        # Use autocommit for simple operations, explicit transactions for complex ones
        self.conn.autocommit = False

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def insert_note(
        self,
        title: str,
        note: str,
        context_tid: int = DEFAULT_CONTEXT_TID,
        context_uuid: str = DEFAULT_CONTEXT_UUID,
        folder_tid: int = DEFAULT_FOLDER_TID,
        folder_uuid: str = DEFAULT_FOLDER_UUID,
        star: bool = False
    ) -> int:
        """
        Insert a new note into the task table.

        Args:
            title: Note title
            note: Note body (markdown)
            context_tid: Context tid (default = "none" context)
            context_uuid: Context UUID (default = "none" context)
            folder_tid: Folder tid (default = "none" folder)
            folder_uuid: Folder UUID (default = "none" folder)
            star: Star/favorite flag

        Returns:
            The tid of the newly created task
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """INSERT INTO task (title, note, folder_tid, folder_uuid,
                   context_tid, context_uuid, star, added, modified)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                   RETURNING tid""",
                (title, note, folder_tid, folder_uuid, context_tid, context_uuid, star)
            )
            task_tid = cursor.fetchone()[0]
            self.conn.commit()
            cursor.close()
            return task_tid
        except psycopg2.DatabaseError:
            self.conn.rollback()
            raise

    def list_contexts(self) -> list[Tuple[int, str, str, bool]]:
        """
        List all available contexts.

        Returns:
            List of tuples: (tid, title, uuid, star)
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT tid, title, uuid, star FROM context WHERE deleted = false ORDER BY title"
        )
        results = cursor.fetchall()
        cursor.close()
        return results

    def list_folders(self) -> list[Tuple[int, str, str, bool]]:
        """
        List all available folders.

        Returns:
            List of tuples: (tid, title, uuid, star)
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT tid, title, uuid, star FROM folder WHERE deleted = false ORDER BY title"
        )
        results = cursor.fetchall()
        cursor.close()
        return results

    def get_context_by_name(self, name: str) -> Optional[Tuple[int, str]]:
        """
        Get context tid and uuid by name.

        Args:
            name: Context name

        Returns:
            Tuple of (tid, uuid) or None if not found
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT tid, uuid FROM context WHERE title = %s AND deleted = false",
            (name,)
        )
        result = cursor.fetchone()
        cursor.close()
        return result

    def get_folder_by_name(self, name: str) -> Optional[Tuple[int, str]]:
        """
        Get folder tid and uuid by name.

        Args:
            name: Folder name

        Returns:
            Tuple of (tid, uuid) or None if not found
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT tid, uuid FROM folder WHERE title = %s AND deleted = false",
            (name,)
        )
        result = cursor.fetchone()
        cursor.close()
        return result

    def find_notes(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """
        Search for notes using PostgreSQL full-text search.

        Args:
            query: Search string (minimum 3 characters)
            limit: Maximum number of rows to return (minimum 1)

        Returns:
            List of dictionaries containing rank, tid, title, context_title, folder_title
        """
        cleaned_query = query.strip()
        if len(cleaned_query) < 3:
            raise ValueError("Search query must be at least 3 characters long.")

        if limit <= 0:
            limit = 5

        # Convert search query to tsquery format
        # Split on whitespace and join with & for AND search
        terms = cleaned_query.split()
        tsquery_str = " & ".join(terms)

        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT
                    ts_rank(to_tsvector('english', COALESCE(task.title, '') || ' ' || COALESCE(task.note, '')),
                            plainto_tsquery('english', %s)) AS rank,
                    task.tid,
                    task.title,
                    COALESCE(context.title, 'none') AS context_title,
                    COALESCE(folder.title, 'none') AS folder_title
                FROM task
                LEFT JOIN context ON context.tid = task.context_tid
                LEFT JOIN folder ON folder.tid = task.folder_tid
                WHERE task.deleted = false
                  AND task.archived = false
                  AND (to_tsvector('english', COALESCE(task.title, '') || ' ' || COALESCE(task.note, ''))
                       @@ plainto_tsquery('english', %s))
                ORDER BY rank DESC
                LIMIT %s
                """,
                (cleaned_query, cleaned_query, limit)
            )
            rows = cursor.fetchall()
            cursor.close()
        except psycopg2.DatabaseError as exc:
            raise RuntimeError(f"Search failed: {exc}") from exc

        results: list[dict[str, Any]] = []
        for i, (rank, tid, title, context_title, folder_title) in enumerate(rows, start=1):
            results.append({
                "rank": i,
                "tid": tid,
                "title": title,
                "context_title": context_title,
                "folder_title": folder_title,
            })

        return results

    def recent_notes(self, limit: int = 5) -> list[dict[str, Any]]:
        """
        Return the most recently modified notes.

        Args:
            limit: Maximum number of notes to return (default 5)

        Returns:
            List of dictionaries containing tid, title, context_title,
            folder_title, and modified timestamp.
        """
        if limit <= 0:
            limit = 5

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                task.tid,
                task.title,
                COALESCE(context.title, 'none') AS context_title,
                COALESCE(folder.title, 'none') AS folder_title,
                task.modified
            FROM task
            LEFT JOIN context ON context.tid = task.context_tid
            LEFT JOIN folder ON folder.tid = task.folder_tid
            WHERE task.deleted = false
              AND task.archived = false
            ORDER BY task.modified DESC
            LIMIT %s
            """,
            (limit,)
        )
        rows = cursor.fetchall()
        cursor.close()

        results: list[dict[str, Any]] = []
        for tid, title, context_title, folder_title, modified in rows:
            results.append({
                "tid": tid,
                "title": title,
                "context_title": context_title,
                "folder_title": folder_title,
                "modified": modified,
            })

        return results

    def get_note_by_tid(self, tid: int) -> Optional[dict[str, Any]]:
        """
        Retrieve the full note content and metadata for a given task tid.

        Args:
            tid: Task tid

        Returns:
            Dictionary with tid, title, note, context_title, folder_title, or None if not found.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT task.tid, task.title, task.note,
                   COALESCE(context.title, 'none') AS context_title,
                   COALESCE(folder.title, 'none') AS folder_title
            FROM task
            LEFT JOIN context ON context.tid = task.context_tid
            LEFT JOIN folder ON folder.tid = task.folder_tid
            WHERE task.tid = %s AND task.deleted = false AND task.archived = false
            """,
            (tid,),
        )
        row = cursor.fetchone()
        cursor.close()

        if not row:
            return None

        task_tid, title, note, context_title, folder_title = row
        return {
            "tid": task_tid,
            "title": title,
            "note": note or "",
            "context_title": context_title,
            "folder_title": folder_title,
        }

    def update_note_metadata(
        self,
        tid: int,
        context_tid: Optional[int] = None,
        context_uuid: Optional[str] = None,
        folder_tid: Optional[int] = None,
        folder_uuid: Optional[str] = None,
        title: Optional[str] = None,
        star: Optional[bool] = None,
        note: Optional[str] = None
    ) -> bool:
        """
        Update fields on an existing note.

        Args:
            tid: Task tid of the note
            context_tid: New context tid (optional)
            context_uuid: New context UUID (optional)
            folder_tid: New folder tid (optional)
            folder_uuid: New folder UUID (optional)
            title: New title (optional)
            star: New star/favorite value (optional)
            note: New note body (optional)

        Returns:
            True if a row was updated, False otherwise
        """
        updates = []
        params = []

        # Context update requires both tid and uuid
        if context_tid is not None:
            updates.append("context_tid = %s")
            params.append(context_tid)
        if context_uuid is not None:
            updates.append("context_uuid = %s")
            params.append(context_uuid)
        # Folder update requires both tid and uuid
        if folder_tid is not None:
            updates.append("folder_tid = %s")
            params.append(folder_tid)
        if folder_uuid is not None:
            updates.append("folder_uuid = %s")
            params.append(folder_uuid)
        if title is not None:
            updates.append("title = %s")
            params.append(title)
        if star is not None:
            updates.append("star = %s")
            params.append(star)
        if note is not None:
            updates.append("note = %s")
            params.append(note)

        if not updates:
            return False

        updates.append("modified = NOW()")
        params.append(tid)

        sql_str = f"UPDATE task SET {', '.join(updates)} WHERE tid = %s AND deleted = false"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql_str, params)
            row_count = cursor.rowcount
            self.conn.commit()
            cursor.close()
            return row_count > 0
        except psycopg2.DatabaseError:
            self.conn.rollback()
            raise


def load_config(config_path: str = "config.json") -> dict:
    """
    Load configuration from JSON file.

    Args:
        config_path: Path to config.json

    Returns:
        Configuration dictionary
    """
    with open(config_path) as f:
        return json.load(f)
