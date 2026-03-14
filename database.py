import sqlite3
from datetime import datetime
import os
import logging

logger = logging.getLogger(__name__)


def _default_db_path():
    # Railway / Render / Fly: if a persistent volume is mounted at /data, use it
    if os.path.isdir("/data"):
        return "/data/bot_data.db"
    return "bot_data.db"


DB_PATH = os.getenv("DB_PATH", _default_db_path())


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def init_db():
    logger.info(f"Database path: {DB_PATH}")
    with get_conn() as conn:
        # ── Create tables for fresh installs ──────────────────────────────────
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS projects (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                name       TEXT    NOT NULL,
                created_at TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL,
                project_id        INTEGER NOT NULL,
                source_note_id    INTEGER,
                title             TEXT    NOT NULL,
                description       TEXT,
                tags              TEXT    DEFAULT '',
                status            TEXT    DEFAULT 'pending',
                reminder_at       TEXT,
                reminded          INTEGER DEFAULT 0,
                deadline          TEXT,
                deadline_reminded INTEGER DEFAULT 0,
                created_at        TEXT    DEFAULT (datetime('now')),
                completed_at      TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
        ''')

        # ── Migrate notes table (old schema had single 'content' column) ───────
        if _table_exists(conn, 'notes') and _column_exists(conn, 'notes', 'content') \
                and not _column_exists(conn, 'notes', 'raw_text'):
            # Rebuild with new schema, preserving existing data
            conn.executescript('''
                CREATE TABLE notes_new (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    project_id   INTEGER NOT NULL,
                    raw_text     TEXT    NOT NULL DEFAULT '',
                    refined_text TEXT    NOT NULL DEFAULT '',
                    tags         TEXT    DEFAULT '',
                    created_at   TEXT    DEFAULT (datetime('now')),
                    FOREIGN KEY (project_id) REFERENCES projects(id)
                );
                INSERT INTO notes_new (id, user_id, project_id, raw_text, refined_text, tags, created_at)
                SELECT id, user_id, project_id, content, content, '', created_at FROM notes;
                DROP TABLE notes;
                ALTER TABLE notes_new RENAME TO notes;
            ''')
        elif not _table_exists(conn, 'notes'):
            conn.executescript('''
                CREATE TABLE notes (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    project_id   INTEGER NOT NULL,
                    raw_text     TEXT    NOT NULL,
                    refined_text TEXT    NOT NULL,
                    tags         TEXT    DEFAULT '',
                    created_at   TEXT    DEFAULT (datetime('now')),
                    FOREIGN KEY (project_id) REFERENCES projects(id)
                );
            ''')

        # ── Migrate tasks table (add columns missing from old schema) ──────────
        for col, definition in [
            ("source_note_id",    "INTEGER"),
            ("tags",              "TEXT DEFAULT ''"),
            ("completed_at",      "TEXT"),
            ("deadline",          "TEXT"),
            ("deadline_reminded", "INTEGER DEFAULT 0"),
        ]:
            if not _column_exists(conn, 'tasks', col):
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {definition}")


# ── Projects ──────────────────────────────────────────────────────────────────

def get_projects(user_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM projects WHERE user_id = ? ORDER BY name", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_project(project_id: int, user_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def create_project(user_id: int, name: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (user_id, name) VALUES (?, ?)", (user_id, name)
        )
        return cur.lastrowid


# ── Notes ─────────────────────────────────────────────────────────────────────

def add_note(user_id: int, project_id: int, raw_text: str,
             refined_text: str, tags: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notes (user_id, project_id, raw_text, refined_text, tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, project_id, raw_text, refined_text, tags)
        )
        return cur.lastrowid


def get_notes(user_id: int, project_id: int, limit: int = 10):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM notes
            WHERE user_id = ? AND project_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, project_id, limit)).fetchall()
        return [dict(r) for r in rows]


def get_note(note_id: int, user_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)
        ).fetchone()
        return dict(row) if row else None


# ── Tasks ─────────────────────────────────────────────────────────────────────

def add_task(user_id: int, project_id: int, title: str,
             description: str = None, tags: str = "",
             source_note_id: int = None, deadline: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO tasks (user_id, project_id, title, description, tags, source_note_id, deadline)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, project_id, title, description, tags, source_note_id, deadline))
        return cur.lastrowid


def get_tasks(user_id: int, project_id: int, status: str = "pending"):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM tasks
            WHERE user_id = ? AND project_id = ? AND status = ?
            ORDER BY created_at DESC
        """, (user_id, project_id, status)).fetchall()
        return [dict(r) for r in rows]


def complete_task(task_id: int, user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'completed', completed_at = datetime('now') "
            "WHERE id = ? AND user_id = ?",
            (task_id, user_id)
        )


def update_task_content(task_id: int, user_id: int, title: str,
                        description: str = None, tags: str = ""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET title = ?, description = ?, tags = ? "
            "WHERE id = ? AND user_id = ?",
            (title, description, tags, task_id, user_id)
        )


def update_task_deadline(task_id: int, user_id: int, deadline: str | None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET deadline = ?, deadline_reminded = 0 "
            "WHERE id = ? AND user_id = ?",
            (deadline, task_id, user_id)
        )


def set_task_reminder(task_id: int, user_id: int, reminder_at: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET reminder_at = ?, reminded = 0 WHERE id = ? AND user_id = ?",
            (reminder_at, task_id, user_id)
        )


def get_due_reminders():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.*, p.name AS project_name
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE t.reminder_at <= datetime('now')
              AND t.reminded = 0
              AND t.status = 'pending'
        """).fetchall()
        return [dict(r) for r in rows]


def mark_reminded(task_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET reminded = 1 WHERE id = ?", (task_id,))


def get_approaching_deadlines():
    """Return pending tasks whose deadline is tomorrow and haven't been deadline-reminded."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.*, p.name AS project_name
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE t.deadline = date('now', '+1 day')
              AND t.status = 'pending'
              AND t.deadline_reminded = 0
        """).fetchall()
        return [dict(r) for r in rows]


def mark_deadline_reminded(task_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET deadline_reminded = 1 WHERE id = ?", (task_id,))
