"""在单一数据库事务中重包裹全部模型供应商和工具凭证数据密钥。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import UUID

from ai_platform_api.common.security import (
    EncryptedSecret,
    EnvelopeSecretCipher,
    MasterKeyFile,
)
from ai_platform_api.modules.tool_execution.domain.credentials import (
    tool_credential_associated_data,
)
from ai_platform_api.persistence.tables import model_provider_credentials, tool_credentials
from sqlalchemy import Connection, Engine, create_engine, select, update


def _associated_data(
    provider_id: UUID,
    credential_id: UUID,
    credential_version: int,
) -> bytes:
    return (
        f"model-provider-credential:v1:{provider_id}:{credential_id}:{credential_version}"
    ).encode()


def rewrap_provider_credentials(
    connection: Connection,
    *,
    old_key: MasterKeyFile,
    new_key: MasterKeyFile,
) -> int:
    """锁定凭据行并重包裹数据密钥，任意版本或更新冲突会回滚全部记录。"""

    rows = connection.execute(select(model_provider_credentials).with_for_update()).mappings().all()
    cipher = EnvelopeSecretCipher(old_key)
    for row in rows:
        if row["master_key_version"] != old_key.version:
            raise ValueError("数据库包含与当前主密钥版本不一致的凭据")
        envelope = EncryptedSecret(
            key_version=row["master_key_version"],
            encrypted_data_key=bytes(row["encrypted_data_key"]),
            data_key_nonce=bytes(row["data_key_nonce"]),
            ciphertext=bytes(row["ciphertext"]),
            data_nonce=bytes(row["data_nonce"]),
            last_four=row["last_four"],
        )
        rewrapped = cipher.rewrap(
            envelope,
            new_master_key=new_key,
            associated_data=_associated_data(
                row["provider_id"],
                row["credential_id"],
                row["credential_version"],
            ),
        )
        result = connection.execute(
            update(model_provider_credentials)
            .where(
                model_provider_credentials.c.credential_id == row["credential_id"],
                model_provider_credentials.c.master_key_version == old_key.version,
            )
            .values(
                master_key_version=rewrapped.key_version,
                encrypted_data_key=rewrapped.encrypted_data_key,
                data_key_nonce=rewrapped.data_key_nonce,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("供应商凭据主密钥轮换发生并发冲突")
    return len(rows)


def rewrap_tool_credentials(
    connection: Connection,
    *,
    old_key: MasterKeyFile,
    new_key: MasterKeyFile,
) -> int:
    """重包裹全部工具凭证，保持密文正文与精确工具绑定不变。"""

    rows = connection.execute(select(tool_credentials).with_for_update()).mappings().all()
    cipher = EnvelopeSecretCipher(old_key)
    for row in rows:
        if row["master_key_version"] != old_key.version:
            raise ValueError("数据库包含与当前主密钥版本不一致的工具凭证")
        envelope = EncryptedSecret(
            key_version=row["master_key_version"],
            encrypted_data_key=bytes(row["encrypted_data_key"]),
            data_key_nonce=bytes(row["data_key_nonce"]),
            ciphertext=bytes(row["ciphertext"]),
            data_nonce=bytes(row["data_nonce"]),
            last_four=row["last_four"],
        )
        rewrapped = cipher.rewrap(
            envelope,
            new_master_key=new_key,
            associated_data=tool_credential_associated_data(
                workspace_id=row["workspace_id"],
                tool_id=row["tool_id"],
                tool_version=row["tool_version"],
                credential_id=row["credential_id"],
                credential_ref=row["credential_ref"],
                credential_version=row["credential_version"],
            ),
        )
        result = connection.execute(
            update(tool_credentials)
            .where(
                tool_credentials.c.credential_id == row["credential_id"],
                tool_credentials.c.master_key_version == old_key.version,
            )
            .values(
                master_key_version=rewrapped.key_version,
                encrypted_data_key=rewrapped.encrypted_data_key,
                data_key_nonce=rewrapped.data_key_nonce,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("工具凭证主密钥轮换发生并发冲突")
    return len(rows)


def rotate_database_credentials(
    database_url: str,
    *,
    old_key_path: Path,
    old_version: int,
    new_key_path: Path,
    new_version: int,
) -> int:
    """创建短连接并把全部凭据重包裹变更作为单一事务提交。"""

    engine: Engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            provider_count = rewrap_provider_credentials(
                connection,
                old_key=MasterKeyFile(str(old_key_path), old_version),
                new_key=MasterKeyFile(str(new_key_path), new_version),
            )
            tool_count = rewrap_tool_credentials(
                connection,
                old_key=MasterKeyFile(str(old_key_path), old_version),
                new_key=MasterKeyFile(str(new_key_path), new_version),
            )
            return provider_count + tool_count
    finally:
        engine.dispose()


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-key-file", type=Path, required=True)
    parser.add_argument("--old-version", type=int, required=True)
    parser.add_argument("--new-key-file", type=Path, required=True)
    parser.add_argument("--new-version", type=int, required=True)
    arguments = parser.parse_args()
    database_url = os.environ.get("AI_PLATFORM_DATABASE_URL")
    if not database_url:
        parser.error("必须通过 AI_PLATFORM_DATABASE_URL 提供数据库地址")
    count = rotate_database_credentials(
        database_url,
        old_key_path=arguments.old_key_file,
        old_version=arguments.old_version,
        new_key_path=arguments.new_key_file,
        new_version=arguments.new_version,
    )
    print(f"应用凭证数据密钥重包裹完成: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
