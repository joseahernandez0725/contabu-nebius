"""
FASE 11 — la prueba del VALIDADOR de la fórmula del libro (y de que nada ejecuta texto).

Lo que comprueba, contra un libro del demo sembrado en una transacción que se DESHACE al final:
1. Las filas válidas pasan (incluida la de las vacaciones, con ventana de fechas).
2. Las filas peligrosas o mal hechas se RECHAZAN, todas: campo extra, operador desconocido,
   categoría fuera del plan o de otra sección, texto con forma de código, marcador de persona sin
   resolver, ventana al revés, palabra clave genérica o muerta, evidencia inventada.
3. En el código del backend no hay `eval(` ni `exec(` (salvo esta misma prueba, que los busca).

Corre así:
    .venv/bin/python check_formula.py
Salida: un renglón por caso y **código de salida 1** si algo falló.
"""

import pathlib
import re
import sys

from sqlmodel import Session

import demo_data
import formula
from db import engine
from models import Firm, User

VALID = [
    ("la fila de las vacaciones (ventana de fechas)", {
        "keyword": "STARBUCKS", "valid_from": "2026-08-03", "valid_to": "2026-08-17",
        "account_type": "NON_PL", "category": "Owner Draw", "subcategory": "Starbucks",
        "explanation": "El dueño estuvo de vacaciones en Europa del 3 al 17-ago (nota del bookkeeper)."}),
    ("comercio con signos reales (LOWE'S) y prioridad", {
        "keyword": "LOWE'S", "priority": 3, "account_type": "COGS", "category": "Materials"}),
    ("comodín del motor", {
        "keyword": "THE HOME DEPOT*ANTIOCH", "account_type": "COGS", "category": "Materials"}),
]

INVALID = [
    ("campo que no existe (monto, todavía no lo entiende el motor)",
     {"keyword": "STARBUCKS", "account_type": "OPERATING_EXPENSE", "category": "Meals", "amount_min": 50},
     "unknown_field:amount_min"),
    ("operador inventado",
     {"keyword": "STARBUCKS", "operator": "regex", "account_type": "OPERATING_EXPENSE", "category": "Meals"},
     "unknown_field:operator"),
    ("código en la palabra clave",
     {"keyword": "__import__('os').system('x')", "account_type": "OPERATING_EXPENSE", "category": "Meals"},
     "keyword_bad_chars"),
    ("código en la palabra clave (sin paréntesis)",
     {"keyword": "STARBUCKS; DROP TABLE", "account_type": "OPERATING_EXPENSE", "category": "Meals"},
     "keyword_bad_chars"),
    ("marcador de persona sin resolver",
     {"keyword": "PERSON_1", "account_type": "COGS", "category": "Subcontractors"},
     "keyword_bad_chars"),
    ("categoría que no existe en el plan",
     {"keyword": "STARBUCKS", "account_type": "OPERATING_EXPENSE", "category": "Bitcoin Mining"},
     "category_not_in_chart"),
    ("categoría de OTRA sección",
     {"keyword": "STARBUCKS", "account_type": "REVENUE", "category": "Meals"},
     "category_wrong_section"),
    ("sección que no existe",
     {"keyword": "STARBUCKS", "account_type": "ASSETS", "category": "Meals"},
     "bad_value:account_type"),
    ("ventana al revés",
     {"keyword": "STARBUCKS", "valid_from": "2026-08-17", "valid_to": "2026-08-03",
      "account_type": "NON_PL", "category": "Owner Draw"},
     "bad_window"),
    ("fecha que no es fecha",
     {"keyword": "STARBUCKS", "valid_from": "next week", "account_type": "NON_PL", "category": "Owner Draw"},
     "bad_value:valid_from"),
    ("palabra clave genérica (solo ruido del banco)",
     {"keyword": "ONLINE PAYMENT", "account_type": "OPERATING_EXPENSE", "category": "Meals"},
     "keyword_too_generic"),
    ("palabra clave solo números",
     {"keyword": "4471", "account_type": "OPERATING_EXPENSE", "category": "Meals"},
     "keyword_too_generic"),
    ("palabra clave muerta (no caza nada en el libro)",
     {"keyword": "NONEXISTENT MERCHANT", "account_type": "OPERATING_EXPENSE", "category": "Meals"},
     "matches_nothing"),
    ("ventana donde no hay movimientos",
     {"keyword": "STARBUCKS", "valid_from": "2025-01-01", "valid_to": "2025-01-31",
      "account_type": "NON_PL", "category": "Owner Draw"},
     "matches_nothing"),
    ("duplica una regla que ya existe",
     {"keyword": "STARBUCKS", "account_type": "OPERATING_EXPENSE", "category": "Meals"},
     "duplicate_rule"),
    ("evidencia inventada (nota que no existe)",
     {"keyword": "LOWE'S", "account_type": "COGS", "category": "Materials", "from_notes": [999999]},
     "unknown_note"),
    ("prioridad fuera de rango",
     {"keyword": "LOWE'S", "priority": 0, "account_type": "COGS", "category": "Materials"},
     "bad_value:priority"),
    ("no es un objeto",
     "STARBUCKS -> Owner Draw",
     "not_an_object"),
]


def check_no_eval() -> list[str]:
    """Ningún archivo del backend llama a eval( o exec( (la regla del motor que interpreta)."""
    here = pathlib.Path(__file__).resolve()
    hits = []
    pattern = re.compile(r"(?<![\w.])(eval|exec)\s*\(")
    for p in here.parent.rglob("*.py"):
        if ".venv" in p.parts or "alembic" in p.parts or p == here:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if pattern.search(code):
                hits.append(f"{p.name}:{n}: {line.strip()}")
    return hits


def main() -> int:
    fails: list[str] = []
    with Session(engine) as s:
        try:
            owner = User(firebase_uid="__check_formula__", email="check-formula@test.local")
            s.add(owner); s.flush()
            firm = Firm(name="CHECK FORMULA", owner_user_id=owner.id)
            s.add(firm); s.flush()
            books = demo_data.seed_demo_books(s, firm, owner)
            rivera = next(b for b in books if b.name == "Rivera HVAC LLC")
            ctx = formula.BookContext(s, rivera)

            print("  --- filas válidas: deben pasar ---")
            for name, raw in VALID:
                c = formula.check_row(raw, ctx)
                if c.ok:
                    print(f"  ✅ {name}  (caza {c.matches} movimientos)")
                else:
                    fails.append(f"debía pasar: {name} → {c.errors}")
                    print(f"  ❌ {name} → {c.errors}")

            print("  --- filas inválidas: deben rechazarse ---")
            for name, raw, code in INVALID:
                c = formula.check_row(raw, ctx)
                if not c.ok and code in c.errors:
                    print(f"  ✅ {name}  → {code}")
                else:
                    fails.append(f"debía rechazarse con {code}: {name} → ok={c.ok} {c.errors}")
                    print(f"  ❌ {name} → ok={c.ok} {c.errors} (esperaba {code})")

            lst = formula.check_rows({"keyword": "x"}, ctx)
            if lst[0].errors != ["not_a_list"]:
                fails.append("una respuesta que no es lista debía rechazarse")
        finally:
            s.rollback()   # no deja nada en la base

    print("  --- nada ejecuta texto ---")
    hits = check_no_eval()
    if hits:
        fails.extend(f"eval/exec encontrado: {h}" for h in hits)
        for h in hits:
            print(f"  ❌ {h}")
    else:
        print("  ✅ 0 llamadas a eval( / exec( en el backend")

    total = len(VALID) + len(INVALID) + 2
    print(f"\n  {total} casos · {len(fails)} fallos")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
