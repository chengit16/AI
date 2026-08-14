"""配置 Alembic 在线与离线 Migration 的 Schema 和数据库连接来源。"""

import os
from logging.config import fileConfig

from ai_platform_api.persistence.tables import metadata
from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def schema_name() -> str:
    return config.get_main_option("ai_platform_schema", "public")


def database_configuration() -> dict[str, str]:
    configuration = config.get_section(config.config_ini_section, {})
    # 容器和测试环境由部署入口注入数据库地址；ini 中的回环地址只作为本机默认值。
    injected_url = os.environ.get("AI_PLATFORM_DATABASE_URL")
    if injected_url:
        configuration["sqlalchemy.url"] = injected_url
    return configuration


def run_migrations_offline() -> None:
    context.configure(
        url=database_configuration()["sqlalchemy.url"],
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=schema_name(),
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        database_configuration(),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=schema_name(),
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
