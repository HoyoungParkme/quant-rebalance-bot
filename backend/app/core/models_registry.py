"""Alembic과 테스트가 모든 테이블을 한 번에 알게 하는 등록 지점. 도메인 models를 import만 한다."""

from app.core.db import Base
from app.domains.decision import models as _decision  # noqa: F401
from app.domains.marketdata import models as _marketdata  # noqa: F401
from app.domains.ops import models as _ops  # noqa: F401
from app.domains.reporting import models as _reporting  # noqa: F401
from app.domains.risk import models as _risk  # noqa: F401
from app.domains.trading import models as _trading  # noqa: F401

metadata = Base.metadata
