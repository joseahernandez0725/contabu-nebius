"""
ContaBu — LO QUE CUESTA CADA LLAMADA DE UN AGENTE (FASE 11, Auditoría / IA · 7-oct-2026).

Cada llamada de los agentes a Nemotron deja una fila `AgentCall`: tokens de entrada y de salida,
razonamiento estimado, costo en dólares, latencia y resultado. Nunca el contenido.

Para qué:
- **Medir desde el principio** (regla del área): cuánto cuesta una propuesta del ④ o una revisión
  del ①, y cuánto razonamiento se paga.
- **El tope por oficina** (`DEMO Y ENTREGA.md` → *Reglas del demo*): cada llamada gasta crédito de
  Nebius. Se cuenta por oficina en las últimas 24 horas.
"""

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from sqlalchemy import text
from sqlmodel import Session, func, select

from models import AgentCall, Company
from nemotron import Usage

# Llamadas por oficina en 24 h. En el demo es más bajo (cada visitante tiene su oficina).
_DEMO = os.getenv("DEMO_MODE", "").lower() in {"1", "true", "yes"}
DAILY_CAP = int(os.getenv("AGENT_DAILY_CALLS") or (20 if _DEMO else 200))
# Búsquedas del ② verificador (Tavily) por oficina en 24 h: tope APARTE, porque cada una gasta un
# crédito del plan gratis (1.000/mes) y la caché es por oficina (cada visitante del demo busca de nuevo).
DAILY_SEARCH_CAP = int(os.getenv("AGENT_DAILY_SEARCHES") or (15 if _DEMO else 100))
_MODEL_AGENTS = ("drafter", "reviewer", "note_checker", "personal_judge")


class CapReached(RuntimeError):
    code = "agent_cap_reached"


class GlobalCapReached(CapReached):
    code = "demo_global_cap_reached"   # FASE 13 (Firm, 23-oct): el tope diario de TODO el demo (`demo.py`)


class AlreadyRunning(RuntimeError):
    code = "agent_busy"


# ── UN PASO DE AGENTES A LA VEZ POR LIBRO (20-oct) ─────────────────────────────────────────────────
# ✅ 20-oct, demo (libro 7, 15:28 UTC): "Revisar el libro" corrió DOS veces en paralelo —cada llamada
# duplicada, doble costo y doble cupo—. El candado vive en Postgres, así vale con varias instancias de
# Cloud Run. Es `pg_try_advisory_xact_lock` (de TRANSACCIÓN, no de sesión) sobre una conexión aparte que
# mantiene su transacción abierta mientras corre el paso: el Neon del demo entra por su pooler
# (PgBouncer en modo transacción) y ahí un candado de sesión puede quedarse pegado a una conexión del
# pooler que otro cliente reusa. Al terminar (o si el proceso muere) la transacción se cierra y el candado
# se suelta solo. La sesión del agente sigue haciendo sus commits por su cuenta: no la toca.
_LOCK_SPACE = 0x41474E54   # "AGNT": el espacio de candados de los agentes; el segundo número es el libro


@contextmanager
def exclusive(company: Company) -> Iterator[None]:
    """Corre el bloque solo si NINGÚN otro paso de agentes está corriendo sobre este libro; si no,
    `AlreadyRunning` al instante (no espera)."""
    from db import engine
    with engine.connect() as conn:
        try:
            got = conn.execute(text("select pg_try_advisory_xact_lock(:space, :book)"),
                               {"space": _LOCK_SPACE, "book": company.id}).scalar()
            if not got:
                raise AlreadyRunning(f"libro {company.id}")
            yield
        finally:
            conn.rollback()   # cierra la transacción → suelta el candado


def _since() -> datetime:
    return (datetime.now(timezone.utc) - timedelta(hours=24)).replace(tzinfo=None)


def calls_last_24h(session: Session, company: Company, agents: tuple = _MODEL_AGENTS) -> int:
    q = select(func.count(AgentCall.id)).where(AgentCall.created_at >= _since(), AgentCall.agent.in_(agents))
    q = q.where(AgentCall.firm_id == company.firm_id) if company.firm_id else q.where(
        AgentCall.company_id == company.id)
    return int(session.exec(q).one())


def check_cap(session: Session, company: Company, agent: Optional[str] = None) -> None:
    """Antes de llamar: ¿la oficina todavía tiene llamadas (o búsquedas, `agent="verifier"`) hoy?"""
    import demo
    demo.check_global_cap(session, agent)   # FASE 13 (Firm, 23-oct): y el demo entero (no hace nada fuera)
    if agent == "verifier":
        if calls_last_24h(session, company, ("verifier",)) >= DAILY_SEARCH_CAP:
            raise CapReached(f"{DAILY_SEARCH_CAP} búsquedas en 24 h")
        return
    if calls_last_24h(session, company) >= DAILY_CAP:
        raise CapReached(f"{DAILY_CAP} llamadas en 24 h")


def record(session: Session, company: Company, agent: str, usage: Optional[Usage], *,
           outcome: str = "ok", user_id: Optional[int] = None, items_out: int = 0,
           items_valid: int = 0, model: Optional[str] = None, credits: int = 0,
           latency_ms: Optional[int] = None) -> AgentCall:
    """Guarda una llamada (también las que fallaron: un `out_of_tokens` se cobra igual). No hace
    commit: lo hace quien llama."""
    row = AgentCall(
        company_id=company.id, firm_id=company.firm_id, user_id=user_id, agent=agent,
        model=(usage.model if usage else model) or "?", reasoning=bool(usage and usage.reasoning),
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        reasoning_tokens_est=usage.reasoning_tokens_est if usage else 0,
        cost_usd=usage.cost_usd if usage else None,
        latency_ms=usage.latency_ms if usage else (latency_ms or 0),
        finish_reason=usage.finish_reason if usage else None, outcome=outcome,
        items_out=items_out, items_valid=items_valid, credits=credits,
    )
    session.add(row)
    return row


def usage_dict(usage: Optional[Usage]) -> Optional[dict]:
    if usage is None:
        return None
    return {"model": usage.model, "reasoning": usage.reasoning, "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens, "reasoning_tokens_est": usage.reasoning_tokens_est,
            "cost_usd": usage.cost_usd, "latency_ms": usage.latency_ms, "finish_reason": usage.finish_reason}


def summary(session: Session, company: Company, limit: int = 20) -> dict:
    """Totales del libro por agente + las últimas llamadas + cuántas le quedan hoy a la oficina."""
    rows = session.exec(select(AgentCall).where(AgentCall.company_id == company.id)
                        .order_by(AgentCall.id.desc())).all()
    by_agent: dict[str, dict] = {}
    for r in rows:
        a = by_agent.setdefault(r.agent, {"calls": 0, "ok": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                          "reasoning_tokens_est": 0, "cost_usd": 0.0, "credits": 0})
        a["credits"] += r.credits or 0
        a["calls"] += 1
        a["ok"] += r.outcome == "ok"
        a["prompt_tokens"] += r.prompt_tokens
        a["completion_tokens"] += r.completion_tokens
        a["reasoning_tokens_est"] += r.reasoning_tokens_est
        a["cost_usd"] += r.cost_usd or 0.0
    used = calls_last_24h(session, company)
    searches = calls_last_24h(session, company, ("verifier",))
    return {
        "searches_today": searches, "daily_search_cap": DAILY_SEARCH_CAP,
        "searches_left_today": max(0, DAILY_SEARCH_CAP - searches),
        "by_agent": by_agent,
        "total_cost_usd": sum(a["cost_usd"] for a in by_agent.values()),
        "calls_today": used, "daily_cap": DAILY_CAP, "left_today": max(0, DAILY_CAP - used),
        "recent": [{"agent": r.agent, "model": r.model, "outcome": r.outcome, "reasoning": r.reasoning,
                    "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                    "reasoning_tokens_est": r.reasoning_tokens_est, "cost_usd": r.cost_usd,
                    "latency_ms": r.latency_ms, "items_out": r.items_out, "items_valid": r.items_valid,
                    "credits": r.credits,
                    "created_at": r.created_at.isoformat()} for r in rows[:limit]],
    }


# ── Lo que costó UNA acción (14-oct: cada botón dice antes cuánto costará y después cuánto costó) ──

def mark(session: Session, company: Company) -> int:
    """El id de la última llamada del libro: lo que se registre después es de ESTA acción."""
    return int(session.exec(select(func.max(AgentCall.id)).where(AgentCall.company_id == company.id)).one() or 0)


def spent(session: Session, company: Company, since_id: int) -> dict:
    """Lo que se gastó desde `mark()`: llamadas a Nemotron, costo, búsquedas y créditos de Tavily."""
    rows = session.exec(select(AgentCall).where(AgentCall.company_id == company.id, AgentCall.id > since_id)).all()
    model = [r for r in rows if r.agent != "verifier"]
    web = [r for r in rows if r.agent == "verifier"]
    return {"calls": len(model), "cost_usd": round(sum(r.cost_usd or 0.0 for r in model), 6),
            "searches": len(web), "credits": sum(r.credits or 0 for r in web)}


# ── EL EQUIPO TRABAJANDO (18-oct, ronda de diseño: "la IA al frente") ─────────────────────────────
# Qué hizo cada agente en la ÚLTIMA CORRIDA del libro, leído de `AgentCall` (nunca inventado). No hay un
# id de corrida: una corrida = las llamadas seguidas del equipo, sin una pausa de más de `RUN_GAP_S`
# entre una y la siguiente ("Revisar el libro" hace auditar → verificar → redactar sin pausas). Un
# agente que no aparece en esa corrida "no corrió" (p. ej. el ⑥ sin notas nuevas): se dice, con cuándo
# trabajó por última vez. Fuera del equipo: el mapeo de columnas al importar (`column_mapper`…).
# Con `since_id` (18-oct, la pantalla mientras corre): la corrida es EXACTA = las llamadas después de ese id
# (el `max_id` que la pantalla leyó al empezar la acción, como `mark()`/`spent()`); sin agrupar por pausas.

TEAM = ("note_checker", "personal_judge", "reviewer", "verifier", "drafter")
_MODEL_KEY = {"note_checker": "notes", "personal_judge": "personal", "reviewer": "reviewer", "drafter": "drafter"}
RUN_GAP_S = 180


def _sum_counts(dicts) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in dicts:
        for k, n in (d or {}).items():
            out[k] = out.get(k, 0) + int(n)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def team(session: Session, company: Company, since_id: Optional[int] = None) -> dict:
    import agent_models
    from sqlalchemy import text
    # "Hace cuánto" con el reloj DE LA BASE: `created_at` (sin zona) queda guardado en la hora de la sesión de
    # Postgres, no en UTC (✅ 18-oct: "hace 7 horas" justo después de correr; el mismo error que se arregló el
    # 8-oct en `book_review.seconds_since_review`). Restar dos `created_at` entre sí sí está bien.
    now = session.connection().execute(text("SELECT LOCALTIMESTAMP")).scalar()
    ago = lambda t: max(0, int((now - t).total_seconds()))   # noqa: E731
    q = select(AgentCall).where(AgentCall.company_id == company.id, AgentCall.agent.in_(TEAM))
    if since_id is not None:
        q = q.where(AgentCall.id > since_id)
    rows = session.exec(q.order_by(AgentCall.id.desc()).limit(500)).all()
    # La corrida: de la llamada más nueva hacia atrás, mientras la pausa entre dos llamadas seguidas no pase
    # de RUN_GAP_S. `created_at` se escribe al TERMINAR la llamada: la pausa descuenta lo que tardó.
    run: list[AgentCall] = []
    for r in rows:
        if since_id is None and run and \
                (run[-1].created_at - r.created_at).total_seconds() - run[-1].latency_ms / 1000 > RUN_GAP_S:
            break
        run.append(r)
    last = dict(session.exec(select(AgentCall.agent, func.max(AgentCall.created_at))
                             .where(AgentCall.company_id == company.id, AgentCall.agent.in_(TEAM))
                             .group_by(AgentCall.agent)).all())
    agents = []
    for a in TEAM:
        mine = [r for r in run if r.agent == a]
        if a in _MODEL_KEY:
            model_id, reasoning = agent_models.get(_MODEL_KEY[a])
            key = agent_models.short(model_id)
            model = {"key": key, "label": agent_models.LABELS.get(key, model_id), "reasoning": reasoning}
        else:
            model = {"key": "tavily", "label": "Tavily", "reasoning": False}
        agents.append({
            "agent": a, "model": model,
            "last_seconds_ago": ago(last[a]) if a in last else None,
            "run": {
                "calls": len(mine), "ok": sum(r.outcome == "ok" for r in mine),
                "items_out": sum(r.items_out for r in mine), "items_valid": sum(r.items_valid for r in mine),
                "latency_ms": sum(r.latency_ms for r in mine),
                "cost_usd": round(sum(r.cost_usd or 0.0 for r in mine), 6),
                "credits": sum(r.credits or 0 for r in mine),
                # 20-oct: el ④ guarda por motivo cuántas filas rechazó el validador (`rejections`).
                "rejections": _sum_counts(r.rejections for r in mine),
            } if mine else None,
        })
    return {
        "max_id": mark(session, company),
        "run": {"seconds_ago": ago(run[0].created_at), "calls": len(run),
                "cost_usd": round(sum(r.cost_usd or 0.0 for r in run), 6),
                "credits": sum(r.credits or 0 for r in run)} if run else None,
        "agents": agents,
    }
