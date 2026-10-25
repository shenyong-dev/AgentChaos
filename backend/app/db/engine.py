from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from app.core.config import Settings, get_settings


def create_database_engine(
    settings: Settings | None = None, *, database: str | None = None
) -> Engine:
    settings = settings or get_settings()
    url = settings.mysql_url
    if database is not None:
        url = url.set(database=database)
    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        isolation_level="REPEATABLE READ",
        connect_args={"connect_timeout": 5, "read_timeout": 15, "write_timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, connection_record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET SESSION time_zone = '+00:00'")
            cursor.execute("SET SESSION innodb_lock_wait_timeout=5")

    return engine
