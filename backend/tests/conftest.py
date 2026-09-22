"""공통 픽스처: 메모리 SQLite에 전체 테이블을 만든 세션."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.models_registry import metadata


@pytest.fixture
def session():
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    yield s
    s.close()
