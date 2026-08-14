"""验证共享数据库引擎、Schema 和 Session 基线。"""

from ai_platform_api.persistence.database import PlatformDatabase
from sqlalchemy import text


def test_database_factory_configures_independent_sessions() -> None:
    database = PlatformDatabase.create("sqlite+pysqlite:///:memory:")
    try:
        first = database.sessions()
        second = database.sessions()
        try:
            assert first is not second
            assert first.autoflush is False
            assert first.expire_on_commit is False
            first.execute(text("CREATE TABLE synthetic_records (value INTEGER NOT NULL)"))
            first.execute(text("INSERT INTO synthetic_records (value) VALUES (1)"))
            first.rollback()
            count = second.scalar(text("SELECT COUNT(*) FROM synthetic_records"))
            assert count == 0
        finally:
            first.close()
            second.close()
    finally:
        database.close()
