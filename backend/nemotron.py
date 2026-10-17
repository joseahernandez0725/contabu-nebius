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
- **Medible:** cada `Reply` trae tokens, costo en dólares y latencia (regla del área: toda llamada
  nueva se mide). Los agentes la guardan en la tabla `agentcall` (`agent_calls.py`).
- **Razonamiento APAGADO por defecto** (7-oct). Nemotron razona antes de contestar y ese razonamiento
  se cobra como tokens de salida: el ping gastó 230–247 tokens razonando contra **18** sin razonar
  (✅ medido en Nebius). Se apaga con `chat_template_kwargs: {"enable_thinking": false}` (✅ ficha
  oficial del modelo en Hugging Face; ✅ Nebius lo respeta). Quien lo encienda (`reasoning=True`)
  recibe un piso de `max_tokens` (`REASONING_MIN_TOKENS`): con poco margen el modelo se queda sin
  tokens a mitad del razonamiento y no contesta nada.
- **"Se quedó sin tokens" es su propio error** (`out_of_tokens`), no `not_json`: el `content` llega
  vacío con `finish_reason="length"` y lo que hay que cambiar es el tope, no el formato.

Comprobar la conexión cuando exista la clave:
    .venv/bin/python nemotron.py --models     # ids disponibles en la cuenta
    .venv/bin/python nemotron.py --ping       # una llamada mínima con JSON estricto (sin razonar)
    .venv/bin/python nemotron.py --ping --reasoning   # la misma, dejándolo razonar
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
# Piso cuando se pide razonar. ✅ Medido el 7-oct: una propuesta real del ④ con Nano razonó MÁS de
# 4000 tokens y se quedó sin contestar (`out_of_tokens`). Configurable para medir (NEMOTRON_REASONING_MIN_TOKENS).
REASONING_MIN_TOKENS = int(os.getenv("NEMOTRON_REASONING_MIN_TOKENS") or 16000)

# Precio por millón de tokens (entrada, salida), en dólares. ✅ Catálogo público de Nebius
# (`/api/public/models_info`, leído el 4-oct-2026). Un modelo que no está aquí se mide en tokens y
# su costo queda en None: nunca se inventa un precio.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
    "nvidia/Nemotron-3_5-Lightning": (0.06, 0.24),
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 3.00),
    # ✅ 16-oct (chat central: /v1/models y 2 pings): mismo precio que Super. Se usa SOLO con texto.
    "nvidia/Nemotron-3.5-Super-VL": (0.30, 0.90),
}

SENT: deque = deque(maxlen=50)          # mensajes enviados (en memoria, para las pruebas de privacidad)
_FORMAT_SUPPORT: dict[str, str] = {}    # modelo → "json_schema" | "json_object" | "none"
_http: Optional[httpx.Client] = None


class NemotronUnavailable(RuntimeError):
    """No hay clave de Nebius configurada: la IA no está disponible (el resto de la app sí)."""


class NemotronError(RuntimeError):
    """La llamada falló o la respuesta no es JSON. `code` es estable y en inglés."""

    def __init__(self, code: str, detail: str = "", usage: Optional["Usage"] = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.usage = usage   # lo gastado aunque falle (p. ej. `out_of_tokens` sí cobra)


@dataclass
class Usage:
    """Lo que costó UNA llamada. `reasoning_tokens_est` es una ESTIMACIÓN: Nebius no separa los tokens
    de razonamiento (`completion_tokens_details` llega vacío, ✅ 7-oct), así que se reparten los de
    salida en proporción a los caracteres de `reasoning` y de `content`."""
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens_est: int = 0
    latency_ms: int = 0
    finish_reason: Optional[str] = None
    format_used: Optional[str] = None
    reasoning: bool = False

    @property
    def cost_usd(self) -> Optional[float]:
        price = PRICES_PER_MTOK.get(self.model)
        if price is None:
            return None
        return (self.prompt_tokens * price[0] + self.completion_tokens * price[1]) / 1_000_000


@dataclass
class Reply:
    data: object                    # el JSON ya parseado (dict o list) — SIN validar: valida quien llama
    usage: Usage
    raw_text: str = field(default="", repr=False)

    # Atajos (compatibles con quien ya los leía)
    @property
    def model(self) -> str:
        return self.usage.model

    @property
    def format_used(self) -> Optional[str]:
        return self.usage.format_used

    @property
    def prompt_tokens(self) -> int:
        return self.usage.prompt_tokens

    @property
    def completion_tokens(self) -> int:
        return self.usage.completion_tokens

    @property
    def latency_ms(self) -> int:
        return self.usage.latency_ms


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


def _usage_from(body: dict, name: str, mode: str, reasoning: bool, started: float) -> Usage:
    choice = (body.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = body.get("usage") or {}
    completion = int(usage.get("completion_tokens") or 0)
    r_chars, c_chars = len(msg.get("reasoning") or msg.get("reasoning_content") or ""), len(msg.get("content") or "")
    est = round(completion * r_chars / (r_chars + c_chars)) if (r_chars + c_chars) else 0
    return Usage(model=body.get("model") or name, prompt_tokens=int(usage.get("prompt_tokens") or 0),
                 completion_tokens=completion, reasoning_tokens_est=est,
                 latency_ms=int((time.monotonic() - started) * 1000),
                 finish_reason=choice.get("finish_reason"), format_used=mode, reasoning=reasoning)


def chat_json(system: str, user: str, schema: Optional[type[BaseModel]] = None, *,
              model: Optional[str] = None, temperature: float = 0.0, max_tokens: int = 2000,
              reasoning: bool = False) -> Reply:
    """Una llamada a Nemotron que devuelve JSON. `schema` (pydantic) se manda como `json_schema` si el
    modelo lo acepta; la respuesta vuelve SIN validar — la valida quien llama (p. ej. el validador de
    la fórmula), porque una fila rechazada se cuenta, no se esconde.

    `reasoning=False` (por defecto) apaga el razonamiento del modelo; `True` lo deja razonar y sube
    `max_tokens` al piso `REASONING_MIN_TOKENS`. Si aun así se queda sin tokens → `out_of_tokens`."""
    name = model or model_name()
    if reasoning:
        max_tokens = max(max_tokens, REASONING_MIN_TOKENS)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    modes = ["json_schema", "json_object", "none"]
    known = _FORMAT_SUPPORT.get(name)
    if known:
        modes = modes[modes.index(known):]
    SENT.append({"model": name, "messages": messages})
    started = time.monotonic()
    for mode in modes:
        payload = {"model": name, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                   "chat_template_kwargs": {"enable_thinking": bool(reasoning)}}
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
            # Se guarda lo que diga Nebius (✅ 16-oct: el 402 del fin del trial no decía por qué, y el cuerpo
            # no quedaba en ninguna parte para mirarlo después).
            logger.warning("Nebius 402: %s", r.text[:300])
            raise NemotronError("no_credits", f"la cuenta de Nebius no tiene saldo · Nebius dijo: {r.text[:200]}")
        if r.status_code >= 400:
            raise NemotronError(f"http_{r.status_code}", r.text[:200])
        _FORMAT_SUPPORT[name] = mode
        body = r.json()
        usage = _usage_from(body, name, mode, reasoning, started)
        text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        logger.info("nemotron | model=%s format=%s reasoning=%s tokens=%d/%d (razonamiento≈%d) "
                    "cost=$%s latency_ms=%d finish=%s", usage.model, mode, reasoning, usage.prompt_tokens,
                    usage.completion_tokens, usage.reasoning_tokens_est,
                    f"{usage.cost_usd:.6f}" if usage.cost_usd is not None else "?", usage.latency_ms,
                    usage.finish_reason)
        if usage.finish_reason == "length":
            raise NemotronError("out_of_tokens",
                                f"se quedó sin tokens (max_tokens={max_tokens}, razonamiento≈"
                                f"{usage.reasoning_tokens_est}); sube max_tokens o apaga el razonamiento",
                                usage=usage)
        try:
            data = parse_json(text)
        except NemotronError as e:
            e.usage = usage
            raise
        return Reply(data=data, usage=usage, raw_text=text)
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
            think = "--reasoning" in sys.argv
            rep = chat_json("Reply only with JSON.",
                            'Return {"ok": true, "model_family": "<your model family>"}.', _Ping,
                            max_tokens=200, reasoning=think)
            u = rep.usage
            cost = f"${u.cost_usd:.6f}" if u.cost_usd is not None else "sin precio"
            print(f"✅ {u.model} · formato {u.format_used} · razonamiento {'sí' if think else 'no'} · "
                  f"{u.prompt_tokens}/{u.completion_tokens} tokens (razonamiento≈{u.reasoning_tokens_est}) · "
                  f"{cost} · {u.latency_ms} ms → {rep.data}")
        else:
            print(__doc__)
    except NemotronUnavailable as e:
        sys.exit(f"⏸️  {e}")
    except NemotronError as e:
        sys.exit(f"❌ {e}")
