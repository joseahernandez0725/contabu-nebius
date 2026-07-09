"""
ContaBu — harness de evaluación del CEREBRO de categorización (FASE 8).

Mide con NÚMEROS (no "a ojo") la calidad del motor sobre el SET DE ORO etiquetado
por un humano que conoce el negocio. Replica el motor REAL en el mismo orden que
`ingestion.ingest_csv`:

    regla COMPANY/SECTOR  →  banda de monto (super/gasolinera)  →  IA línea por línea
    →  pase de CONSISTENCIA (mismo comercio → misma categoría)
    →  forzado de revisión en ambiguos  →  detector de signo

…y compara la predicción contra la etiqueta correcta para producir un BOLETÍN:
acierto de account_type / category / subcategory, tasa de revisión, calibración de la
confianza, consistencia por comercio, Δ del P&L y top de confusiones.

Uso:
    python eval_engine.py                      # corrida completa (usa IA) → boletín + guarda artefactos
    python eval_engine.py --no-ai              # solo reglas+bandas (prueba de plomería, sin gastar Gemini)
    python eval_engine.py --baseline           # además guarda la corrida como LÍNEA BASE
    python eval_engine.py --compare baseline.json   # compara contra una corrida previa (¿subió o bajó?)

Privacidad: lee `private/gold_set_template.csv` y escribe en `private/eval_runs/` (gitignored).
"""

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from difflib import SequenceMatcher

from sqlmodel import Session

from ai import categorize_transaction
from company_profile import format_profile_facts, p2p_counterparty
from db import engine
from ingestion import AI_CONCURRENCY, needs_human_review, unify_merchant_categorization
from models import AccountType, RuleScope
from rules import amount_band_category, classify_transfer, load_rules, match_rule

HERE = os.path.dirname(os.path.abspath(__file__))
GOLD_DEFAULT = os.path.join(HERE, "private", "gold_set_template.csv")
RUNS_DIR = os.path.join(HERE, "private", "eval_runs")

# Secciones del P&L que SUMAN / RESTAN (NON_PL se excluye del P&L).
_INCOME = {AccountType.REVENUE, AccountType.OTHER_INCOME}
_EXPENSE = {AccountType.COGS, AccountType.OPERATING_EXPENSE, AccountType.OTHER_EXPENSE}


# --------------------------------------------------------------------------- #
# Carga del set de oro
# --------------------------------------------------------------------------- #
def load_gold(path: str) -> list[dict]:
    """Lee el set de oro etiquetado. Cada fila: date, description, amount(con signo),
    correct_account_type, correct_category, correct_payee."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        at = (r.get("correct_account_type") or "").strip()
        cat = (r.get("correct_category") or "").strip()
        if not at or not cat:
            continue  # fila sin etiquetar → no se puede medir
        out.append({
            "description": (r.get("description") or "").strip(),
            "signed": Decimal(str(r.get("amount") or "0")),
            "gold_at": at,
            "gold_cat": cat,
            "gold_payee": (r.get("correct_payee") or "").strip(),
        })
    return out


# --------------------------------------------------------------------------- #
# La ENTREVISTA simulada (FASE 9 Paso 5-6)
# --------------------------------------------------------------------------- #
def interview_profile_from_gold(gold: list[dict]) -> dict:
    """Simula la ENTREVISTA del bookkeeper para la medición: los hechos que el dueño daría —
    y el dueño es LA MISMA persona que etiquetó el set de oro, así que se derivan de sus etiquetas
    (no es fuga del eval: es la información que la entrevista recogería en producción).

    Solo hechos a NIVEL NEGOCIO (ronda 1) + roles de personas P2P (ronda 2). Determinista.
    En producción los hechos viajan igual: `Company.profile` → `format_profile_facts` → prompt."""
    profile: dict = {}
    cats = {g["gold_cat"] for g in gold}
    if "Subcontractors" in cats:
        profile["uses_subcontractors"] = "yes"
    if "Owner Draw" in cats or "Owner Contribution" in cats:
        profile["personal_expenses_mixed"] = "yes"
    people: dict = {}
    for g in gold:
        name = p2p_counterparty(g["description"])
        if not name:
            continue
        if g["gold_cat"] == "Subcontractors":
            role = "subcontractor"
        elif g["gold_at"] == "REVENUE":
            role = "customer"
        elif g["gold_cat"] in ("Owner Draw", "Owner Contribution"):
            role = "owner"
        else:
            continue
        people[name] = {"name": name.title(), "role": role}
    if people:
        profile["people"] = people
    return profile


# --------------------------------------------------------------------------- #
# Motor real (mismo orden que ingestion): regla → banda → IA → consistencia
# --------------------------------------------------------------------------- #
def run_engine(session: Session, gold: list[dict], sector: str, *,
               sector_only: bool, use_ai: bool, profile: dict | None = None) -> list[dict]:
    """Corre el motor sobre el set de oro y devuelve la predicción por fila.
    `sector_only`: usar SOLO reglas de sector (cerebro de una empresa nueva, sin reglas propias).
    `profile`: perfil de la entrevista — sus hechos viajan al prompt igual que en producción."""
    # parsed con la MISMA forma que ingestion (description en el índice 1) para reusar el pase de consistencia.
    parsed = [(None, g["description"], g["signed"], None, None) for g in gold]

    rules = load_rules(session, company_id=-1, sector=sector)  # company_id=-1 → no hay reglas COMPANY
    if sector_only:
        rules = [r for r in rules if r.scope == RuleScope.SECTOR]

    rule_hits: dict[int, object] = {}
    band_hits: dict[int, tuple] = {}
    transfer_hits: dict[int, tuple] = {}
    ai_indices: list[int] = []
    for idx, g in enumerate(gold):
        rule = match_rule(rules, g["description"])
        if rule is not None:
            rule_hits[idx] = rule
            continue
        band = amount_band_category(g["description"], float(g["signed"]), sector=sector)  # umbrales default
        if band is not None:
            band_hits[idx] = band
            continue
        transfer = classify_transfer(g["description"])  # transferencia entre cuentas → NON_PL
        if transfer is not None:
            transfer_hits[idx] = transfer
            continue
        ai_indices.append(idx)

    ai_cats: dict[int, object] = {}
    profile_facts = format_profile_facts(profile)  # "" sin perfil → prompt idéntico al de la base
    if use_ai and ai_indices:
        def _one(idx: int):
            try:
                return idx, categorize_transaction(
                    gold[idx]["description"], float(gold[idx]["signed"]), sector, profile_facts)
            except Exception:
                return idx, None
        with ThreadPoolExecutor(max_workers=AI_CONCURRENCY) as pool:
            for idx, cat in pool.map(_one, ai_indices):
                if cat is not None:
                    ai_cats[idx] = cat

    # Pase de CONSISTENCIA intra-set (idéntico a producción).
    consistency_adjusted = unify_merchant_categorization(parsed, ai_cats) if ai_cats else 0

    preds: list[dict] = []
    for idx, g in enumerate(gold):
        signed = g["signed"]
        is_inflow = signed > 0
        p = {"source": "none", "at": None, "cat": None, "sub": None,
             "confidence": None, "needs_review": False}
        if idx in rule_hits:
            r = rule_hits[idx]
            p.update(source="rule", at=r.account_type, cat=r.category,
                     sub=(r.match_text or "").upper(), confidence=1.0, needs_review=False)
        elif idx in band_hits:
            at, cat, near = band_hits[idx]  # near = zona gris (cerca del umbral) → revisión
            p.update(source="band", at=at, cat=cat, sub=None,
                     confidence=0.5 if near else 0.85, needs_review=near)
        elif idx in transfer_hits:
            at, cat = transfer_hits[idx]  # transferencia entre cuentas → NON_PL, determinista
            p.update(source="transfer", at=at, cat=cat, sub=None, confidence=1.0, needs_review=False)
        elif idx in ai_cats:
            c = ai_cats[idx]
            p.update(source="ai", at=c.account_type, cat=c.category,
                     sub=(c.subcategory or "").upper() or None,
                     confidence=c.confidence, needs_review=c.needs_review)

        # Forzado de revisión en ambiguos por naturaleza (solo lo de la IA), igual que ingestion.
        if p["source"] == "ai" and needs_human_review(g["description"]):
            p["needs_review"] = True
            if p["confidence"] is None or p["confidence"] > 0.5:
                p["confidence"] = 0.5
        # Detector de signo: dirección del banco contradice la sección del P&L → revisar.
        if p["at"] is not None and p["at"] != AccountType.NON_PL:
            if (p["at"] in _INCOME) != is_inflow:
                p["needs_review"] = True

        p["at"] = p["at"].value if p["at"] is not None else None
        preds.append(p)

    return preds, {"by_rule": len(rule_hits), "by_band": len(band_hits),
                   "by_transfer": len(transfer_hits),
                   "by_ai": len(ai_cats), "ai_failed": len(ai_indices) - len(ai_cats),
                   "consistency_adjusted": consistency_adjusted,
                   "rules_loaded": len(rules)}


# --------------------------------------------------------------------------- #
# Métricas del boletín
# --------------------------------------------------------------------------- #
def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _approx(a: str, b: str) -> bool:
    """Coincidencia aproximada de comercio: contención o similitud ≥ 0.8 (texto libre ruidoso)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or (len(na) >= 3 and len(nb) >= 3 and (na in nb or nb in na)):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.8


def _pnl(rows: list[tuple[str, Decimal]]) -> dict:
    """Calcula el P&L (como lo haría SQL): rows = [(account_type, magnitud_positiva)]."""
    agg = defaultdict(Decimal)
    for at, mag in rows:
        if at:
            agg[at] += mag
    revenue = agg.get("REVENUE", Decimal(0))
    cogs = agg.get("COGS", Decimal(0))
    opex = agg.get("OPERATING_EXPENSE", Decimal(0))
    oth_inc = agg.get("OTHER_INCOME", Decimal(0))
    oth_exp = agg.get("OTHER_EXPENSE", Decimal(0))
    net = revenue + oth_inc - cogs - opex - oth_exp
    return {"revenue": revenue, "cogs": cogs, "opex": opex,
            "other_income": oth_inc, "other_expense": oth_exp,
            "gross_profit": revenue - cogs, "net_income": net}


def scorecard(gold: list[dict], preds: list[dict], engine_stats: dict) -> dict:
    n = len(gold)
    at_ok = cat_ok = 0
    uncategorized = 0
    review_n = 0
    sub_exact = sub_approx = sub_total = 0
    by_section_total = Counter()
    by_section_ok = Counter()
    confusions = Counter()
    calib = defaultdict(lambda: [0, 0])  # bucket -> [aciertos_categoria, total]
    merchant_groups: dict[str, list[int]] = defaultdict(list)

    for i, (g, p) in enumerate(zip(gold, preds)):
        gold_at, gold_cat = g["gold_at"], g["gold_cat"]
        by_section_total[gold_at] += 1
        if p["at"] is None:
            uncategorized += 1
        if p["needs_review"]:
            review_n += 1

        at_hit = p["at"] == gold_at
        cat_hit = _norm(p["cat"]) == _norm(gold_cat) and p["cat"] is not None
        if at_hit:
            at_ok += 1
        if cat_hit:
            cat_ok += 1
            by_section_ok[gold_at] += 1
        else:
            confusions[(f"{gold_at}/{gold_cat}", f"{p['at']}/{p['cat']}")] += 1

        # Subcategoría (= payee): solo donde el oro tiene beneficiario (cheques/depósitos no tienen).
        if g["gold_payee"]:
            sub_total += 1
            if _norm(p["sub"]) == _norm(g["gold_payee"]) and p["sub"]:
                sub_exact += 1
                sub_approx += 1
            elif _approx(p["sub"] or "", g["gold_payee"]):
                sub_approx += 1

        # Calibración: bucket de confianza vs acierto de categoría.
        conf = p["confidence"]
        if conf is not None:
            b = ("0.9-1.0" if conf >= 0.9 else "0.8-0.9" if conf >= 0.8 else
                 "0.7-0.8" if conf >= 0.7 else "0.5-0.7" if conf >= 0.5 else "<0.5")
            calib[b][1] += 1
            if cat_hit:
                calib[b][0] += 1

        # Consistencia: agrupar por BENEFICIARIO DEL ORO (la verdad), no por la predicción.
        if g["gold_payee"]:
            merchant_groups[_norm(g["gold_payee"])].append(i)

    # P&L del oro vs del predicho (NON_PL y None se excluyen solos).
    pnl_gold = _pnl([(g["gold_at"], abs(g["signed"])) for g in gold])
    pnl_pred = _pnl([(p["at"], abs(g["signed"])) for g, p in zip(gold, preds)])
    net_g, net_p = pnl_gold["net_income"], pnl_pred["net_income"]
    pnl_delta = net_p - net_g
    pnl_delta_pct = (float(pnl_delta) / abs(float(net_g)) * 100) if net_g else None

    # Consistencia: % de comercios (≥2 apariciones) con UNA sola (account_type,category) predicha.
    inconsistent = []
    multi = 0
    for key, idxs in merchant_groups.items():
        if len(idxs) < 2:
            continue
        multi += 1
        labels = {(preds[i]["at"], preds[i]["cat"]) for i in idxs}
        if len(labels) > 1:
            inconsistent.append((gold[idxs[0]]["gold_payee"], sorted(f"{a}/{c}" for a, c in labels)))
    consistency_pct = (multi - len(inconsistent)) / multi * 100 if multi else None

    return {
        "n": n,
        "engine": engine_stats,
        "account_type_acc": round(at_ok / n * 100, 1),
        "category_acc": round(cat_ok / n * 100, 1),
        "uncategorized": uncategorized,
        "review_rate": round(review_n / n * 100, 1),
        "subcategory": {
            "n": sub_total,
            "exact_acc": round(sub_exact / sub_total * 100, 1) if sub_total else None,
            "approx_acc": round(sub_approx / sub_total * 100, 1) if sub_total else None,
        },
        "pnl": {
            "net_income_gold": float(net_g),
            "net_income_pred": float(net_p),
            "delta": float(pnl_delta),
            "delta_pct": round(pnl_delta_pct, 1) if pnl_delta_pct is not None else None,
        },
        "by_section": {
            sec: {"total": by_section_total[sec], "category_acc":
                  round(by_section_ok[sec] / by_section_total[sec] * 100, 1)}
            for sec in sorted(by_section_total)
        },
        "calibration": {b: {"category_acc": round(v[0] / v[1] * 100, 1), "n": v[1]}
                        for b, v in sorted(calib.items())},
        "consistency": {
            "merchants_multi": multi,
            "consistency_pct": round(consistency_pct, 1) if consistency_pct is not None else None,
            "inconsistent": inconsistent[:10],
        },
        "top_confusions": confusions.most_common(12),
    }


# --------------------------------------------------------------------------- #
# Impresión del boletín
# --------------------------------------------------------------------------- #
def _money(x: float) -> str:
    return f"${x:,.2f}"


def print_scorecard(sc: dict, *, baseline: dict | None = None):
    def d(key_path, val, fmt=lambda v: f"{v}"):
        """Delta contra baseline, si existe."""
        if not baseline:
            return ""
        b = baseline
        for k in key_path:
            b = (b or {}).get(k) if isinstance(b, dict) else None
        if b is None or not isinstance(val, (int, float)):
            return ""
        diff = val - b
        arrow = "▲" if diff > 0 else "▼" if diff < 0 else "="
        return f"  ({arrow} {diff:+.1f} vs base)"

    e = sc["engine"]
    print("\n" + "=" * 64)
    print(f"  BOLETÍN DEL CEREBRO — {sc['n']} transacciones del set de oro")
    print("=" * 64)
    print(f"  Resueltas por: regla={e['by_rule']}  banda={e['by_band']}  "
          f"IA={e['by_ai']}  (IA falló={e['ai_failed']})  reglas cargadas={e['rules_loaded']}")
    print(f"  Pase de consistencia ajustó: {e['consistency_adjusted']} filas")
    print("-" * 64)
    print(f"  Acierto account_type : {sc['account_type_acc']:5.1f}%{d(['account_type_acc'], sc['account_type_acc'])}")
    print(f"  Acierto category     : {sc['category_acc']:5.1f}%{d(['category_acc'], sc['category_acc'])}")
    sub = sc["subcategory"]
    if sub["n"]:
        print(f"  Acierto subcategory  : exacto {sub['exact_acc']:.1f}% · "
              f"aprox {sub['approx_acc']:.1f}%  (n={sub['n']} con beneficiario)")
    print(f"  Sin categorizar      : {sc['uncategorized']}")
    print(f"  Tasa de revisión     : {sc['review_rate']:5.1f}%{d(['review_rate'], sc['review_rate'])}")
    print("-" * 64)
    p = sc["pnl"]
    print("  Δ DEL P&L (utilidad neta):")
    print(f"    oro       = {_money(p['net_income_gold'])}")
    print(f"    predicho  = {_money(p['net_income_pred'])}")
    dp = f"{p['delta_pct']:+.1f}%" if p["delta_pct"] is not None else "n/a"
    print(f"    Δ         = {_money(p['delta'])}  ({dp})")
    print("-" * 64)
    print("  Acierto de categoría POR SECCIÓN:")
    for sec, v in sc["by_section"].items():
        print(f"    {sec:18s} {v['category_acc']:5.1f}%  (n={v['total']})")
    print("-" * 64)
    print("  CALIBRACIÓN (confianza → acierto real de categoría):")
    for b, v in sc["calibration"].items():
        print(f"    conf {b:8s}  acierto {v['category_acc']:5.1f}%  (n={v['n']})")
    print("-" * 64)
    c = sc["consistency"]
    if c["consistency_pct"] is not None:
        print(f"  CONSISTENCIA por comercio: {c['consistency_pct']:.1f}% "
              f"({c['merchants_multi']} comercios con ≥2 apariciones)")
        for payee, labels in c["inconsistent"]:
            print(f"    ⚠ {payee}: {labels}")
    print("-" * 64)
    print("  TOP CONFUSIONES (oro → predicho):")
    for (g, pr), cnt in sc["top_confusions"]:
        print(f"    {cnt:2d}×  {g}   →   {pr}")
    print("=" * 64 + "\n")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Harness de evaluación del cerebro (FASE 8)")
    ap.add_argument("--gold", default=GOLD_DEFAULT, help="ruta del set de oro etiquetado")
    ap.add_argument("--sector", default="construction")
    ap.add_argument("--no-ai", action="store_true", help="no llamar a Gemini (prueba de plomería)")
    ap.add_argument("--with-company-rules", action="store_true",
                    help="incluir reglas COMPANY (por defecto: solo SECTOR = cerebro base)")
    ap.add_argument("--baseline", action="store_true", help="guardar esta corrida como línea base")
    ap.add_argument("--compare", default=None, help="json de una corrida previa para comparar")
    ap.add_argument("--interview", action="store_true",
                    help="simular la ENTREVISTA (FASE 9): hechos del dueño derivados de sus "
                         "etiquetas del oro → viajan al prompt como en producción")
    args = ap.parse_args()

    gold = load_gold(args.gold)
    if not gold:
        raise SystemExit(f"No hay filas etiquetadas en {args.gold}")
    print(f"Set de oro: {len(gold)} transacciones etiquetadas.")

    profile = interview_profile_from_gold(gold) if args.interview else None
    if profile:
        n_people = len(profile.get("people", {}))
        print(f"Entrevista simulada: {list(profile.keys())} · {n_people} personas con rol confirmado")
        print(format_profile_facts(profile))

    with Session(engine) as session:
        preds, stats = run_engine(
            session, gold, args.sector,
            sector_only=not args.with_company_rules, use_ai=not args.no_ai, profile=profile,
        )
    sc = scorecard(gold, preds, stats)

    baseline = None
    if args.compare and os.path.exists(args.compare):
        with open(args.compare) as f:
            baseline = json.load(f)
    print_scorecard(sc, baseline=baseline)

    # Guardar artefactos (gitignored).
    os.makedirs(RUNS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = ("noai" if args.no_ai else "full") + ("_interview" if args.interview else "")
    score_path = os.path.join(RUNS_DIR, f"{ts}_{mode}_scorecard.json")
    with open(score_path, "w") as f:
        json.dump(sc, f, indent=2, ensure_ascii=False, default=str)
    # CSV etiquetado (oro vs predicho) para auditar dónde falla.
    labeled_path = os.path.join(RUNS_DIR, f"{ts}_{mode}_labeled.csv")
    with open(labeled_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["description", "amount", "gold_at", "pred_at", "at_ok",
                    "gold_cat", "pred_cat", "cat_ok", "gold_payee", "pred_sub",
                    "source", "confidence", "needs_review"])
        for g, p in zip(gold, preds):
            w.writerow([g["description"], str(g["signed"]), g["gold_at"], p["at"],
                        int(p["at"] == g["gold_at"]),
                        g["gold_cat"], p["cat"], int(_norm(p["cat"]) == _norm(g["gold_cat"])),
                        g["gold_payee"], p["sub"], p["source"], p["confidence"], p["needs_review"]])
    print(f"Artefactos:\n  {score_path}\n  {labeled_path}")

    if args.baseline:
        base_path = os.path.join(RUNS_DIR, "baseline.json")
        with open(base_path, "w") as f:
            json.dump(sc, f, indent=2, ensure_ascii=False, default=str)
        print(f"  Línea base guardada → {base_path}")


if __name__ == "__main__":
    main()
