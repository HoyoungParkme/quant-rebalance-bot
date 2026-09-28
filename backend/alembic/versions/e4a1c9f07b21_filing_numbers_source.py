"""공시에 "숫자를 가져온 공시" 칸 추가

Revision ID: e4a1c9f07b21
Revises: d18f2a3b5c40
Create Date: 2026-09-28

전자공시 재무 API는 정정이 있으면 정정본의 숫자만 준다. 원본 공시는 원본 접수일에 넣되 숫자는 정정본에서
가져온다(운영자 결정 2026-09-28). 어느 공시의 숫자인지 남겨야 나중에 미래 정보가 섞인 행을 가려낼 수 있다.
NULL이면 그 공시 자신의 숫자다.
"""

import sqlalchemy as sa
from alembic import op

revision = "e4a1c9f07b21"
down_revision = "d18f2a3b5c40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("filing", sa.Column("numbers_rcept_no", sa.String(length=14), nullable=True))


def downgrade() -> None:
    op.drop_column("filing", "numbers_rcept_no")
