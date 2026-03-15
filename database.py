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

        # ── Chat / multi-chat support tables ───────────────────────────────────
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS chats (
                id                  INTEGER PRIMARY KEY,
                chat_type           TEXT    NOT NULL,
                title               TEXT,
                created_by_user_id  INTEGER NOT NULL,
                setup_complete      INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS chat_projects (
                chat_id     INTEGER NOT NULL,
                project_id  INTEGER NOT NULL,
                UNIQUE(chat_id, project_id),
                FOREIGN KEY (chat_id)    REFERENCES chats(id),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );
        ''')

        # ── Migrate notes/tasks to add chat_id column ─────────────────────────
        if not _column_exists(conn, 'notes', 'chat_id'):
            conn.execute("ALTER TABLE notes ADD COLUMN chat_id INTEGER")
        if not _column_exists(conn, 'tasks', 'chat_id'):
            conn.execute("ALTER TABLE tasks ADD COLUMN chat_id INTEGER")

        # ── Whitelist table ────────────────────────────────────────────────────
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS whitelist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                type        TEXT    NOT NULL CHECK (type IN ('user', 'chat')),
                telegram_id INTEGER NOT NULL,
                added_by    INTEGER,
                added_at    TEXT    DEFAULT (datetime('now')),
                UNIQUE(type, telegram_id)
            );
        ''')

        # ── Content classification tables ──────────────────────────────────────
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS saved_references (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                project_id  INTEGER NOT NULL,
                url         TEXT    NOT NULL,
                title       TEXT    DEFAULT '',
                description TEXT    DEFAULT '',
                created_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS ideas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                project_id  INTEGER NOT NULL,
                content     TEXT    NOT NULL,
                created_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS journal (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                project_id  INTEGER NOT NULL,
                content     TEXT    NOT NULL,
                created_at  TEXT    DEFAULT (datetime('now'))
            );
        ''')


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


# ── Chats ─────────────────────────────────────────────────────────────────────

def register_chat(chat_id: int, chat_type: str, title: str, created_by_user_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO chats (id, chat_type, title, created_by_user_id) VALUES (?, ?, ?, ?)",
            (chat_id, chat_type, title, created_by_user_id)
        )


def get_chat(chat_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        return dict(row) if row else None


def mark_chat_setup_complete(chat_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE chats SET setup_complete = 1 WHERE id = ?", (chat_id,))


def get_chat_projects(chat_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.* FROM projects p "
            "JOIN chat_projects cp ON cp.project_id = p.id "
            "WHERE cp.chat_id = ? ORDER BY p.name",
            (chat_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def set_chat_projects(chat_id: int, project_ids: list):
    with get_conn() as conn:
        conn.execute("DELETE FROM chat_projects WHERE chat_id = ?", (chat_id,))
        for pid in project_ids:
            conn.execute(
                "INSERT OR IGNORE INTO chat_projects (chat_id, project_id) VALUES (?, ?)",
                (chat_id, pid)
            )


# ── Whitelist ──────────────────────────────────────────────────────────────────

def is_whitelisted(entry_type: str, telegram_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM whitelist WHERE type = ? AND telegram_id = ?",
            (entry_type, telegram_id)
        ).fetchone()
        return row is not None


def add_to_whitelist(entry_type: str, telegram_id: int, added_by: int) -> bool:
    """Returns True if added, False if already present."""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO whitelist (type, telegram_id, added_by) VALUES (?, ?, ?)",
                (entry_type, telegram_id, added_by)
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_from_whitelist(entry_type: str, telegram_id: int) -> bool:
    """Returns True if removed, False if not found."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM whitelist WHERE type = ? AND telegram_id = ?",
            (entry_type, telegram_id)
        )
        return cur.rowcount > 0


def get_whitelist():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM whitelist ORDER BY type, added_at"
        ).fetchall()
        return [dict(r) for r in rows]


# ── References ─────────────────────────────────────────────────────────────────

def add_reference(user_id: int, project_id: int, url: str,
                  title: str = "", description: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO saved_references (user_id, project_id, url, title, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, project_id, url, title, description)
        )
        return cur.lastrowid


def get_references(user_id: int, project_id: int, limit: int = 10):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM saved_references WHERE user_id=? AND project_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, project_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_reference(ref_id: int, user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM saved_references WHERE id=? AND user_id=?", (ref_id, user_id))


# ── Ideas ──────────────────────────────────────────────────────────────────────

def add_idea(user_id: int, project_id: int, content: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO ideas (user_id, project_id, content) VALUES (?, ?, ?)",
            (user_id, project_id, content)
        )
        return cur.lastrowid


def get_ideas(user_id: int, project_id: int, limit: int = 10):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM ideas WHERE user_id=? AND project_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, project_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_idea(idea_id: int, user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM ideas WHERE id=? AND user_id=?", (idea_id, user_id))


# ── Journal ────────────────────────────────────────────────────────────────────

def add_journal_entry(user_id: int, project_id: int, content: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO journal (user_id, project_id, content) VALUES (?, ?, ?)",
            (user_id, project_id, content)
        )
        return cur.lastrowid


def get_journal_entries(user_id: int, project_id: int, limit: int = 10):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM journal WHERE user_id=? AND project_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, project_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_journal_entry(journal_id: int, user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM journal WHERE id=? AND user_id=?", (journal_id, user_id))


# ── Reclassify helpers ─────────────────────────────────────────────────────────

def delete_note(note_id: int, user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM notes WHERE id=? AND user_id=?", (note_id, user_id))


def delete_task_record(task_id: int, user_id: int):
    """Hard-delete a task (distinct from complete_task which marks it done)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (task_id, user_id))


# ── Search ─────────────────────────────────────────────────────────────────────

def search_all(user_id: int, project_id: int, query: str, limit_per_type: int = 5) -> dict:
    """Search across all content tables for the given project."""
    pat = f"%{query}%"
    with get_conn() as conn:
        notes = conn.execute(
            "SELECT id, 'note' AS type, refined_text AS content, created_at FROM notes "
            "WHERE user_id=? AND project_id=? AND (refined_text LIKE ? OR tags LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, project_id, pat, pat, limit_per_type)
        ).fetchall()

        tasks = conn.execute(
            "SELECT id, 'task' AS type, title AS content, created_at FROM tasks "
            "WHERE user_id=? AND project_id=? AND status='pending' "
            "AND (title LIKE ? OR description LIKE ? OR tags LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, project_id, pat, pat, pat, limit_per_type)
        ).fetchall()

        ideas = conn.execute(
            "SELECT id, 'idea' AS type, content, created_at FROM ideas "
            "WHERE user_id=? AND project_id=? AND content LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, project_id, pat, limit_per_type)
        ).fetchall()

        journal_rows = conn.execute(
            "SELECT id, 'journal' AS type, content, created_at FROM journal "
            "WHERE user_id=? AND project_id=? AND content LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, project_id, pat, limit_per_type)
        ).fetchall()

        refs = conn.execute(
            "SELECT id, 'reference' AS type, COALESCE(NULLIF(title,''), url) AS content, created_at "
            "FROM saved_references "
            "WHERE user_id=? AND project_id=? AND (title LIKE ? OR description LIKE ? OR url LIKE ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, project_id, pat, pat, pat, limit_per_type)
        ).fetchall()

    return {
        "notes":      [dict(r) for r in notes],
        "tasks":      [dict(r) for r in tasks],
        "ideas":      [dict(r) for r in ideas],
        "journal":    [dict(r) for r in journal_rows],
        "references": [dict(r) for r in refs],
    }


# ── Daily digest ───────────────────────────────────────────────────────────────

def get_active_users_today() -> list:
    """Return user_ids who had any activity today and have a private chat registered."""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT user_id FROM (
                SELECT user_id FROM notes            WHERE date(created_at) = ?
                UNION
                SELECT user_id FROM tasks            WHERE date(created_at) = ?
                UNION
                SELECT user_id FROM ideas            WHERE date(created_at) = ?
                UNION
                SELECT user_id FROM journal          WHERE date(created_at) = ?
                UNION
                SELECT user_id FROM saved_references WHERE date(created_at) = ?
            )
            WHERE user_id IN (SELECT id FROM chats WHERE chat_type = 'private')
        """, (today, today, today, today, today)).fetchall()
        return [r[0] for r in rows]


def get_daily_activity(user_id: int) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        def _count(table, extra_where=""):
            return conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id=? AND date(created_at)=? {extra_where}",
                (user_id, today)
            ).fetchone()[0]

        tasks_completed = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id=? AND date(completed_at)=?",
            (user_id, today)
        ).fetchone()[0]

        upcoming = conn.execute(
            "SELECT title, deadline FROM tasks WHERE user_id=? AND status='pending' "
            "AND deadline IS NOT NULL ORDER BY deadline ASC LIMIT 5",
            (user_id,)
        ).fetchall()

        project_names = conn.execute(
            "SELECT DISTINCT p.name FROM projects p "
            "JOIN notes n ON n.project_id = p.id WHERE n.user_id=? AND date(n.created_at)=? "
            "UNION "
            "SELECT DISTINCT p.name FROM projects p "
            "JOIN tasks t ON t.project_id = p.id WHERE t.user_id=? AND date(t.created_at)=?",
            (user_id, today, user_id, today)
        ).fetchall()

        return {
            "notes":           _count("notes"),
            "tasks_created":   _count("tasks"),
            "tasks_completed": tasks_completed,
            "ideas":           _count("ideas"),
            "journal":         _count("journal"),
            "references":      _count("saved_references"),
            "upcoming_tasks":  [dict(r) for r in upcoming],
            "project_names":   [r[0] for r in project_names],
        }
