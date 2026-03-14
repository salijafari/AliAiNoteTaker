import sqlite3
from datetime import datetime
import os

DB_PATH = os.getenv("DB_PATH", "bot_data.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'pending',
                reminder_at TEXT,
                reminded INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (project_id) REFERENCES projects(id)
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                active_project_id INTEGER,
                FOREIGN KEY (active_project_id) REFERENCES projects(id)
            );
        ''')


def ensure_user(user_id: int):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))


def get_active_project(user_id: int):
    with get_conn() as conn:
        row = conn.execute("""
            SELECT p.* FROM users u
            JOIN projects p ON p.id = u.active_project_id
            WHERE u.user_id = ?
        """, (user_id,)).fetchone()
        return dict(row) if row else None


def set_active_project(user_id: int, project_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET active_project_id = ? WHERE user_id = ?",
            (project_id, user_id)
        )


def create_project(user_id: int, name: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (user_id, name) VALUES (?, ?)", (user_id, name)
        )
        project_id = cur.lastrowid
        conn.execute(
            "UPDATE users SET active_project_id = ? WHERE user_id = ?",
            (project_id, user_id)
        )
        return project_id


def get_projects(user_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM projects WHERE user_id = ? ORDER BY name", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_note(user_id: int, project_id: int, content: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notes (user_id, project_id, content) VALUES (?, ?, ?)",
            (user_id, project_id, content)
        )
        return cur.lastrowid


def get_notes(user_id: int, project_id: int, limit: int = 15):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM notes
            WHERE user_id = ? AND project_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, project_id, limit)).fetchall()
        return [dict(r) for r in rows]


def add_task(user_id: int, project_id: int, title: str,
             description: str = None, reminder_at: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO tasks (user_id, project_id, title, description, reminder_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, project_id, title, description, reminder_at))
        return cur.lastrowid


def get_tasks(user_id: int, project_id: int, status: str = 'pending'):
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
            "UPDATE tasks SET status = 'completed' WHERE id = ? AND user_id = ?",
            (task_id, user_id)
        )


def set_task_reminder(task_id: int, user_id: int, reminder_at: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET reminder_at = ?, reminded = 0 WHERE id = ? AND user_id = ?",
            (reminder_at, task_id, user_id)
        )


def get_due_reminders():
    """Return tasks whose reminder time has passed and haven't been sent yet."""
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
