"""Local user profiles, password verification, and revocable sessions."""

from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.storage.database import connect


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
PASSWORD_ITERATIONS = 310_000
SESSION_LIFETIME = timedelta(days=7)


class AuthRepository:
    """Persist local accounts in the same SQLite database as history."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path).expanduser().resolve() if db_path is not None else None

    def register(
        self,
        username: str,
        display_name: str,
        password: str,
        role: str = "",
        account_type: str = "developer",
    ) -> dict[str, Any]:
        normalized_username = username.strip()
        normalized_name = display_name.strip()
        normalized_account_type = account_type.strip().lower()
        normalized_role = role.strip() if normalized_account_type == "developer" else ""
        _validate_registration(
            normalized_username, normalized_name, password, normalized_role, normalized_account_type
        )
        salt = secrets.token_bytes(16)
        now = utc_now()
        user = {
            "user_id": uuid4().hex,
            "username": normalized_username,
            "display_name": normalized_name,
            "role": normalized_role,
            "account_type": normalized_account_type,
            "password_hash": _password_hash(password, salt, PASSWORD_ITERATIONS),
            "password_salt": base64.b64encode(salt).decode("ascii"),
            "password_iterations": PASSWORD_ITERATIONS,
            "avatar_path": None,
            "created_at": now,
            "updated_at": now,
        }
        with closing(connect(self.db_path)) as connection:
            exists = connection.execute(
                "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE",
                (normalized_username,),
            ).fetchone()
            if exists is not None:
                raise ValueError("该用户名已经注册。")
            connection.execute(
                """
                INSERT INTO users (
                    user_id, username, display_name, role, account_type, password_hash, password_salt,
                    password_iterations, avatar_path, created_at, updated_at
                )
                VALUES (
                    :user_id, :username, :display_name, :role, :account_type, :password_hash, :password_salt,
                    :password_iterations, :avatar_path, :created_at, :updated_at
                )
                """,
                user,
            )
            connection.commit()
        return public_user(user)

    def authenticate_password(self, username: str, password: str) -> dict[str, Any] | None:
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (username.strip(),),
            ).fetchone()
        if row is None:
            _dummy_password_check(password)
            return None
        user = dict(row)
        try:
            salt = base64.b64decode(str(user["password_salt"]).encode("ascii"))
            candidate = _password_hash(password, salt, int(user["password_iterations"]))
        except (ValueError, TypeError):
            return None
        if not hmac.compare_digest(candidate, str(user["password_hash"])):
            return None
        return public_user(user)

    def create_session(self, user_id: str) -> str:
        raw_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        payload = {
            "session_id": uuid4().hex,
            "user_id": user_id,
            "token_hash": _token_hash(raw_token),
            "created_at": now.isoformat(),
            "expires_at": (now + SESSION_LIFETIME).isoformat(),
            "last_used_at": now.isoformat(),
        }
        with closing(connect(self.db_path)) as connection:
            connection.execute(
                """
                INSERT INTO user_sessions (
                    session_id, user_id, token_hash, created_at, expires_at, last_used_at
                )
                VALUES (
                    :session_id, :user_id, :token_hash, :created_at, :expires_at, :last_used_at
                )
                """,
                payload,
            )
            connection.execute(
                "DELETE FROM user_sessions WHERE expires_at <= ?",
                (now.isoformat(),),
            )
            connection.commit()
        return raw_token

    def user_for_token(self, raw_token: str) -> dict[str, Any] | None:
        if not raw_token:
            return None
        now = utc_now()
        token_hash = _token_hash(raw_token)
        with closing(connect(self.db_path)) as connection:
            row = connection.execute(
                """
                SELECT users.*
                FROM user_sessions
                JOIN users ON users.user_id = user_sessions.user_id
                WHERE user_sessions.token_hash = ? AND user_sessions.expires_at > ?
                """,
                (token_hash, now),
            ).fetchone()
            if row is not None:
                connection.execute(
                    "UPDATE user_sessions SET last_used_at = ? WHERE token_hash = ?",
                    (now, token_hash),
                )
                connection.commit()
        return public_user(dict(row)) if row is not None else None

    def revoke_session(self, raw_token: str) -> None:
        with closing(connect(self.db_path)) as connection:
            connection.execute("DELETE FROM user_sessions WHERE token_hash = ?", (_token_hash(raw_token),))
            connection.commit()

    def list_users(self, account_type: str | None = None) -> list[dict[str, Any]]:
        with closing(connect(self.db_path)) as connection:
            if account_type is None:
                rows = connection.execute(
                    """
                    SELECT user_id, username, display_name, role, account_type, avatar_path, created_at, updated_at
                    FROM users
                    ORDER BY created_at ASC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT user_id, username, display_name, role, account_type, avatar_path, created_at, updated_at
                    FROM users
                    WHERE account_type = ?
                    ORDER BY created_at ASC
                    """,
                    (account_type,),
                ).fetchall()
        return [public_user(dict(row)) for row in rows]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with closing(connect(self.db_path)) as connection:
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return public_user(dict(row)) if row is not None else None

    def update_avatar(self, user_id: str, avatar_path: str | Path) -> dict[str, Any]:
        now = utc_now()
        with closing(connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE users SET avatar_path = ?, updated_at = ? WHERE user_id = ?",
                (str(Path(avatar_path).expanduser().resolve()), now, user_id),
            )
            connection.commit()
        user = self.get_user(user_id)
        if user is None:
            raise KeyError(user_id)
        return user

    def update_profile(self, user_id: str, display_name: str, role: str = "") -> dict[str, Any]:
        normalized_name = display_name.strip()
        normalized_role = role.strip()
        if not 1 <= len(normalized_name) <= 40:
            raise ValueError("显示名称需为 1–40 个字符。")
        if len(normalized_role) > 40:
            raise ValueError("组内角色不能超过 40 个字符。")
        with closing(connect(self.db_path)) as connection:
            cursor = connection.execute(
                """
                UPDATE users
                SET display_name = ?, role = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (normalized_name, normalized_role, utc_now(), user_id),
            )
            connection.commit()
        if cursor.rowcount == 0:
            raise KeyError(user_id)
        user = self.get_user(user_id)
        if user is None:
            raise KeyError(user_id)
        return user

    def change_password(self, user_id: str, old_password: str, new_password: str) -> dict[str, Any]:
        if not 8 <= len(new_password) <= 128:
            raise ValueError("新密码长度需为 8–128 个字符。")
        with closing(connect(self.db_path)) as connection:
            row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row is None:
                raise KeyError(user_id)
            user = dict(row)
            if not _verify_password(user, old_password):
                raise PermissionError("旧密码错误。")
            salt = secrets.token_bytes(16)
            now = utc_now()
            connection.execute(
                """
                UPDATE users
                SET password_hash = ?, password_salt = ?, password_iterations = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (
                    _password_hash(new_password, salt, PASSWORD_ITERATIONS),
                    base64.b64encode(salt).decode("ascii"),
                    PASSWORD_ITERATIONS,
                    now,
                    user_id,
                ),
            )
            connection.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            connection.commit()
        updated = self.get_user(user_id)
        if updated is None:
            raise KeyError(user_id)
        return updated


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": str(user.get("user_id") or ""),
        "username": str(user.get("username") or ""),
        "display_name": str(user.get("display_name") or ""),
        "role": str(user.get("role") or ""),
        "account_type": str(user.get("account_type") or "developer"),
        "avatar_path": str(user.get("avatar_path") or ""),
        "created_at": str(user.get("created_at") or ""),
        "updated_at": str(user.get("updated_at") or ""),
    }


def _validate_registration(
    username: str, display_name: str, password: str, role: str, account_type: str = "developer"
) -> None:
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("用户名需为 3–32 位，只能包含字母、数字、点、横线或下划线。")
    if not 1 <= len(display_name) <= 40:
        raise ValueError("显示名称需为 1–40 个字符。")
    if not 8 <= len(password) <= 128:
        raise ValueError("密码长度需为 8–128 个字符。")
    if len(role) > 40:
        raise ValueError("组内角色不能超过 40 个字符。")
    if account_type not in {"user", "developer"}:
        raise ValueError("账户类型仅支持 user 或 developer。")


def _password_hash(password: str, salt: bytes, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return base64.b64encode(digest).decode("ascii")


def _verify_password(user: dict[str, Any], password: str) -> bool:
    try:
        salt = base64.b64decode(str(user["password_salt"]).encode("ascii"))
        candidate = _password_hash(password, salt, int(user["password_iterations"]))
    except (ValueError, TypeError, KeyError):
        return False
    return hmac.compare_digest(candidate, str(user.get("password_hash") or ""))


def _dummy_password_check(password: str) -> None:
    hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"\x00" * 16, PASSWORD_ITERATIONS)


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
