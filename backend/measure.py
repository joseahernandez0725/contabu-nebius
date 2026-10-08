"""
ContaBu — LA MEDICIÓN de la fórmula del libro y su LÍNEA BASE SIN IA (FASE 11, Auditoría / IA ·
4-oct-2026). Ver `docs/FASE 11.md` → Actualización 3-oct → "📏 La medición".

La pregunta (la que hace el jurado de Nebius): ¿las filas que propone el agente reproducen lo que el
bookkeeper corrigió a mano, sin romper lo que estaba bien?

- **Separación por tiempo:** se aprende de las correcciones de movimientos ANTERIORES a la fecha de
  corte y se mide con las POSTERIORES. Así no se mide memorización.
- **La línea base (sin IA):** por cada huella de comercio corregida a mano 2 o más veces hacia la
  misma categoría, una fila "palabra clave = ese comercio → esa categoría". Es lo que haría alguien
  contando a mano. **El agente se gana su lugar solo si reproduce MÁS correcciones que esto, con
  IGUAL o MENOS filas cambiadas mal.**
- Todo se cuenta aquí, con el motor real (`formula.decide`), nunca con el modelo.

Uso:
    .venv/bin/python measure.py --demo                 # siembra los 3 libros del demo en una
                                                       # transacción, mide y la DESHACE (no deja nada)
    .venv/bin/python measure.py --company 12           # un libro existente (solo lectura)
    .venv/bin/python measure.py --demo --cutoff 2026-08-15
"""

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlmodel import Session, select

import formula
from formula import BookContext, FormulaRow
from models import CategorizationChange, Company, Transaction
from rules import merchant_signature, suggest_keyword

DEFAULT_CUTOFF = date(2026, 9, 1)   # demo: julio–agosto para aprender, septiembre para medir
MIN_REPEATS = 2                      # la línea base pide al menos 2 correcciones iguales

# Casos de NOTA plantados en los datos del demo (`demo_data.DEMO_NOTES`). Lo esperado se escribe
# AQUÍ, antes de correr al agente: qué movimientos tienen que terminar dónde. La línea base, por
# construcción, no puede resolverlos (no sabe de vacaciones): ahí está el valor propio del agente.
NOTE_CASES = {
    "Rivera HVAC LLC": {
        "note": "vacation in Europe Aug 3–17",
        "contains": "STARBUCKS",
        "from": date(2026, 8, 3), "to": date(2026, 8, 17),
        "expect": ("NON_PL", "Owner Draw"),
    },
}


@dataclass
class Correction:
    """Un movimiento corregido a mano: la VERDAD es como lo dejó el bookkeeper."""
    tx: Transaction
    truth: tuple                      # (account_type, category)
    changes: int                      # filas `via=manual` del historial sobre este movimiento


@dataclass
class Score:
    corrected: int = 0                # movimientos corregidos a mano en el periodo de medición
    correction_rows: int = 0          # filas del historial de esos movimientos
    reproduced: int = 0               # movimientos que la fórmula deja directo en la verdad
    reproduced_rows: int = 0          # correcciones que se habrían evitado
    wrong: int = 0                    # filas que la fórmula mueve a una categoría equivocada
    newly_categorized: int = 0        # Pendientes que la fórmula categorizaría (sin verdad: informativo)
    wrong_examples: list = field(default_factory=list)


def corrections(session: Session, company_id: int) -> list[Correction]:
    """Los movimientos que el bookkeeper corrigió a mano y siguen así (`source="manual"`)."""
    rows = session.exec(select(CategorizationChange).where(
        CategorizationChange.company_id == company_id, CategorizationChange.via == "manual")).all()
    per_tx = Counter(r.transaction_id for r in rows)
    out = []
    for tx_id, n in per_tx.items():
        tx = session.get(Transaction, tx_id)
        if tx is None or tx.source != "manual" or tx.account_type is None or not tx.category:
            continue
        out.append(Correction(tx=tx, truth=(tx.account_type.value, tx.category), changes=n))
    return out


def baseline_rows(learn: list[Correction]) -> list[FormulaRow]:
    """LA LÍNEA BASE SIN IA: una fila por huella de comercio corregida ≥ MIN_REPEATS veces hacia la
    MISMA categoría (y sin empate con otra). Sin ventana, sin prioridad: lo más simple que funciona."""
    by_merchant: dict[str, list[Correction]] = defaultdict(list)
    for c in learn:
        sig = merchant_signature(c.tx.description)
        if sig:
            by_merchant[sig].append(c)
    rows = []
    for sig, group in sorted(by_merchant.items()):
        counts = Counter(c.truth for c in group).most_common()
        (truth, n), runner_up = counts[0], (counts[1][1] if len(counts) > 1 else 0)
        if n < MIN_REPEATS or n == runner_up:
            continue
        example = next(c for c in group if c.truth == truth).tx
        keyword = suggest_keyword(example.description, example.description_extra) or sig
        rows.append(FormulaRow(
            keyword=keyword, account_type=truth[0], category=truth[1],
            explanation=f"baseline: corrected {n}x to {truth[1]}"))
    return rows


def score(ctx: BookContext, rows: list[FormulaRow], test: list[Correction], cutoff: date) -> Score:
    """Examina `rows` contra el periodo de medición con el motor real."""
    rules = formula.with_rows(ctx.rules, rows, ctx.company.id)
    s = Score(corrected=len(test), correction_rows=sum(c.changes for c in test))
    for c in test:
        d = formula.decide(rules, c.tx)
        if (d.account_type, d.category) == c.truth:
            s.reproduced += 1
            s.reproduced_rows += c.changes
        elif d.source == "rule" and formula.decide(ctx.rules, c.tx) != d:
            # La fórmula nueva lo tomó y lo mandó a otro lado: habría que corregirlo igual (o peor).
            s.wrong += 1
            s.wrong_examples.append(f"{c.tx.description} → {d.category} (era {c.truth[1]})")
    corrected_ids = {c.tx.id for c in test}
    for t in ctx.txs:
        if t.date < cutoff or t.id in corrected_ids or t.source == "manual":
            continue
        before, after = formula.decide(ctx.rules, t), formula.decide(rules, t)
        if before == after:
            continue
        if before.source is None:
            s.newly_categorized += 1           # era Pendiente: no hay verdad contra qué comparar
        else:
            s.wrong += 1                       # estaba decidido y aceptado; la fórmula lo movería
            if len(s.wrong_examples) < 5:
                s.wrong_examples.append(f"{t.description} → {after.category} (era {before.category})")
    return s


def note_case(ctx: BookContext, rows: list[FormulaRow]) -> dict | None:
    """¿Las filas resuelven el caso de nota plantado del libro? 100 % de las esperadas y 0 de más."""
    case = NOTE_CASES.get(ctx.company.name)
    if case is None:
        return None
    rules = formula.with_rows(ctx.rules, rows, ctx.company.id)
    expected = {t.id for t in ctx.txs
                if case["contains"] in (t.description or "").upper() and case["from"] <= t.date <= case["to"]}
    hit = extra = 0
    for t in ctx.txs:
        if t.source == "manual":
            continue
        d = formula.decide(rules, t)
        goes = (d.account_type, d.category) == case["expect"]
        if t.id in expected and goes:
            hit += 1
        elif t.id not in expected and goes and formula.decide(ctx.rules, t) != d:
            extra += 1
    return {"note": case["note"], "expected": len(expected), "hit": hit, "extra": extra}


def measure_book(session: Session, company: Company, cutoff: date) -> dict:
    ctx = BookContext(session, company)
    all_corr = corrections(session, company.id)
    learn = [c for c in all_corr if c.tx.date < cutoff]
    test = [c for c in all_corr if c.tx.date >= cutoff]
    rows = baseline_rows(learn)
    checked = formula.check_rows([r.model_dump(mode="json") for r in rows], ctx)
    valid = [c.row for c in checked if c.ok]
    return {
        "book": company.name, "learn": len(learn), "test": len(test),
        "rows": [(r.keyword, r.category) for r in valid],
        "rejected": [(c.raw.get("keyword"), c.errors) for c in checked if not c.ok],
        "score": score(ctx, valid, test, cutoff),
        "note_case": note_case(ctx, valid),
    }


def _print(results: list[dict], cutoff: date) -> None:
    print(f"\nLÍNEA BASE SIN IA · corte {cutoff.isoformat()} (aprende antes, se mide desde ahí)\n")
    tot = Score()
    for r in results:
        s = r["score"]
        print(f"■ {r['book']}  — correcciones a mano: {r['learn']} para aprender · {r['test']} para medir")
        for kw, cat in r["rows"]:
            print(f"    fila de la línea base: {kw!r} → {cat}")
        for kw, errs in r["rejected"]:
            print(f"    fila RECHAZADA por el validador: {kw!r} {errs}")
        print(f"    reproduce {s.reproduced}/{s.corrected} movimientos corregidos "
              f"({s.reproduced_rows}/{s.correction_rows} filas del historial) · cambiadas mal: {s.wrong} · "
              f"Pendientes que categorizaría: {s.newly_categorized}")
        for ex in s.wrong_examples:
            print(f"      ✗ {ex}")
        if r["note_case"]:
            n = r["note_case"]
            print(f"    caso de nota ({n['note']}): {n['hit']}/{n['expected']} filas esperadas · {n['extra']} de más")
        for k in ("corrected", "correction_rows", "reproduced", "reproduced_rows", "wrong", "newly_categorized"):
            setattr(tot, k, getattr(tot, k) + getattr(s, k))
    print(f"\nTOTAL · reproduce {tot.reproduced}/{tot.corrected} movimientos corregidos "
          f"({tot.reproduced_rows}/{tot.correction_rows} filas del historial) · cambiadas mal: {tot.wrong} · "
          f"Pendientes que categorizaría: {tot.newly_categorized}")
    print("→ Es el número que el agente tiene que SUPERAR: más reproducidas, igual o menos cambiadas mal.\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="siembra el demo en una transacción y la deshace")
    ap.add_argument("--company", type=int, help="id de un libro existente (solo lectura)")
    ap.add_argument("--cutoff", type=date.fromisoformat, default=DEFAULT_CUTOFF)
    args = ap.parse_args()
    if not args.demo and args.company is None:
        ap.error("usa --demo o --company ID")

    from db import engine
    with Session(engine) as session:
        try:
            if args.demo:
                import book_notes
                import demo_data
                from models import Firm, User
                owner = User(firebase_uid="__measure_demo__", email="measure@test.local")
                session.add(owner); session.flush()
                firm = Firm(name="MEASURE DEMO", owner_user_id=owner.id)
                session.add(firm); session.flush()
                books = demo_data.seed_demo_books(session, firm, owner)
                for b in books:
                    book_notes.seed_notes(session, b.id, demo_data.DEMO_NOTES.get(b.name, []))
                session.flush()
            else:
                book = session.get(Company, args.company)
                if book is None:
                    print(f"No existe el libro {args.company}", file=sys.stderr)
                    return 1
                books = [book]
            _print([measure_book(session, b, args.cutoff) for b in books], args.cutoff)
        finally:
            session.rollback()   # --demo no deja NADA en la base; --company nunca escribió
    return 0


if __name__ == "__main__":
    sys.exit(main())
