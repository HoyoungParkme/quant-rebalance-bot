"""대조에 외부 입출금 금액 칸 추가

Revision ID: d18f2a3b5c40
Revises: b71c04e5aa10
Create Date: 2026-09-22

"계좌가 옳다"고 받아들인 차액에는 두 가지가 섞여 있다. 진짜 입출금과, 우리가 놓친 매매·수수료다.
앞의 것만 원금·고점 기준선을 옮겨야 한다. 뒤의 것까지 옮기면 그 손실이 손실 한도에서 사라진다.
사람이 승인할 때 "이만큼이 입출금"이라고 밝힌 금액만 여기에 적는다.
"""

import sqlalchemy as sa
from alembic import op

revision = "d18f2a3b5c40"
down_revision = "b71c04e5aa10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reconciliation", sa.Column("external_flow", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("reconciliation", "external_flow")
