"""Keep version allocation independent from deletable resume snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261111_0017"
down_revision: str | Sequence[str] | None = "20261110_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "student_profiles",
        sa.Column("resume_version_seq", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE student_profiles p LEFT JOIN "
        "(SELECT student_profile_id, MAX(version_no) AS allocated FROM resumes "
        "GROUP BY student_profile_id) r ON r.student_profile_id = p.id "
        "SET p.resume_version_seq = COALESCE(r.allocated, 0)"
    )


def downgrade() -> None:
    op.drop_column("student_profiles", "resume_version_seq")
