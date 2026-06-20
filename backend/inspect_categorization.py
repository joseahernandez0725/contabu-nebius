"""
Inspección rápida de cómo quedó categorizada la data — para revisar la CALIDAD de la IA.

Imprime una tabla legible (fecha · monto · descripción · account_type · category · subcategory ·
confianza · fuente) y un resumen por sección. Solo lectura.

Uso contra Cloud SQL (vía Cloud SQL Auth Proxy en el puerto 5433):
    ./cloud-sql-proxy --port 5433 contabu-prod:us-central1:contabu-db &
    DATABASE_URL='postgresql+psycopg://contabu:TU_PASSWORD@127.0.0.1:5433/contabu' \
        .venv/bin/python inspect_categorization.py
"""

from collections import Counter

from sqlmodel import Session, select

from db import engine
from models import Transaction


def main() -> None:
    with Session(engine) as session:
        rows = session.exec(select(Transaction).order_by(Transaction.date)).all()

    print(f"{len(rows)} transacciones\n")
    print(f"{'date':10} | {'amount':>10} | {'description':38} | {'account_type':17} | "
          f"{'category':16} | {'subcategory':16} | conf | src")
    print("-" * 130)
    by_section: Counter = Counter()
    low_conf = 0
    for tx in rows:
        at = tx.account_type.value if tx.account_type else "—"
        by_section[at] += 1
        if tx.confidence is not None and tx.confidence < 0.9:
            low_conf += 1
        print(
            f"{str(tx.date):10} | {float(tx.amount):>10,.2f} | {(tx.description or '')[:38]:38} | "
            f"{at:17} | {(tx.category or '—')[:16]:16} | {(tx.subcategory or '—')[:16]:16} | "
            f"{'' if tx.confidence is None else f'{tx.confidence:.2f}'} | {tx.source or '—'}"
        )

    print("\nResumen por sección:")
    for section, n in by_section.most_common():
        print(f"  {section:18} {n}")
    print(f"\nConfianza < 0.90: {low_conf} de {len(rows)}")


if __name__ == "__main__":
    main()
