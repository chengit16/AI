from __future__ import annotations

import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ai_platform_api.common.security import (
    EncryptedSecret,
    EnvelopeSecretCipher,
    MasterKeyFile,
)

__all__ = [
    "Argon2idPasswordAdapter",
    "EncryptedSecret",
    "EnvelopeSecretCipher",
    "MasterKeyFile",
    "Sha256SecretDigester",
]


class Argon2idPasswordAdapter:
    """固定 Argon2id 参数，哈希内携带版本和盐以支持后续渐进升级。"""

    def __init__(self) -> None:
        self._hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
        )
        self._dummy_hash = self._hasher.hash("synthetic-password-never-used")

    def hash(self, password: str) -> str:
        if len(password) < 12:
            raise ValueError("密码长度不能少于 12 个字符")
        return self._hasher.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        try:
            verified = self._hasher.verify(password_hash or self._dummy_hash, password)
            return bool(password_hash is not None and verified)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False


class Sha256SecretDigester:
    """高熵 Session/API Key 使用单向摘要，原始凭证不进入事实存储。"""

    @staticmethod
    def digest(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    def matches(self, expected_digest: str, secret: str) -> bool:
        return hmac.compare_digest(expected_digest, self.digest(secret))
