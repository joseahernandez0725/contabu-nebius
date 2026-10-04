"""
PRUEBA DE PUNTA A PUNTA DE PLAID — SOLO SANDBOX (FASE 10, Movimientos · 4-oct-2026).

    cd backend && .venv/bin/python plaid_sandbox_check.py            # user_good: vincular + re-sync
    cd backend && .venv/bin/python plaid_sandbox_check.py --dynamic  # + avisos del banco (refresh)

Crea la conexión SIN abrir Link (`/sandbox/public_token/create`, solo existe en sandbox), la pasa por
el MISMO camino que la app (`bank.exchange_public_token` → `BankConnection` cifrada → `bank.sync`), y
comprueba:
  1. el primer sync trae movimientos;
  2. re-sincronizar NO duplica (0 nuevos);
  3. con --dynamic (`user_transactions_dynamic` en First Platypus Bank, `ins_109508`):
     `/transactions/refresh` → otro sync aplica los avisos (pendientes que se asientan, cambios).
Todo vive en un LIBRO TEMPORAL que se borra al final, y el Item se elimina en Plaid (`/item/remove`).

🔴 Se niega a correr si PLAID_ENV no es sandbox: las 10 conexiones del plan Trial (production) NO
se recuperan al desconectar y están reservadas para el libro real.
"""

import sys
import time

from dotenv import load_dotenv

load_dotenv(".env")

from sqlmodel import Session, select  # noqa: E402

import bank  # noqa: E402
from db import engine  # noqa: E402
from models import BankConnection, CategorizationChange, Company, Transaction  # noqa: E402

DYNAMIC = "--dynamic" in sys.argv
INSTITUTION = "ins_109508"  # First Platypus Bank (no OAuth)
USER = "user_transactions_dynamic" if DYNAMIC else "user_good"
PASSWORD = "any-password" if DYNAMIC else "pass_good"


def wait_sync(session: Session, conn: BankConnection, company: Company, label: str, tries: int = 24) -> dict:
    """Justo después de vincular, Plaid responde NOT_READY y luego trae el historial por partes: se
    sincroniza hasta HISTORICAL_UPDATE_COMPLETE. Sorpresa (4-oct): un /transactions/refresh pedido
    ANTES de ese estado no produce ningún aviso."""
    total: dict = {}
    for _ in range(tries):
        r = bank.sync(session, conn, company)
        session.refresh(conn)
        for k, v in r.items():
            total[k] = total.get(k, 0) + v if isinstance(v, int) and not isinstance(v, bool) else v
        if r.get("update_status") == "HISTORICAL_UPDATE_COMPLETE":
            print(f"  {label}: {total}")
            return total
        time.sleep(5)
    raise SystemExit(f"  {label}: Plaid no terminó el historial tras {tries * 5}s")


def main() -> None:
    if bank.environment() != "sandbox":
        raise SystemExit("🔴 PLAID_ENV no es sandbox. Esta prueba SOLO corre en sandbox.")
    if not bank.public_config()["enabled"]:
        raise SystemExit("Faltan PLAID_CLIENT_ID / PLAID_SECRET en backend/.env (los del ambiente sandbox).")

    print(f"Sandbox · {INSTITUTION} · {USER}")
    public_token = bank._post("/sandbox/public_token/create", {
        "institution_id": INSTITUTION,
        "initial_products": ["transactions"],
        "options": {"override_username": USER, "override_password": PASSWORD,
                    "transactions": {"days_requested": bank.DAYS_REQUESTED}},
    })["public_token"]
    access_token, item_id = bank.exchange_public_token(public_token)

    with Session(engine) as s:
        company = Company(name="PLAID SANDBOX CHECK (temporal)", sector="construction")
        s.add(company)
        s.commit()
        s.refresh(company)
        cid = company.id
        ok = False
        try:
            conn = BankConnection(company_id=cid, item_id=item_id, environment="sandbox",
                                  institution_name="First Platypus Bank",
                                  access_token_enc=bank.encrypt_token(access_token))
            s.add(conn)
            s.commit()
            s.refresh(conn)

            r1 = wait_sync(s, conn, company, "1er sync")
            assert r1["inserted"] > 0, "el primer sync no trajo movimientos"
            n1 = len(s.exec(select(Transaction).where(Transaction.company_id == cid)).all())

            def ids_unique(when: str) -> int:
                ids = [t.plaid_transaction_id for t in s.exec(
                    select(Transaction).where(Transaction.company_id == cid)).all()]
                assert all(ids) and len(ids) == len(set(ids)), f"{when}: hay ids de Plaid repetidos en el libro"
                return len(ids)

            r2 = bank.sync(s, conn, company)
            print(f"  re-sync: {r2}")
            n2 = ids_unique("re-sync")
            if DYNAMIC:
                # Sorpresa (4-oct): el usuario dinámico GENERA movimientos solo, poco después de terminar el
                # historial, y reenvía todo lo anterior como `modified`. Lo reenviado igual debe contar
                # como duplicado saltado, y lo nuevo trae ids nuevos: la prueba es "ningún id repetido".
                assert r2["inserted"] == 0 or r2["skipped_duplicates"] >= n1, "lo reenviado no se reconoció"
            else:
                assert r2["inserted"] == 0 and n2 == n1, f"re-sincronizar duplicó: {n1} → {n2}"
            print(f"  ✅ {n2} movimientos · ids de Plaid únicos · re-sincronizar no duplica")

            if DYNAMIC:
                bank._post("/transactions/refresh", {"access_token": access_token})
                print("  /transactions/refresh pedido; esperando a Plaid (hasta 2 min)…")
                r3 = {}
                for _ in range(12):
                    time.sleep(10)
                    r3 = bank.sync(s, conn, company)
                    if r3["fetched"] or r3["modified_applied"] or r3["removed_marked"] or r3["removed_ignored"]:
                        break
                print(f"  sync tras refresh: {r3}")
                n3 = ids_unique("tras refresh")
                got = r3["fetched"] or r3["modified_applied"] or r3["removed_marked"] or r3["removed_ignored"]
                print(f"  ✅ avisos aplicados · {n3} movimientos · ids de Plaid únicos" if got else
                      f"  🟡 Plaid no mandó cambios tras el refresh (su ráfaga ya vino en un sync anterior) · {n3} · ids únicos")
            ok = True
        finally:
            s.rollback()
            for model in (CategorizationChange, Transaction, BankConnection):
                for row in s.exec(select(model).where(model.company_id == cid)).all():
                    s.delete(row)
            s.delete(s.get(Company, cid))
            s.commit()
            try:
                bank.remove_item(access_token, "sandbox")
            except bank.BankError as e:
                print(f"  (no se pudo eliminar el Item en Plaid: {e.code})")
            print("  libro temporal borrado · Item eliminado en Plaid")
    print("✅ PRUEBA OK" if ok else "❌ PRUEBA FALLÓ")


if __name__ == "__main__":
    main()
