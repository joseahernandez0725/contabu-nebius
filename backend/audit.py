"""
ContaBu — AGENTE AUDITOR de categorización (idea #18, ver `docs/IDEAS.md` y `PROPUESTA DE REGLAS.md`).

Escanea las transacciones YA categorizadas del usuario y marca **sospechosos** reusando la lógica del
boletín de la Fase 8 (`eval_engine.py`, borrado el 4-oct) pero sobre los **datos vivos** del cliente. Determinista, sin IA
→ 1 click, rápido y gratis. La capa de IA (juicio fino "¿esto es realmente raro?") queda para después.

Es **aditivo** a la cola de revisión: atrapa lo que `needs_review` NO ve — sobre todo **inconsistencias
entre transacciones** del mismo comercio. Calibrado para evitar falsos positivos (excluye gas/super, cuyo
split por monto es intencional, y los cheques, que no agrupan).
"""
from collections import Counter, defaultdict

from rules import classify_merchant, merchant_signature

# account_type (valor string) que SUMA en el P&L; el resto (COGS/OPEX/OTHER_EXPENSE) resta; NON_PL se excluye.
_INCOME = {"REVENUE", "OTHER_INCOME"}


def audit_categorization(items: list[dict], rules_by_id: dict[int, dict] | None = None) -> list[dict]:
    """`items`: dicts con `id`, `description`, `account_type` (valor string o None), `category`,
    `is_inflow` y (opcional) `matched_rule_id`. `rules_by_id`: id → {match_text, account_type,
    category} de las reglas COMPANY vivas — habilita la señal de DESVÍO DE REGLA (FASE 9 Paso 4).
    Devuelve una lista de hallazgos ordenados por severidad. Cada hallazgo: `type`, `severity`,
    `transaction_ids`, `detail` (+ `merchant` en inconsistencias)."""
    findings: list[dict] = []

    # 0) DESVÍO DE REGLA (señal EXACTA, gracias a matched_rule_id — FASE 9): la transacción dice que
    #    la decidió una regla, pero su categoría YA NO coincide con la regla (típico: el dueño editó
    #    la regla en la tabla después). Las excepciones intencionales ("solo esta") NO caen aquí:
    #    al corregir a mano se limpia matched_rule_id. Fix de 1 toque: re-aplicar la regla.
    if rules_by_id:
        for it in items:
            rid = it.get("matched_rule_id")
            rule = rules_by_id.get(rid) if rid is not None else None
            if rule is None:
                continue
            if (it.get("account_type"), it.get("category")) != (rule["account_type"], rule["category"]):
                findings.append({
                    "type": "rule_deviation",
                    "severity": "high",
                    "transaction_ids": [it["id"]],
                    "detail": (f"rule “{rule['match_text']}” says {rule['account_type']}/{rule['category']} "
                               f"but this is {it.get('account_type')}/{it.get('category')}"),
                    "suggestion": f"{rule['account_type']}/{rule['category']}",
                    "suggested_account_type": rule["account_type"],  # estructurado → fix de 1 toque
                    "suggested_category": rule["category"],
                    "examples": [{"id": it["id"], "description": it.get("description"),
                                  "is_inflow": bool(it.get("is_inflow"))}],
                })

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
                    # REGLA DE ORO: el dato va en INGLÉS; el frontend traduce por `type` (i18n).
                    "detail": f"{at} but the money came {'IN' if it.get('is_inflow') else 'OUT'}",
                    "account_type": at,                         # estructurado, para que la UI lo traduzca
                    "direction": "in" if it.get("is_inflow") else "out",
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
