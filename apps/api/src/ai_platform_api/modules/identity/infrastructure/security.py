from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


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


@dataclass(frozen=True)
class EncryptedSecret:
    key_version: int
    encrypted_data_key: bytes
    data_key_nonce: bytes
    ciphertext: bytes
    data_nonce: bytes
    last_four: str


class MasterKeyFile:
    """延迟读取本地主密钥，避免普通身份功能被未启用的供应商凭证阻塞。"""

    def __init__(self, path: str, version: int = 1) -> None:
        self._path = Path(path)
        self.version = version

    def load(self) -> bytes:
        if self._path.is_symlink():
            raise PermissionError("平台主密钥不能是符号链接")
        if not self._path.is_file():
            raise FileNotFoundError("平台主密钥文件不存在")
        key = self._path.read_bytes()
        if len(key) != 32:
            raise ValueError("平台主密钥必须正好为 32 字节")
        if os.name == "posix" and self._path.stat().st_mode & 0o077:
            raise PermissionError("平台主密钥权限必须限制为当前用户可读")
        return key


class EnvelopeSecretCipher:
    """每条凭证使用独立数据密钥，主密钥只负责包裹数据密钥。"""

    def __init__(self, master_key: MasterKeyFile) -> None:
        self._master_key = master_key

    def encrypt(self, plaintext: str, *, associated_data: bytes) -> EncryptedSecret:
        data_key = AESGCM.generate_key(bit_length=256)
        data_nonce = os.urandom(12)
        data_key_nonce = os.urandom(12)
        ciphertext = AESGCM(data_key).encrypt(
            data_nonce,
            plaintext.encode("utf-8"),
            associated_data,
        )
        encrypted_data_key = AESGCM(self._master_key.load()).encrypt(
            data_key_nonce,
            data_key,
            associated_data,
        )
        return EncryptedSecret(
            key_version=self._master_key.version,
            encrypted_data_key=encrypted_data_key,
            data_key_nonce=data_key_nonce,
            ciphertext=ciphertext,
            data_nonce=data_nonce,
            last_four=plaintext[-4:],
        )

    def decrypt(self, secret: EncryptedSecret, *, associated_data: bytes) -> str:
        if secret.key_version != self._master_key.version:
            raise ValueError("凭证主密钥版本不匹配")
        data_key = AESGCM(self._master_key.load()).decrypt(
            secret.data_key_nonce,
            secret.encrypted_data_key,
            associated_data,
        )
        plaintext = AESGCM(data_key).decrypt(
            secret.data_nonce,
            secret.ciphertext,
            associated_data,
        )
        return plaintext.decode("utf-8")
