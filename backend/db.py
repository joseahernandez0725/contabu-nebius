"""
Conexión a la base de datos de ContaBu (SQLModel sobre PostgreSQL).

- Lee la cadena de conexión `DATABASE_URL` desde el archivo `.env`.
- Expone el `engine` (la conexión raíz) y `get_session()` (una sesión por operación).
"""

import os

from dotenv import load_dotenv
from sqlmodel import Session, create_engine

# Carga las variables definidas en backend/.env (DATABASE_URL, etc.).
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "Falta DATABASE_URL. Copia backend/.env.example a backend/.env "
        "y pon tu contraseña real."
    )

# El engine es la conexión "raíz" a PostgreSQL.
# echo=True imprime el SQL que se ejecuta (útil para aprender; se apagará en producción).
engine = create_engine(DATABASE_URL, echo=True)


def get_session():
    """Entrega una sesión para leer/escribir; se cierra sola al terminar."""
    with Session(engine) as session:
        yield session
