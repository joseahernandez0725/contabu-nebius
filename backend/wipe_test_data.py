"""Limpia la data de PRUEBA de ContaBu para re-procesar con el motor nuevo.

Borra TODAS las transacciones y las reglas de empresa (COMPANY) aprendidas, para que al re-subir
el CSV la categorización salga "fresca" con el motor actual. NO toca usuarios, empresas ni reglas
de SECTOR (baseline). Muestra los conteos y EXIGE confirmación explícita (--yes).

Uso (vía Cloud SQL Auth Proxy):
    ./cloud-sql-proxy --port 5433 contabu-prod:us-central1:contabu-db &
    DATABASE_URL='postgresql+psycopg://contabu:***@127.0.0.1:5433/contabu' \
        .venv/bin/python wipe_test_data.py          # solo muestra qué borraría (dry-run)
    DATABASE_URL='...' .venv/bin/python wipe_test_data.py --yes   # borra de verdad
"""

import sys

from sqlmodel import Session, delete, select

from db import engine
from models import CategoryRule, RuleScope, Transaction


def main() -> None:
    confirm = "--yes" in sys.argv
    with Session(engine) as session:
        n_tx = len(session.exec(select(Transaction)).all())
        company_rules = session.exec(
            select(CategoryRule).where(CategoryRule.scope == RuleScope.COMPANY)
        ).all()
        n_rules = len(company_rules)

        print(f"Transacciones a borrar:        {n_tx}")
        print(f"Reglas de EMPRESA a borrar:    {n_rules}  (las de SECTOR se conservan)")

        if not confirm:
            print("\n[DRY-RUN] No se borró nada. Re-ejecuta con --yes para borrar de verdad.")
            return

        session.exec(delete(Transaction))
        session.exec(
            delete(CategoryRule).where(CategoryRule.scope == RuleScope.COMPANY)
        )
        session.commit()
        print("\n✅ Borrado. Ahora re-sube el CSV por la app para ver la categorización nueva.")


if __name__ == "__main__":
    main()
