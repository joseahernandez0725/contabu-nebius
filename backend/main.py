"""
ContaBu — backend (FastAPI).
Fase 1: primer endpoint de prueba para verificar que el frontend puede
hablar con el backend.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
