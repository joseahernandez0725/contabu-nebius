"""ContaBu — los 3 libros de ejemplo del demo, en tu oficina LOCAL (revisión externa, 26-oct).

En local, la primera vez que abres la app se crea una oficina con un libro vacío ("My Business"). Esto le agrega los
mismos tres libros inventados del demo público (Rivera HVAC, Casa Lupita y Sparkle Cleaning), con sus reglas, notas,
historial y la evidencia web guardada, para que haya algo que auditar sin subir nada.

Uso (con el backend configurado y la app abierta UNA vez con ese usuario):
    python seed_local.py              # el usuario de VITE_DEV_UID=local-dev
    python seed_local.py otro-uid
"""
import sys

from sqlmodel import Session, select

import demo_data
from auth import user_firm
from db import engine
from models import Company, Transaction, User


def main() -> int:
    uid = sys.argv[1] if len(sys.argv) > 1 else "local-dev"
    with Session(engine) as session:
        user = session.exec(select(User).where(User.firebase_uid == uid)).first()
        firm, _role = user_firm(user, session) if user else (None, None)
        if user is None or firm is None:
            print(f"No office for '{uid}' yet: open the app once (http://localhost:5173), then run this again.")
            return 1
        names = {c.name for c in session.exec(select(Company).where(Company.firm_id == firm.id))}
        if names & {"Rivera HVAC LLC"}:
            print("The sample books are already in your office.")
            return 0
        books = demo_data.seed_demo_books(session, firm, user)
        # El libro vacío que la app crea al entrar ("My Business", sin sector) abre "Set up your business" y tapa la
        # consola: se quita, pero SOLO si sigue vacío (sin sector ni movimientos).
        for c in session.exec(select(Company).where(Company.firm_id == firm.id, Company.sector == "")).all():
            if c not in books and not session.exec(select(Transaction.id).where(Transaction.company_id == c.id)).first():
                session.delete(c)
        session.commit()
        print("Created:", ", ".join(b.name for b in books), "- reload the app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
