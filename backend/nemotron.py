"""
ContaBu — el cliente de NEMOTRON sobre NEBIUS TOKEN FACTORY (FASE 11, Auditoría / IA · 4-oct-2026).

Requisito del concurso Nebius x NVIDIA: una llamada en ejecución a Nebius Token Factory con un modelo
abierto de NVIDIA. La API es compatible con OpenAI (`POST /chat/completions`); se habla con `httpx`
(ya instalado) en vez de sumar el SDK de OpenAI: es una sola ruta y así no entra otra dependencia.

Configuración (en `backend/.env`, NUNCA en el código ni en el chat):
    NEBIUS_API_KEY=...                                   # obligatoria para llamar
    NEBIUS_MODEL=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B   # opcional (id del catálogo público, 4-oct)
    NEBIUS_BASE_URL=https://api.tokenfactory.nebius.com/v1/   # opcional

Reglas:
- **El cliente se crea la primera vez que se usa**, no al importar: sin clave, el backend arranca
  igual y solo falla (con `NemotronUnavailable`) quien de verdad intente llamar.
- **JSON estricto:** se pide `response_format: json_schema`. ✅ El catálogo de Nebius no dice si
  Nemotron lo soporta (verificado 4-oct): si el endpoint lo rechaza, se reintenta con `json_object`
  y luego sin formato, y se recuerda por modelo. En cualquier caso **el que valida es el código**
  (`formula.check_rows`), no el proveedor: el esquema del proveedor no conoce el plan de cuentas de
  cada libro.
- **Privacidad:** en el log va el modelo, los tokens y la latencia; **nunca el contenido** del prompt
  ni de la respuesta. Lo que se manda queda en memoria (`SENT`, los últimos 50) solo para que una
  prueba pueda comprobar que no viajaron descripciones, montos ni nombres.
- **Medible:** cada `Reply` trae tokens y latencia (regla del área: toda llamada nueva se mide).

Comprobar la conexión cuando exista la clave:
    .venv/bin/python nemotron.py --models     # ids disponibles en la cuenta
    .venv/bin/python nemotron.py --ping       # una llamada mínima con JSON estricto
"""

import json
import logging
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

logger = logging.getLogger("contabu.nemotron")

DEFAULT_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
_RETRY_CODES = {429, 500, 502, 503, 504}

SENT: deque = deque(maxlen=50)          # mensajes enviados (en memoria, para las pruebas de privacidad)
_FORMAT_SUPPORT: dict[str, str] = {}    # modelo → "json_schema" | "json_object" | "none"
_http: Optional[httpx.Client] = None


class NemotronUnavailable(RuntimeError):
    """No hay clave de Nebius configurada: la IA no está disponible (el resto de la app sí)."""


class NemotronError(RuntimeError):
    """La llamada falló o la respuesta no es JSON. `code` es estable y en inglés."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass
class Reply:
    data: object                    # el JSON ya parseado (dict o list) — SIN validar: valida quien llama
    model: str
    format_used: str                # json_schema | json_object | none
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    raw_text: str = field(default="", repr=False)


def model_name() -> str:
    return os.getenv("NEBIUS_MODEL") or DEFAULT_MODEL


def _client() -> httpx.Client:
    """El cliente HTTP, creado la primera vez que se usa."""
    global _http
    key = os.getenv("NEBIUS_API_KEY")
    if not key:
        raise NemotronUnavailable("NEBIUS_API_KEY no está configurada en backend/.env")
    if _http is None:
        base = os.getenv("NEBIUS_BASE_URL") or DEFAULT_BASE_URL
        _http = httpx.Client(base_url=base if base.endswith("/") else base + "/",
                             headers={"Authorization": f"Bearer {key}"}, timeout=90.0)
    return _http


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json(text: str):
    """El texto del modelo → JSON. Quita el razonamiento `<think>…</think>` que emiten los modelos de
    razonamiento y las cercas ```json; si sobra texto alrededor, toma el primer objeto/lista completo.
    Nunca evalúa nada: solo `json.loads`."""
    t = _FENCE_RE.sub("", _THINK_RE.sub("", text or "").strip()).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise NemotronError("not_json", "la respuesta no trae JSON")
    try:
        obj, _ = json.JSONDecoder().raw_decode(t[min(starts):])
        return obj
    except json.JSONDecodeError as e:
        raise NemotronError("not_json", str(e))


def _response_format(mode: str, schema: Optional[type[BaseModel]]):
    if mode == "json_schema" and schema is not None:
        return {"type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": True}}
    if mode in ("json_schema", "json_object"):
        return {"type": "json_object"}
    return None


def _post(payload: dict, max_retries: int = 3) -> httpx.Response:
    delay = 2.0
    for attempt in range(1, max_retries + 1):
        try:
            r = _client().post("chat/completions", json=payload)
        except httpx.HTTPError as e:
            if attempt == max_retries:
                raise NemotronError("network", type(e).__name__)
            r = None
        if r is not None and r.status_code not in _RETRY_CODES:
            return r
        if attempt == max_retries:
            return r
        logger.warning("Nebius %s (intento %d/%d); reintentando en %.0fs",
                       getattr(r, "status_code", "network"), attempt, max_retries, delay)
        time.sleep(delay)
        delay *= 2


def _format_rejected(r: httpx.Response) -> bool:
    """¿El 400 es porque el modelo no acepta ese `response_format`?"""
    if r.status_code not in (400, 422):
        return False
    body = r.text.lower()
    return "response_format" in body or "json_schema" in body or "json_object" in body or "guided" in body


def chat_json(system: str, user: str, schema: Optional[type[BaseModel]] = None, *,
              model: Optional[str] = None, temperature: float = 0.0, max_tokens: int = 4000) -> Reply:
    """Una llamada a Nemotron que devuelve JSON. `schema` (pydantic) se manda como `json_schema` si el
    modelo lo acepta; la respuesta vuelve SIN validar — la valida quien llama (p. ej. el validador de
    la fórmula), porque una fila rechazada se cuenta, no se esconde."""
    name = model or model_name()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    modes = ["json_schema", "json_object", "none"]
    known = _FORMAT_SUPPORT.get(name)
    if known:
        modes = modes[modes.index(known):]
    SENT.append({"model": name, "messages": messages})
    started = time.monotonic()
    for mode in modes:
        payload = {"model": name, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        fmt = _response_format(mode, schema)
        if fmt is not None:
            payload["response_format"] = fmt
        r = _post(payload)
        if _format_rejected(r) and mode != "none":
            logger.info("Nebius: %s no acepta response_format=%s; se prueba el siguiente", name, mode)
            continue
        if r.status_code == 401:
            raise NemotronError("unauthorized", "revisa NEBIUS_API_KEY")
        if r.status_code == 402:
            raise NemotronError("no_credits", "la cuenta de Nebius no tiene saldo")
        if r.status_code >= 400:
            raise NemotronError(f"http_{r.status_code}", r.text[:200])
        _FORMAT_SUPPORT[name] = mode
        body = r.json()
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        usage = body.get("usage") or {}
        reply = Reply(data=parse_json(text), model=body.get("model") or name, format_used=mode,
                      prompt_tokens=int(usage.get("prompt_tokens") or 0),
                      completion_tokens=int(usage.get("completion_tokens") or 0),
                      latency_ms=int((time.monotonic() - started) * 1000), raw_text=text)
        logger.info("nemotron | model=%s format=%s tokens=%d/%d latency_ms=%d", reply.model, mode,
                    reply.prompt_tokens, reply.completion_tokens, reply.latency_ms)
        return reply
    raise NemotronError("format_unsupported", "el modelo rechazó todos los formatos de respuesta")


def list_models() -> list[str]:
    r = _client().get("models")
    if r.status_code >= 400:
        raise NemotronError(f"http_{r.status_code}", r.text[:200])
    return sorted(m.get("id", "") for m in r.json().get("data", []))


class _Ping(BaseModel):
    ok: bool
    model_family: str


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        if "--models" in sys.argv:
            ids = list_models()
            print("\n".join(i for i in ids if "nemotron" in i.lower()) or "(ningún Nemotron en la lista)")
            print(f"\n{len(ids)} modelos en total · NEBIUS_MODEL = {model_name()}")
        elif "--ping" in sys.argv:
            rep = chat_json("Reply only with JSON.",
                            'Return {"ok": true, "model_family": "<your model family>"}.', _Ping, max_tokens=200)
            print(f"✅ {rep.model} · formato {rep.format_used} · {rep.prompt_tokens}/{rep.completion_tokens} "
                  f"tokens · {rep.latency_ms} ms → {rep.data}")
        else:
            print(__doc__)
    except NemotronUnavailable as e:
        sys.exit(f"⏸️  {e}")
    except NemotronError as e:
        sys.exit(f"❌ {e}")
