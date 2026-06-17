"""
ContaBu — motor de reglas de categorización.

ANTES de llamar a la IA se consultan las `CategoryRule`: si un comercio/patrón ya tiene
regla (de la empresa o del sector), se aplica directo. Esto da:
- **consistencia** (mismo comercio → misma categoría siempre),
- **velocidad** y **ahorro** de llamadas a la IA,
- y construye el **moat** (las reglas crecen con las correcciones del usuario).

Prioridad: reglas **COMPANY** (privadas de la empresa) sobre **SECTOR** (genéricas);
a igualdad, gana la de `match_text` más específico (más largo).
"""

import re

from sqlalchemy import or_
from sqlmodel import Session, select

from models import CategoryRule, RuleScope


def normalize(text: str) -> str:
    """Normaliza una descripción para comparar: MAYÚSCULAS y solo alfanuméricos.

    'HOME DEPOT #2745 BUILDING' y 'HomeDepot.com' → 'HOMEDEPOT2745BUILDING' /
    'HOMEDEPOTCOM' (ambas contienen 'HOMEDEPOT'), así un match por subcadena funciona
    pese al ruido del extracto bancario.
    """
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def load_rules(session: Session, company_id: int, sector: str) -> list[CategoryRule]:
    """Carga (en UNA query) las reglas aplicables, ya ordenadas por prioridad.

    Solo devuelve reglas con `account_type` definido (necesario para clasificar el P&L)
    y `match_text` no vacío. Orden: COMPANY antes que SECTOR; dentro de cada grupo, el
    `match_text` más largo primero (más específico gana).
    """
    rules = session.exec(
        select(CategoryRule).where(
            or_(
                (CategoryRule.scope == RuleScope.COMPANY)
                & (CategoryRule.company_id == company_id),
                (CategoryRule.scope == RuleScope.SECTOR)
                & (CategoryRule.sector == sector),
            )
        )
    ).all()
    usable = [r for r in rules if r.account_type is not None and normalize(r.match_text)]
    usable.sort(
        key=lambda r: (0 if r.scope == RuleScope.COMPANY else 1, -len(normalize(r.match_text)))
    )
    return usable


def match_rule(rules: list[CategoryRule], description: str) -> CategoryRule | None:
    """Primera regla (de la lista ya ordenada por prioridad) cuyo `match_text` aparece
    en la descripción normalizada. `None` si ninguna aplica."""
    norm_desc = normalize(description)
    if not norm_desc:
        return None
    for rule in rules:
        if normalize(rule.match_text) in norm_desc:
            return rule
    return None


def find_matching_rule(
    session: Session, company_id: int, sector: str, description: str
) -> CategoryRule | None:
    """Conveniencia para un solo lookup: carga las reglas y matchea una descripción."""
    return match_rule(load_rules(session, company_id, sector), description)
