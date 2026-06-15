"""
ContaBu — backend (FastAPI).
Endpoints de la API: prueba (/api/hello) e ingestión de archivos bancarios.
"""

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from db import get_session
from ingestion import ingest_csv
from models import Company

# `app` es nuestra aplicación. uvicorn la "sirve" y queda esperando peticiones.
app = FastAPI(title="ContaBu API")

# CORS: permite que el frontend (que corre en otro puerto, el 5173)
# pueda llamar a este backend (puerto 8000). Sin esto, el navegador lo bloquea.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/hello")
def hello():
    """Endpoint de prueba: responde un JSON. El frontend lo pedirá y lo mostrará."""
    return {"message": "¡Hola desde el backend de ContaBu! 🚀"}


@app.post("/api/companies/{company_id}/transactions/upload")
async def upload_transactions(
    company_id: int,
    file: UploadFile,
    session: Session = Depends(get_session),
):
    """Sube un CSV bancario y dispara el pipeline (mapeo IA → parse → categoriza → guarda).

    `limit` está fijo en dev para no agotar el rate limit del tier gratis; se quitará al
    resolver el bulk asíncrono (ver FASE 5.md).
    """
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")

    content = await file.read()
    result = ingest_csv(
        session, company_id, company.sector, content, limit=20
    )
    return result
