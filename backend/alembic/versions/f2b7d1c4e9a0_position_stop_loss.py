"""보유에 손절 표시 날짜 칸 추가

Revision ID: f2b7d1c4e9a0
Revises: e4a1c9f07b21
Create Date: 2026-09-30

저녁 점검에서 확정 종가가 매입 평단 대비 -15% 이하인 종목에 날짜를 적는다(QBOT-PRD-001 R6).
다음 거래일 매도 목록에 들어가고, 이 날짜가 판단 기준일보다 뒤면 그 판단에서 되사지 않는다.
"""

import sqlalchemy as sa
from alembic import op

revision = "f2b7d1c4e9a0"
down_revision = "e4a1c9f07b21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("position", sa.Column("stop_loss_on", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("position", "stop_loss_on")
