"""Small, dependency-light account and session layer for the production demo.

The app can later replace this module with an OIDC provider without changing
the trip ownership contract: every request still resolves to a user id.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SESSION_COOKIE = "tripmate_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
PASSWORD_RESET_TTL_SECONDS = 60 * 30
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def auth_required() -> bool:
    return os.getenv("AUTH_REQUIRED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _secret() -> bytes:
    configured = os.getenv("APP_SECRET_KEY", "").strip()
    if configured:
        return configured.encode("utf-8")
    # Local development remains usable, while production should always set a stable secret.
    return b"local-development-secret-change-me"


def normalize_email(email: str) -> str:
    value = str(email or "").strip().lower()
    if not EMAIL_RE.fullmatch(value):
        raise ValueError("Email không hợp lệ.")
    return value


def validate_password(password: str) -> str:
    value = str(password or "")
    if len(value) < 8:
        raise ValueError("Mật khẩu phải có ít nhất 8 ký tự.")
    if len(value) > 128:
        raise ValueError("Mật khẩu không được vượt quá 128 ký tự.")
    return value


def hash_password(password: str, salt: bytes | None = None) -> str:
    value = validate_password(password)
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(value.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return "scrypt$16384$8$1${}${}".format(
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            validate_password(password).encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_token(token: str) -> str:
    """Hash bearer-style tokens before storing or comparing them in Postgres."""
    return _hash_token(token)


def apply_migrations(connection: Any) -> None:
    """Apply ordered, idempotent SQL migrations for app-owned tables."""
    migrations_dir = Path(__file__).resolve().parent / "migrations"
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute("SELECT version FROM schema_migrations")
        applied = {row["version"] if isinstance(row, dict) else row[0] for row in cursor.fetchall()}

    for path in sorted(migrations_dir.glob("*.sql")):
        version = path.stem
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(sql)
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING", (version,))


def ensure_auth_schema(connection: Any) -> None:
    """Create app-owned tables; LangGraph checkpoint tables remain separate."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS app_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_app_sessions_user ON app_sessions(user_id)")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trip_metadata (
                thread_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                title TEXT NOT NULL DEFAULT 'Chuyến đi mới',
                archived BOOLEAN NOT NULL DEFAULT FALSE,
                share_token_hash TEXT,
                share_expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute("ALTER TABLE trip_metadata ADD COLUMN IF NOT EXISTS share_token_hash TEXT")
        cursor.execute("ALTER TABLE trip_metadata ADD COLUMN IF NOT EXISTS share_expires_at TIMESTAMPTZ")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trip_metadata_user ON trip_metadata(user_id, updated_at DESC)")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id BIGSERIAL PRIMARY KEY,
                user_id TEXT,
                thread_id TEXT,
                event_type TEXT NOT NULL,
                request_id TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_thread ON audit_events(thread_id, created_at DESC)")
        cursor.execute(
            """
            INSERT INTO app_users (id, email, display_name, password_hash)
            VALUES ('guest', 'guest@local.invalid', 'Guest', 'disabled')
            ON CONFLICT (id) DO NOTHING
            """
        )
        # Preserve sessions created before ownership metadata was introduced in local/guest mode.
        cursor.execute(
            """
            INSERT INTO trip_metadata (thread_id, user_id, title)
            SELECT DISTINCT c.thread_id, 'guest', 'Chuyến đi cũ'
            FROM checkpoints c
            WHERE c.checkpoint_ns = ''
              AND NOT EXISTS (SELECT 1 FROM trip_metadata tm WHERE tm.thread_id = c.thread_id)
            ON CONFLICT (thread_id) DO NOTHING
            """
        )
    apply_migrations(connection)
    connection.commit()


def create_user(connection: Any, email: str, password: str, display_name: str = "") -> dict[str, str]:
    normalized = normalize_email(email)
    name = str(display_name or "").strip()[:80] or normalized.split("@", 1)[0]
    user_id = f"usr_{uuid.uuid4().hex}"
    with connection.cursor() as cursor:
        try:
            cursor.execute(
                "INSERT INTO app_users (id, email, display_name, password_hash) VALUES (%s, %s, %s, %s)",
                (user_id, normalized, name, hash_password(password)),
            )
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise ValueError("Email này đã được đăng ký.") from exc
            raise
    connection.commit()
    return {"id": user_id, "email": normalized, "display_name": name}


def find_user_by_credentials(connection: Any, email: str, password: str) -> dict[str, str] | None:
    normalized = normalize_email(email)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, email, display_name, password_hash FROM app_users WHERE email = %s AND is_active = TRUE",
            (normalized,),
        )
        row = cursor.fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return {"id": row["id"], "email": row["email"], "display_name": row["display_name"]}


def create_session(connection: Any, user_id: str) -> str:
    token = secrets.token_urlsafe(48)
    expires = datetime.now(timezone.utc).timestamp() + SESSION_TTL_SECONDS
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO app_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, to_timestamp(%s))",
            (_hash_token(token), user_id, expires),
        )
    connection.commit()
    return token


def get_user_from_session(connection: Any, token: str | None) -> dict[str, str] | None:
    if not token:
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT u.id, u.email, u.display_name
            FROM app_sessions s
            JOIN app_users u ON u.id = s.user_id
            WHERE s.token_hash = %s AND s.expires_at > NOW() AND u.is_active = TRUE
            """,
            (_hash_token(token),),
        )
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE app_sessions SET last_seen_at = NOW() WHERE token_hash = %s", (_hash_token(token),))
    connection.commit()
    return dict(row) if row else None


def revoke_session(connection: Any, token: str | None) -> None:
    if not token:
        return
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM app_sessions WHERE token_hash = %s", (_hash_token(token),))
    connection.commit()


def create_password_reset_token(connection: Any, email: str) -> str | None:
    normalized = normalize_email(email)
    token = secrets.token_urlsafe(48)
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM app_users WHERE email = %s AND is_active = TRUE", (normalized,))
            row = cursor.fetchone()
            if not row:
                return None
            cursor.execute("DELETE FROM password_reset_tokens WHERE user_id = %s OR expires_at < NOW()", (row["id"],))
            cursor.execute(
                "INSERT INTO password_reset_tokens (token_hash, user_id, expires_at) VALUES (%s, %s, NOW() + (%s * INTERVAL '1 second'))",
                (_hash_token(token), row["id"], PASSWORD_RESET_TTL_SECONDS),
            )
    connection.commit()
    return token


def reset_password(connection: Any, token: str, password: str) -> bool:
    validated = validate_password(password)
    if not token or len(token) > 200:
        return False
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id FROM password_reset_tokens
                WHERE token_hash = %s AND expires_at > NOW() AND used_at IS NULL
                FOR UPDATE
                """,
                (_hash_token(token),),
            )
            row = cursor.fetchone()
            if not row:
                return False
            cursor.execute("UPDATE app_users SET password_hash = %s, updated_at = NOW() WHERE id = %s", (hash_password(validated), row["user_id"]))
            cursor.execute("UPDATE password_reset_tokens SET used_at = NOW() WHERE token_hash = %s", (_hash_token(token),))
            cursor.execute("DELETE FROM app_sessions WHERE user_id = %s", (row["user_id"],))
    connection.commit()
    return True


def signed_guest_id() -> str:
    payload = {"sub": "guest", "exp": int(time.time()) + 86400}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(_secret(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"guest.{encoded}.{signature}"
