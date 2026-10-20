"""el plan de cuentas POR LIBRO, con la línea del IRS, y cada libro dice CÓMO TRIBUTA (Movimientos)

Decisiones 🧾 y 🏛️ del tablero (Jose, 18-oct-2026):

- `company.tax_form`: "schedule_c" (por defecto) o "1120s".
- `companycategory` deja de ser "las categorías propias" y pasa a ser EL PLAN de cada libro: +`irs_line`
  (con su formulario: "SchC:9", "1120S:20", "K:16d"…), +`archived_at`, +`position`, +`default_key`
  (el nombre con que vino del plan por defecto), y un nombre no se repite dentro del libro.

Datos (lo ya guardado, en CADA tabla que nombra una categoría):
- TODOS los libros quedan en Schedule C con SU copia del plan por defecto (+ sus categorías propias), y
  sus nombres viejos pasan a los oficiales (`categories.schedule_c_renames`, por sector). Un libro que
  tribute como S corp se pasa a 1120-S DESDE AJUSTES (cambiar el formulario y fusionar cada cuenta en uso
  con su nueva): ningún dato de un cliente vive en el código.
- Una fila cambia de sección solo si estaba en la sección de su categoría vieja (así se mueve, p. ej.,
  Taxes & Licenses de Otros gastos a Gastos de operación). Una fila que ya estaba en otra sección (un
  error, a veces plantado a propósito en el demo) conserva la suya.
- Una categoría propia que solo difiere en mayúsculas de una del plan nuevo se fusiona con esa.
- Toda categoría en uso que no esté en el plan queda en él como propia: ningún movimiento ni regla
  apunta a una cuenta que el plan no tenga.
- Se renombran: transaction (+ ai_original_*), categoryrule (de libro y de sector), categorizationchange
  (antes y después: la historia usa las etiquetas del plan), proposaldismissal y el payload de finding.
  NO se tocan los P&L CERRADOS (`pnlreport.snapshot`): son documentos congelados.
- Comprueba con conteos antes/después que ninguna fila pierde su categoría (si no, se detiene).

Downgrade: quita las columnas y las filas del plan por defecto; los NOMBRES no vuelven (dos viejos que se
fusionaron no se pueden separar). Para volver de verdad: el respaldo de `pg_dump`.

Revision ID: a1f0c2d4e6b8
Revises: 9e0f1a2b3c4d
Create Date: 2026-10-18

"""
import json
from collections import Counter, defaultdict
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

# Los planes NUEVOS salen del código de hoy (`categories.py`, mismo commit que esta migración).
from categories import (DEFAULT_TAX_FORM, default_plan, schedule_c_renames,  # noqa: E402
                        suggested_line)

revision: str = "a1f0c2d4e6b8"
down_revision: Union[str, Sequence[str], None] = "9e0f1a2b3c4d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# El plan VIEJO (copiado de `categories.py` antes del 18-oct), congelado: solo para saber la sección de
# cada nombre viejo.
_OLD_COMMON = {
    "OPERATING_EXPENSE": ["Office Expense", "Software", "Bank Fees", "Legal & Professional", "Meals",
                          "Dues & Subscriptions", "Telephone & Internet", "Travel"],
    "OTHER_INCOME": ["Interest Income", "Refunds & Rebates"],
    "OTHER_EXPENSE": ["Interest Expense", "Taxes & Licenses"],
    "NON_PL": ["Owner Draw", "Owner Contribution", "Transfer", "Loan Proceeds", "Loan Principal Payment",
               "Credit Card Payment"],
}
_OLD_SECTOR = {
    "construction": {"REVENUE": ["Construction Revenue"],
                     "COGS": ["Materials", "Subcontractors", "Direct Labor", "Equipment Rental", "Small Tools & Equipment",
                              "Permits", "Equipment Fuel", "Trucking & Hauling", "Dumping & Disposal"],
                     "OPERATING_EXPENSE": ["Office Rent", "Insurance", "Marketing", "Admin Wages", "Vehicle Fuel",
                                           "Vehicle Expenses", "Utilities", "Repairs & Maintenance"]},
    "cleaning": {"REVENUE": ["Cleaning Revenue"],
                 "COGS": ["Cleaning Supplies", "Direct Labor", "Subcontractors", "Transportation", "Small Tools & Equipment"],
                 "OPERATING_EXPENSE": ["Insurance", "Marketing", "Equipment", "Admin Wages", "Uniforms", "Vehicle Fuel",
                                       "Utilities"]},
    "restaurant": {"REVENUE": ["Food & Beverage Sales"],
                   "COGS": ["Food Purchases", "Beverages", "Beer/Wine/Liquor", "Paper & Packaging"],
                   "OPERATING_EXPENSE": ["Rent", "Utilities", "Contract Cleaning", "Linens", "Smallwares",
                                         "Repairs & Maintenance", "Marketing", "Front-of-House Wages", "Kitchen Wages"]},
    "landscaping": {"REVENUE": ["Landscaping Revenue"],
                    "COGS": ["Plants & Materials", "Subcontractors", "Direct Labor", "Equipment Rental",
                             "Small Tools & Equipment", "Equipment Fuel", "Dumping & Disposal"],
                    "OPERATING_EXPENSE": ["Insurance", "Marketing", "Admin Wages", "Vehicle Fuel", "Vehicle Expenses",
                                          "Repairs & Maintenance", "Uniforms"]},
    "painting": {"REVENUE": ["Painting Revenue"],
                 "COGS": ["Paint & Materials", "Subcontractors", "Direct Labor", "Equipment Rental",
                          "Small Tools & Equipment", "Permits"],
                 "OPERATING_EXPENSE": ["Insurance", "Marketing", "Admin Wages", "Vehicle Fuel", "Vehicle Expenses",
                                       "Utilities", "Repairs & Maintenance"]},
    "trucking": {"REVENUE": ["Freight Revenue"],
                 "COGS": ["Fuel", "Truck Repairs & Maintenance", "Tires", "Tolls & Scales", "Truck Lease", "Driver Pay",
                          "Dispatch & Broker Fees"],
                 "OPERATING_EXPENSE": ["Insurance", "Parking & Storage", "Marketing", "Admin Wages"]},
    "beauty": {"REVENUE": ["Service Revenue", "Product Sales"],
               "COGS": ["Products & Supplies", "Contract Stylists", "Direct Labor"],
               "OPERATING_EXPENSE": ["Salon Rent", "Utilities", "Insurance", "Marketing", "Equipment",
                                     "Uniforms & Laundry", "Admin Wages"]},
    "auto_repair": {"REVENUE": ["Repair Revenue", "Parts Sales"],
                    "COGS": ["Parts", "Shop Supplies", "Direct Labor", "Subcontractors", "Small Tools & Equipment"],
                    "OPERATING_EXPENSE": ["Shop Rent", "Utilities", "Insurance", "Marketing", "Equipment", "Uniforms",
                                          "Admin Wages", "Vehicle Fuel"]},
    "retail": {"REVENUE": ["Sales Revenue"],
               "COGS": ["Merchandise Purchases", "Freight-In", "Packaging & Bags"],
               "OPERATING_EXPENSE": ["Store Rent", "Utilities", "Insurance", "Marketing", "Wages", "Equipment"]},
}
_OLD_GENERIC = {"REVENUE": ["Sales Revenue", "Service Revenue"],
                "COGS": ["Materials", "Direct Labor", "Subcontractors", "Small Tools & Equipment"],
                "OPERATING_EXPENSE": ["Rent", "Insurance", "Marketing", "Wages", "Utilities", "Vehicle Fuel",
                                      "Repairs & Maintenance"]}


def _old_home(sector: str) -> dict[str, str]:
    """Nombre viejo → su sección en el plan viejo del sector."""
    base = _OLD_SECTOR.get((sector or "").strip().lower(), _OLD_GENERIC)
    out: dict[str, str] = {}
    for src in (base, _OLD_COMMON):
        for sec, names in src.items():
            for n in names:
                out.setdefault(n, sec)
    return out


def _map_payload(obj, rename: dict[str, tuple[str, Optional[str], Optional[str]]]):
    """Renombra, dentro del payload de un hallazgo, los textos "Nombre" o "SECCIÓN/Nombre"."""
    if isinstance(obj, dict):
        return {k: _map_payload(v, rename) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_map_payload(v, rename) for v in obj]
    if isinstance(obj, str):
        if obj in rename:
            return rename[obj][0]
        if "/" in obj:
            sec, _, name = obj.partition("/")
            if name in rename:
                new, old_home, new_home = rename[name]
                return f"{new_home if sec == old_home and new_home else sec}/{new}"
    return obj


def upgrade() -> None:
    op.add_column("company", sa.Column("tax_form", sa.String(), nullable=False, server_default=DEFAULT_TAX_FORM))
    op.add_column("companycategory", sa.Column("irs_line", sa.String(), nullable=True))
    op.add_column("companycategory", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.add_column("companycategory", sa.Column("position", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("companycategory", sa.Column("default_key", sa.String(), nullable=True))

    bind = op.get_bind()
    q = lambda sql, **kw: bind.execute(sa.text(sql), kw)  # noqa: E731

    before_tx = q("select count(*) from transaction where category is not null").scalar()
    before_rules = q("select count(*) from categoryrule where category is not null").scalar()
    before_changes = q("select count(*) from categorizationchange where category_after is not null").scalar()

    companies = q("select id, sector from company order by id").all()
    for cid, sector in companies:
        form = DEFAULT_TAX_FORM
        old_home = _old_home(sector)
        customs = q("select id, name, account_type from companycategory where company_id = :c", c=cid).all()
        for _id, n, at in customs:
            old_home.setdefault(n, at)
        plan = default_plan(sector or "", form)
        new_home = {n: sec for sec, rows in plan.items() for n, _ in rows}
        by_lower = {n.lower(): n for n in new_home}

        rename = schedule_c_renames(sector or "")
        # Una propia que solo difiere en mayúsculas de una del plan nuevo ("advertising") → esa.
        for _id, n, _at in customs:
            target = rename.get(n, n)
            if target not in new_home and target.lower() in by_lower:
                rename[n] = by_lower[target.lower()]
        rename = {o: n for o, n in rename.items() if o != n}
        moves = {o: (n, old_home.get(o), new_home.get(n)) for o, n in rename.items()}

        def _section(old: str, current: Optional[str]) -> Optional[str]:
            _n, oh, nh = moves[old]
            return nh if (current is not None and current == oh and nh) else current

        # transaction
        for tid, at, cat in q("select id, account_type, category from transaction "
                              "where company_id = :c and category is not null", c=cid).all():
            if cat in moves:
                q("update transaction set category = :n, account_type = cast(:a as accounttype) where id = :i",
                  n=moves[cat][0], a=_section(cat, at), i=tid)
        for tid, at, cat in q("select id, ai_original_account_type, ai_original_category from transaction "
                              "where company_id = :c and ai_original_category is not null", c=cid).all():
            if cat in moves:
                q("update transaction set ai_original_category = :n, ai_original_account_type = :a where id = :i",
                  n=moves[cat][0], a=_section(cat, at), i=tid)
        # categoryrule (de libro)
        for rid, at, cat in q("select id, account_type, category from categoryrule where company_id = :c", c=cid).all():
            if cat in moves:
                q("update categoryrule set category = :n, account_type = cast(:a as accounttype) where id = :i",
                  n=moves[cat][0], a=_section(cat, at), i=rid)
        # categorizationchange: antes y después
        for side in ("before", "after"):
            for hid, at, cat in q(f"select id, account_type_{side}, category_{side} from categorizationchange "
                                  f"where company_id = :c and category_{side} is not null", c=cid).all():
                if cat in moves:
                    q(f"update categorizationchange set category_{side} = :n, account_type_{side} = :a where id = :i",
                      n=moves[cat][0], a=_section(cat, at), i=hid)
        # proposaldismissal
        for pid, at, cat in q("select id, account_type, category from proposaldismissal "
                              "where company_id = :c and category is not null", c=cid).all():
            if cat in moves:
                q("update proposaldismissal set category = :n, account_type = :a where id = :i",
                  n=moves[cat][0], a=_section(cat, at), i=pid)
        # finding.payload (los textos "SECCIÓN/Nombre" y "suggested_category")
        for fid, payload in q("select id, payload from finding where company_id = :c and payload is not null",
                              c=cid).all():
            data = payload if isinstance(payload, (dict, list)) else json.loads(payload)
            mapped = _map_payload(data, moves)
            if mapped != data:
                q("update finding set payload = cast(:p as json) where id = :i", p=json.dumps(mapped), i=fid)

        # EL PLAN DEL LIBRO: sus propias (las que se fusionaron salen) + el plan por defecto + lo que esté
        # en uso y el plan no tenga.
        for _id, n, at in customs:
            if n in moves or n in new_home:
                q("delete from companycategory where id = :i", i=_id)
        kept = q("select id, account_type from companycategory where company_id = :c order by id", c=cid).all()
        have = {r[0] for r in q("select lower(name) from companycategory where company_id = :c", c=cid).all()}
        pos: Counter = Counter()
        for sec, rows in plan.items():
            for name, line in rows:
                if name.lower() in have:
                    continue
                q("insert into companycategory (company_id, account_type, name, created_at, irs_line, position, default_key) "
                  "values (:c, cast(:a as accounttype), :n, now(), :l, :p, :n)", c=cid, a=sec, n=name, l=line, p=pos[sec])
                pos[sec] += 1
                have.add(name.lower())
        for _id, at in kept:
            q("update companycategory set irs_line = :l, position = :p where id = :i",
              l=suggested_line(form, at), p=pos[at], i=_id)
            pos[at] += 1
        in_plan = {r[0] for r in q("select name from companycategory where company_id = :c", c=cid).all()}
        orphan_sec: dict[str, Counter] = defaultdict(Counter)
        for at, cat in q("select account_type, category from transaction where company_id = :c and category is not null "
                         "union all select account_type, category from categoryrule where company_id = :c", c=cid).all():
            if cat not in in_plan and at is not None:
                orphan_sec[cat][at] += 1
        for cat, secs in orphan_sec.items():
            at = secs.most_common(1)[0][0]
            q("insert into companycategory (company_id, account_type, name, created_at, irs_line, position) "
              "values (:c, cast(:a as accounttype), :n, now(), :l, :p)", c=cid, a=at, n=cat, l=suggested_line(form, at), p=pos[at])
            pos[at] += 1
            print(f"  [a1f0c2d4e6b8] libro {cid}: «{cat}» estaba en uso fuera del plan → queda como propia ({at})")

    # Las reglas de SECTOR (apagadas desde el 26-jun; se renombran para cuando vuelvan).
    for rid, sec_name, at, cat in q("select id, sector, account_type, category from categoryrule "
                                    "where scope = 'SECTOR'").all():
        ren = schedule_c_renames(sec_name or "")
        if cat in ren:
            oh = _old_home(sec_name).get(cat)
            nh = {n: s for s, rows in default_plan(sec_name or "", DEFAULT_TAX_FORM).items() for n, _ in rows}.get(ren[cat])
            q("update categoryrule set category = :n, account_type = cast(:a as accounttype) where id = :i",
              n=ren[cat], a=(nh if at == oh and nh else at), i=rid)

    after_tx = q("select count(*) from transaction where category is not null").scalar()
    after_rules = q("select count(*) from categoryrule where category is not null").scalar()
    after_changes = q("select count(*) from categorizationchange where category_after is not null").scalar()
    print(f"  [a1f0c2d4e6b8] con categoría · movimientos {before_tx} → {after_tx} · reglas {before_rules} → "
          f"{after_rules} · historial {before_changes} → {after_changes}")
    if (before_tx, before_rules, before_changes) != (after_tx, after_rules, after_changes):
        raise RuntimeError("a1f0c2d4e6b8: una fila perdió su categoría; se deshace la migración")

    op.create_unique_constraint("uq_companycategory_company_name", "companycategory", ["company_id", "name"])


def downgrade() -> None:
    op.drop_constraint("uq_companycategory_company_name", "companycategory", type_="unique")
    op.execute("delete from companycategory where default_key is not null")
    op.drop_column("companycategory", "default_key")
    op.drop_column("companycategory", "position")
    op.drop_column("companycategory", "archived_at")
    op.drop_column("companycategory", "irs_line")
    op.drop_column("company", "tax_form")
