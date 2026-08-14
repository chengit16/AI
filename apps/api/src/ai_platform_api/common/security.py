"""提供主密钥读取和带关联数据的应用凭证信封加密边界。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class EncryptedSecret:
    """保存数据密文、加密后的数据密钥、Nonce 和主密钥版本。"""

    key_version: int
    encrypted_data_key: bytes
    data_key_nonce: bytes
    ciphertext: bytes
    data_nonce: bytes
    last_four: str


class MasterKeyFile:
    """延迟读取本地主密钥，避免未使用秘密能力时阻塞普通请求。"""

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
        """使用数据密钥加密敏感凭据，并用关联数据阻止密文跨记录替换。"""

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
        """校验关联数据后解密凭据，认证失败时不泄露密文或明文细节。"""

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
