"""Store extracted manual visual assets."""
from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("manual_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("manual_versions.id"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("asset_index", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("ocr_text", sa.Text(), nullable=False),
        sa.Column("visual_description", sa.Text(), nullable=True),
        sa.Column("processing_status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.UniqueConstraint("version_id", "page_number", "asset_index", "sha256"))
    op.create_index("ix_manual_assets_version_id", "manual_assets", ["version_id"])


def downgrade():
    op.drop_index("ix_manual_assets_version_id", table_name="manual_assets")
    op.drop_table("manual_assets")
