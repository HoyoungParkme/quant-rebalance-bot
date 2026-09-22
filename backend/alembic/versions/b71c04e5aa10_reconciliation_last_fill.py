"""대조에 마지막 반영 체결 번호 추가

Revision ID: b71c04e5aa10
Revises: c52503255538
Create Date: 2026-09-22

시각으로 "지난 대조 이후의 체결"을 가르면 같은 초에 일어난 대조와 체결의 앞뒤를 알 수 없다.
체결 번호를 기준점으로 쓴다.
"""

import sqlalchemy as sa
from alembic import op

revision = "b71c04e5aa10"
down_revision = "c52503255538"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reconciliation", sa.Column("last_fill_id", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("reconciliation", "last_fill_id")
