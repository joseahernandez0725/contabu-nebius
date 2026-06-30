"""
ContaBu — AGENTE AUDITOR de categorización (idea #18, ver `docs/IDEAS.md` y `PROPUESTA DE REGLAS.md`).

Escanea las transacciones YA categorizadas del usuario y marca **sospechosos** reusando la lógica del
boletín de la Fase 8 (`eval_engine.py`) pero sobre los **datos vivos** del cliente. Determinista, sin IA
→ 1 click, rápido y gratis. La capa de IA (juicio fino "¿esto es realmente raro?") queda para después.

Es **aditivo** a la cola de revisión: atrapa lo que `needs_review` NO ve — sobre todo **inconsistencias
entre transacciones** del mismo comercio. Calibrado para evitar falsos positivos (excluye gas/super, cuyo
split por monto es intencional, y los cheques, que no agrupan).
"""
from collections import Counter, defaultdict

from rules import classify_merchant, merchant_signature

# account_type (valor string) que SUMA en el P&L; el resto (COGS/OPEX/OTHER_EXPENSE) resta; NON_PL se excluye.
_INCOME = {"REVENUE", "OTHER_INCOME"}


def audit_categorization(items: list[dict]) -> list[dict]:
    """`items`: dicts con `id`, `description`, `account_type` (valor string o None), `category`, `is_inflow`.
    Devuelve una lista de hallazgos ordenados por severidad. Cada hallazgo: `type`, `severity`,
    `transaction_ids`, `detail` (+ `merchant` en inconsistencias)."""
    findings: list[dict] = []

    # 1) INCONSISTENCIA POR COMERCIO: misma huella, distintas (account_type, category).
    #    Excluye gas/super (su split por monto es a propósito) y cheques (huella vacía).
    groups: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        desc = it.get("description") or ""
        sig = merchant_signature(desc)
        if sig and classify_merchant(desc) is None:
            groups[sig].append(it)
    for sig, its in groups.items():
        labels = Counter((it.get("account_type"), it.get("category"))
                         for it in its if it.get("account_type"))
        if len(labels) > 1:
            (maj_at, maj_cat), _ = labels.most_common(1)[0]  # la categoría dominante = la probable correcta
            odd = [it["id"] for it in its
                   if (it.get("account_type"), it.get("category")) != (maj_at, maj_cat)]
            findings.append({
                "type": "inconsistent_merchant",
                "severity": "high",
                "merchant": sig,
                "transaction_ids": [it["id"] for it in its],
                "odd_ids": odd,  # las que difieren de la mayoría (las que el fix de 1 toque corrige)
                "detail": sorted(f"{a}/{c}" for a, c in labels),
                "suggestion": f"{maj_at}/{maj_cat}",
                "suggested_account_type": maj_at,  # estructurado → el fix lo aplica directo
                "suggested_category": maj_cat,
                "examples": [{"id": it["id"], "description": it.get("description"), "is_inflow": bool(it.get("is_inflow"))} for it in its[:3]],
            })

    # 2) CONTRADICCIÓN DE SIGNO: la dirección del banco contradice la sección del P&L.
    for it in items:
        at = it.get("account_type")
        if at and at != "NON_PL":
            if (at in _INCOME) != bool(it.get("is_inflow")):
                findings.append({
                    "type": "sign_mismatch",
                    "severity": "high",
                    "transaction_ids": [it["id"]],
                    "detail": f"{at} pero el dinero {'ENTRÓ' if it.get('is_inflow') else 'SALIÓ'}",
                    "examples": [{"id": it["id"], "description": it.get("description"), "is_inflow": bool(it.get("is_inflow"))}],
                })

    # 3) SIN CATEGORIZAR: quedó sin sección del P&L.
    for it in items:
        if not it.get("account_type"):
            findings.append({
                "type": "uncategorized",
                "severity": "medium",
                "transaction_ids": [it["id"]],
                "detail": it.get("description"),
                "examples": [{"id": it["id"], "description": it.get("description"), "is_inflow": bool(it.get("is_inflow"))}],
            })

    _sev = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: _sev.get(f["severity"], 9))
    return findings
