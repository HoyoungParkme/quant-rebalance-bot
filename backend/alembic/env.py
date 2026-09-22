"""Alembic 환경. DB 경로는 Settings가 아니라 -x db_path= 또는 환경 변수 QBOT_DB_PATH로 받는다 (비밀값 불필요)."""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import create_engine

from alembic import context
from app.core.models_registry import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _db_path() -> str:
    x = context.get_x_argument(as_dictionary=True)
    mode = os.environ.get("MODE", "paper")
    p = x.get("db_path") or os.environ.get("QBOT_DB_PATH") or os.path.expanduser(f"~/.qbot/qbot-{mode}.sqlite3")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def run_migrations_offline() -> None:
    context.configure(
        url=f"sqlite:///{_db_path()}", target_metadata=target_metadata, literal_binds=True, render_as_batch=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        f"sqlite:///{_db_path()}", future=True, connect_args={"timeout": 60}
    )  # 적재 중에도 마이그레이션이 들어갈 수 있게 기다린다
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
