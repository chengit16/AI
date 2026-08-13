from dataclasses import dataclass

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

SCHEMA_TOKEN = "ai_platform"


def create_platform_engine(database_url: str, schema: str = "public") -> Engine:
    engine = create_engine(database_url, pool_pre_ping=True)
    return engine.execution_options(schema_translate_map={SCHEMA_TOKEN: schema})


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@dataclass(frozen=True)
class PlatformDatabase:
    """固定 Engine 与 Session Factory 生命周期，禁止进程内共享可变 Session。"""

    engine: Engine
    sessions: sessionmaker[Session]

    @classmethod
    def create(cls, database_url: str, schema: str = "public") -> "PlatformDatabase":
        engine = create_platform_engine(database_url, schema)
        return cls(engine=engine, sessions=create_session_factory(engine))

    def close(self) -> None:
        self.engine.dispose()
