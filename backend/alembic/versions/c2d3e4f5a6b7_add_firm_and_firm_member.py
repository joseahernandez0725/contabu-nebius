"""add firm, firmmember, company.firm_id and Role.CLIENT (FASE 13 — la oficina)

El nivel que faltaba: hasta aquí la jerarquía era User → Company. Con `Firm` un bookkeeper se invita
UNA vez a la oficina y entra a TODOS sus libros (antes: una invitación por libro).

Hace CUATRO cosas, y la de datos es la que importa:
  1. crea el enum `firmrole` y las tablas `firm` / `firmmember`
  2. agrega `company.firm_id`
  3. agrega el valor `CLIENT` al enum `role` que ya existe
  4. BACKFILL: por cada usuario con empresas propias → crea su firma, lo mete como FirmMember OWNER
     y apunta sus empresas a esa firma. **Sin este paso, al desplegar nadie ve ningún libro.**

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-09-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1) El enum de roles de OFICINA es nuevo → se crea aquí (create_type por defecto = True).
    firmrole = sa.Enum("OWNER", "BOOKKEEPER", "VIEWER", name="firmrole")

    op.create_table(
        "firm",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        # El SELLO de la firma: es lo que ve el cliente en su plataforma y lo que va en el
        # encabezado del PDF/Excel de la FASE 12. NULL → se cae al `name`.
        sa.Column("brand_name", sa.String(), nullable=True),
        sa.Column("brand_logo_url", sa.String(), nullable=True),
        sa.Column("brand_color", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_firm_owner_user_id", "firm", ["owner_user_id"])

    op.create_table(
        "firmmember",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("firm_id", sa.Integer(), sa.ForeignKey("firm.id"), nullable=False),
        # NULL mientras la invitación está PENDIENTE (solo se conoce el email).
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("role", firmrole, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_firmmember_firm_id", "firmmember", ["firm_id"])
    op.create_index("ix_firmmember_user_id", "firmmember", ["user_id"])
    op.create_index("ix_firmmember_email", "firmmember", ["email"])

    # --- 2) A qué oficina pertenece cada libro. Nullable: el backfill de abajo lo llena.
    op.add_column("company", sa.Column("firm_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_company_firm_id", "company", "firm", ["firm_id"], ["id"])
    op.create_index("ix_company_firm_id", "company", ["firm_id"])

    # --- 3) El rol CLIENT sobre una empresa (el dueño del negocio en su plataforma).
    # `ADD VALUE` dentro de una transacción es válido desde PostgreSQL 12 (aquí corre 18.6); el valor
    # nuevo no se puede USAR en la misma transacción, y el backfill de abajo no lo usa.
    op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'CLIENT'")

    # --- 4) BACKFILL — el paso que evita que el despliegue deje a todos sin libros.
    # Por cada usuario que HOY tiene empresas propias se crea su oficina. `brand_name` se siembra en
    # 'ContaBu' a propósito: los únicos usuarios que existen en esta base son Jose y sus pruebas, y su
    # compañía ES ContaBu — es lo que sus clientes deben ver (decisión del 27-sep-2026). Las firmas
    # que se creen DESPUÉS (al registrarse) nacen con brand_name NULL → se caen a su propio nombre.
    bind = op.get_bind()
    owners = bind.execute(sa.text("""
        SELECT DISTINCT u.id, u.email
        FROM "user" u
        JOIN company c ON c.user_id = u.id
    """)).fetchall()

    for user_id, email in owners:
        email = (email or "").strip()
        # El nombre de la firma es presentable; `firmmember.email` NO: existe para ENLAZAR
        # invitaciones por correo (`auth.current_user`). Un usuario sin email (un uid de desarrollo)
        # deja el campo VACÍO — meterle "Firm 3" ahí crearía un email que nunca va a coincidir.
        firm_name = email or f"Firm {user_id}"
        firm_id = bind.execute(
            sa.text("""
                INSERT INTO firm (name, owner_user_id, brand_name, created_at)
                VALUES (:name, :uid, 'ContaBu', now())
                RETURNING id
            """),
            {"name": firm_name, "uid": user_id},
        ).scalar()
        bind.execute(
            sa.text("""
                INSERT INTO firmmember (firm_id, user_id, email, role, created_at)
                VALUES (:fid, :uid, :email, 'OWNER', now())
            """),
            {"fid": firm_id, "uid": user_id, "email": email},
        )
        bind.execute(
            sa.text("UPDATE company SET firm_id = :fid WHERE user_id = :uid AND firm_id IS NULL"),
            {"fid": firm_id, "uid": user_id},
        )


def downgrade() -> None:
    op.drop_index("ix_company_firm_id", table_name="company")
    op.drop_constraint("fk_company_firm_id", "company", type_="foreignkey")
    op.drop_column("company", "firm_id")

    op.drop_index("ix_firmmember_email", table_name="firmmember")
    op.drop_index("ix_firmmember_user_id", table_name="firmmember")
    op.drop_index("ix_firmmember_firm_id", table_name="firmmember")
    op.drop_table("firmmember")

    op.drop_index("ix_firm_owner_user_id", table_name="firm")
    op.drop_table("firm")

    sa.Enum(name="firmrole").drop(op.get_bind(), checkfirst=True)
    # NOTA: 'CLIENT' NO se quita del enum `role`. PostgreSQL no soporta borrar un valor de enum; la
    # única vía es recrear el tipo y reescribir cada columna que lo usa. Un valor sobrante es inocuo.
