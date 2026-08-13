from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

SCHEMA_TOKEN = "ai_platform"


def create_platform_engine(database_url: str, schema: str = "public") -> Engine:
    return create_engine(database_url).execution_options(
        schema_translate_map={SCHEMA_TOKEN: schema},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
