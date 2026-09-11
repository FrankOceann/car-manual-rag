"""Users, versioned manuals and durable import jobs."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False))
    op.create_table("manuals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("vehicle_id", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("active_version_id", sa.String(36), nullable=True),
        sa.UniqueConstraint("vehicle_id", "title"))
    op.create_index("ix_manuals_vehicle_id", "manuals", ["vehicle_id"])
    op.create_table("manual_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("manual_id", sa.String(36), sa.ForeignKey("manuals.id"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("processing_config", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("manual_id", "sha256"))
    op.create_index("ix_manual_versions_manual_id", "manual_versions", ["manual_id"])
    op.create_table("import_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("manual_versions.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_import_jobs_status", "import_jobs", ["status"])
    op.create_index("ix_import_jobs_updated_at", "import_jobs", ["updated_at"])


def downgrade():
    op.drop_table("import_jobs")
    op.drop_table("manual_versions")
    op.drop_table("manuals")
    op.drop_table("users")
