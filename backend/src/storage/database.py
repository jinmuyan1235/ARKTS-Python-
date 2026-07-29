"""SQLite database bootstrap for local analysis history."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

import config


SCHEMA_VERSION = 7
Migration = Callable[[sqlite3.Connection], None]


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the local application database and ensure the schema exists."""
    path = Path(db_path or config.APP_DB_PATH).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    initialize(connection)
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    """Apply all pending schema migrations."""
    current_version = _user_version(connection)
    if current_version > SCHEMA_VERSION:
        raise RuntimeError(f"数据库 schema 版本过新：{current_version} > {SCHEMA_VERSION}")
    try:
        connection.execute("BEGIN")
        for version in range(current_version + 1, SCHEMA_VERSION + 1):
            migration = MIGRATIONS.get(version)
            if migration is None:
                raise RuntimeError(f"缺少数据库迁移：v{version}")
            migration(connection)
            connection.execute(f"PRAGMA user_version = {version}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _user_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def _migrate_v1(connection: sqlite3.Connection) -> None:
    """Create all history tables and indexes idempotently."""
    statements = [
        """
        CREATE TABLE IF NOT EXISTS analyses (
            analysis_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            input_type TEXT,
            filename TEXT,
            input_path TEXT,
            image_sha256 TEXT,
            backend TEXT,
            decision TEXT,
            status TEXT,
            final_smiles TEXT,
            inchikey TEXT,
            report_path TEXT,
            is_favorite INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS corrections (
            correction_id TEXT PRIMARY KEY,
            analysis_id TEXT NOT NULL,
            previous_smiles TEXT,
            new_smiles TEXT,
            source TEXT,
            created_at TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY (analysis_id) REFERENCES analyses(analysis_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            job_type TEXT,
            status TEXT,
            progress TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            result_path TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_analyses_created_at ON analyses(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_analyses_filename ON analyses(filename)",
        "CREATE INDEX IF NOT EXISTS idx_analyses_final_smiles ON analyses(final_smiles)",
        "CREATE INDEX IF NOT EXISTS idx_analyses_inchikey ON analyses(inchikey)",
        "CREATE INDEX IF NOT EXISTS idx_analyses_status ON analyses(status)",
        "CREATE INDEX IF NOT EXISTS idx_analyses_decision ON analyses(decision)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
    ]
    for statement in statements:
        connection.execute(statement)


def _migrate_v2(connection: sqlite3.Connection) -> None:
    """Add recoverable local-file deletion state to history rows."""
    columns = _column_names(connection, "analyses")
    additions = {
        "delete_status": "TEXT NOT NULL DEFAULT ''",
        "delete_errors": "TEXT NOT NULL DEFAULT ''",
        "delete_requested_at": "TEXT",
        "delete_updated_at": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE analyses ADD COLUMN {name} {definition}")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_analyses_delete_status ON analyses(delete_status)")


def _migrate_v3(connection: sqlite3.Connection) -> None:
    """Persist the complete model-candidate audit alongside the searchable index."""
    columns = _column_names(connection, "analyses")
    if "recognition_audit" not in columns:
        connection.execute("ALTER TABLE analyses ADD COLUMN recognition_audit TEXT NOT NULL DEFAULT '{}'")


def _migrate_v4(connection: sqlite3.Connection) -> None:
    """Add local user profiles and revocable login sessions."""
    statements = [
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            password_iterations INTEGER NOT NULL,
            avatar_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_used_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON user_sessions(token_hash)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON user_sessions(expires_at)",
    ]
    for statement in statements:
        connection.execute(statement)


def _migrate_v5(connection: sqlite3.Connection) -> None:
    """Attach new analyses to their creating user without rewriting legacy rows."""
    columns = _column_names(connection, "analyses")
    if "owner_user_id" not in columns:
        connection.execute(
            "ALTER TABLE analyses ADD COLUMN owner_user_id TEXT REFERENCES users(user_id) ON DELETE SET NULL"
        )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_analyses_owner_user_id ON analyses(owner_user_id)")


def _migrate_v6(connection: sqlite3.Connection) -> None:
    """Normalize legacy local team roles to the five mobile task categories."""
    connection.execute(
        """
        UPDATE users
        SET role = CASE
            WHEN username = 'jinmu' COLLATE NOCASE THEN '架构统筹'
            WHEN username = 'liuke' COLLATE NOCASE THEN '前端测试'
            WHEN role IN ('组长', '架构', '统筹') THEN '架构统筹'
            WHEN role IN ('图像', '文档', '图像文档') THEN '图像文档'
            WHEN role IN ('算法', '算法组', '后端', '模型') THEN '模型后端'
            WHEN role IN ('化学', '化学组', '数据') THEN '化学数据'
            WHEN role IN ('前端', '测试') THEN '前端测试'
            ELSE role
        END
        WHERE username IN ('jinmu', 'liuke') COLLATE NOCASE
           OR role IN ('组长', '架构', '统筹', '图像', '文档', '算法', '算法组',
                       '后端', '模型', '化学', '化学组', '数据', '前端', '测试')
        """
    )


def _migrate_v7(connection: sqlite3.Connection) -> None:
    """Separate product users from developer/team accounts."""
    columns = _column_names(connection, "users")
    if "account_type" not in columns:
        connection.execute(
            "ALTER TABLE users ADD COLUMN account_type TEXT NOT NULL DEFAULT 'developer'"
        )
    connection.execute(
        "UPDATE users SET account_type = 'developer' WHERE account_type NOT IN ('user', 'developer')"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_users_account_type ON users(account_type)")


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


MIGRATIONS: dict[int, Migration] = {
    1: _migrate_v1,
    2: _migrate_v2,
    3: _migrate_v3,
    4: _migrate_v4,
    5: _migrate_v5,
    6: _migrate_v6,
    7: _migrate_v7,
}
