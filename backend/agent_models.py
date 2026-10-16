"""
ContaBu — QUÉ MODELO USA CADA AGENTE, y si razona (FASE 11, Auditoría / IA · 15-oct-2026).

Cada agente con modelo se configura POR SEPARADO, con variables de entorno en `backend/.env`; sin ellas,
corren con los valores de hoy (`DEFAULTS`):

    AGENT_MODEL_REVIEWER  / AGENT_REASONING_REVIEWER    ① revisor
    AGENT_MODEL_DRAFTER   / AGENT_REASONING_DRAFTER     ④ redactor de la fórmula
    AGENT_MODEL_PERSONAL  / AGENT_REASONING_PERSONAL    ⑤ ¿personal para este negocio?
    AGENT_MODEL_NOTES     / AGENT_REASONING_NOTES       traductor de notas (la nota → chequeo del auditor)

El modelo se escribe con su nombre corto (`nano`, `lightning`, `super`, `ultra`) o con el id completo de
Nebius; el razonamiento, `1`/`0` (`true`/`false`). El modelo de cada llamada queda en `agentcall.model`.
`OVERRIDES` es para la medición (`team_matrix.py`), en el mismo proceso.
"""

import os
from typing import Optional

MODEL_IDS = {
    "nano": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "lightning": "nvidia/Nemotron-3_5-Lightning",
    "super": "nvidia/nemotron-3-super-120b-a12b",
    "ultra": "nvidia/Nemotron-3-Ultra-550b-a55b",
}
# La mejor combinación EN EQUIPO (✅ 15/16-oct, `team_matrix.py`, con la regla escrita antes de medir en
# FASE 11.A): ① y ⑤ en Nano sin razonar (ninguna variación les ganó); ④ en Super RAZONANDO (cobertura de lo
# Pendiente 47 % contra 41 % de Super sin razonar y 37 % de Ultra; tarda 20–40 s por llamada); traductor de
# notas en Super sin razonar (empata con Ultra y cuesta menos). Hasta el 15-oct: todo Nano (el traductor,
# razonando). Para volver a eso sin tocar código: AGENT_MODEL_DRAFTER=nano y AGENT_MODEL_NOTES=nano +
# AGENT_REASONING_NOTES=1 en backend/.env.
DEFAULTS: dict[str, tuple[str, bool]] = {
    "reviewer": ("nano", False),
    "drafter": ("super", True),
    "personal": ("nano", False),
    "notes": ("super", False),
}
_ENV = {"reviewer": "REVIEWER", "drafter": "DRAFTER", "personal": "PERSONAL", "notes": "NOTES"}
OVERRIDES: dict[str, tuple[str, bool]] = {}


def short(model: Optional[str]) -> Optional[str]:
    """El id completo → su nombre corto (para las tablas)."""
    return next((k for k, v in MODEL_IDS.items() if v == model), model)


def get(agent: str) -> tuple[str, bool]:
    """(id del modelo, ¿razona?) de un agente: la medición primero, después el entorno, después lo de hoy."""
    if agent in OVERRIDES:
        key, reasoning = OVERRIDES[agent]
    else:
        key = os.getenv(f"AGENT_MODEL_{_ENV[agent]}") or DEFAULTS[agent][0]
        raw = os.getenv(f"AGENT_REASONING_{_ENV[agent]}")
        reasoning = DEFAULTS[agent][1] if raw in (None, "") else raw.strip().lower() in {"1", "true", "yes", "si", "sí"}
    return MODEL_IDS.get(key, key), reasoning


def resolve(agent: str, model: Optional[str], reasoning: Optional[bool]) -> tuple[str, bool]:
    """Lo que pidió quien llama (si pidió algo) o la configuración del agente."""
    m, r = get(agent)
    return (model or m), (r if reasoning is None else reasoning)


def current() -> dict[str, dict]:
    """La configuración en uso, para registrarla junto a una medición."""
    return {a: {"model": short(get(a)[0]), "reasoning": get(a)[1]} for a in DEFAULTS}
