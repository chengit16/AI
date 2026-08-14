"""使用 Valkey 管理服务端浏览器 Session、CSRF 和过期撤销。"""

from __future__ import annotations

import json
import secrets
from uuid import UUID

from redis import Redis

from ai_platform_api.modules.identity.domain.models import BrowserSession
from ai_platform_api.modules.identity.infrastructure.security import Sha256SecretDigester


class ValkeySessionStore:
    """Valkey 只保存短期会话状态；账号与空间成员事实仍逐请求从 PostgreSQL 验证。"""

    def __init__(self, url: str) -> None:
        self._client = Redis.from_url(url, decode_responses=True)
        self._digester = Sha256SecretDigester()

    def create(self, session: BrowserSession, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(48)
        payload = json.dumps(
            {
                "account_id": str(session.account_id),
                "auth_version": session.auth_version,
                "csrf_digest": session.csrf_digest,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        self._client.set(self._key(token), payload, ex=ttl_seconds)
        return token

    def resolve(self, token: str) -> BrowserSession | None:
        payload = self._client.get(self._key(token))
        if not isinstance(payload, str):
            return None
        try:
            document: object = json.loads(payload)
            if not isinstance(document, dict):
                return None
            account_id = document.get("account_id")
            auth_version = document.get("auth_version")
            csrf_digest = document.get("csrf_digest")
            if (
                not isinstance(account_id, str)
                or not isinstance(auth_version, int)
                or isinstance(auth_version, bool)
                or auth_version < 1
                or not isinstance(csrf_digest, str)
            ):
                return None
            return BrowserSession(
                account_id=UUID(account_id),
                auth_version=auth_version,
                csrf_digest=csrf_digest,
            )
        except (json.JSONDecodeError, ValueError):
            # 缓存载荷不可验证时默认视为无会话，不把损坏内容带入认证链。
            return None

    def revoke(self, token: str) -> None:
        self._client.delete(self._key(token))

    def close(self) -> None:
        self._client.close()

    def _key(self, token: str) -> str:
        return f"session:v1:{self._digester.digest(token)}"
