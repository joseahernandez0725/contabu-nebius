# Imagen base: Python 3.14 slim (coincide con el venv local; requirements.txt se congeló ahí).
FROM python:3.14-slim

WORKDIR /app

# Instalar dependencias primero (capa cacheada mientras requirements.txt no cambie).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código del backend.
COPY . .

# Cloud Run inyecta el puerto en $PORT (8080 por defecto en local).
ENV PORT=8080
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
