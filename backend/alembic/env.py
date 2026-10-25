from logging.config import fileConfig

from alembic import context
from app.core.config import get_settings
from app.db.engine import create_database_engine
from app.models import Base
from app.models.base import SnapshotJSON, UTCDateTime

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)


def render_item(kind, item, autogen_context):
    if kind == "type" and isinstance(item, UTCDateTime):
        autogen_context.imports.add("from sqlalchemy.dialects import mysql")
        return "mysql.DATETIME(fsp=6)"
    if kind == "type" and isinstance(item, SnapshotJSON):
        autogen_context.imports.add("from sqlalchemy.dialects import mysql")
        return "mysql.JSON(none_as_null=True)"
    return False


def run(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=get_settings().mysql_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    engine = create_database_engine()
    try:
        with engine.connect() as connection:
            run(connection)
    finally:
        engine.dispose()
