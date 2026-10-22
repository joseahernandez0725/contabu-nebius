import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { money } from "../api";

/**
 * LA CONDICIÓN DE MONTO, EN UN SOLO EDITOR (FASE 10, 21-oct-2026 — pedido de Jose, diseño sobre un
 * dibujo). Antes eran dos columnas de la hoja de Reglas (`Min $` / `Max $`), cada una un número
 * suelto, sin decir qué significaba el combo ("200 / vacío" = ¿al menos 200? ¿hasta 200?). Ahora es
 * UNA columna ("Monto") con una etiqueta legible, y al editarla se abre este diálogo chico con
 * CUATRO MODOS — el modelo de datos no cambia (`amount_min`/`amount_max`, inclusivos, igual que
 * siempre lee `rules.in_amount` en el backend): esto es solo una capa de traducción.
 *
 * - **Cualquier monto** — `amount_min = amount_max = null`.
 * - **Hasta $X** — `amount_max = X` (inclusive), `amount_min = null`.
 * - **Más de $X** — `amount_min = X + 0.01` (EXCLUYE el borde: a centavos, "> X" y ">= X+0.01" son
 *   el mismo conjunto), `amount_max = null`. Al releer, se resta 0.01 para volver a mostrar "Más de $X".
 * - **Entre $X y $Y** — `amount_min = X`, `amount_max = Y`, los DOS inclusivos (sin el +0.01: acá el
 *   borde sí cuenta, a propósito — así lo pidió Jose).
 *
 * Nada de esto toca el motor (`rules.in_amount`, ya inclusivo en los dos lados) ni la exportación/
 * importación (siguen siendo las columnas "Min amount"/"Max amount" tal cual).
 */

export type AmountRange = { amount_min: number | null; amount_max: number | null };
export type AmountSibling = {
  id: number; category: string; subcategory: string | null;
  amount_min: number | null; amount_max: number | null;
};
type Mode = "any" | "upTo" | "moreThan" | "between";

/** "50" / "$1,200.50" → 50 / 1200.5. Vacío o ilegible (negativo incluido) → undefined: a diferencia
 *  de `RulesSheet.parseMoney`, acá un campo en blanco NUNCA es una condición válida por sí solo. */
function parseAmt(v: string): number | undefined {
  const s = v.trim().replace(/[$,\s]/g, "");
  if (!s || !/^\d+(\.\d{1,2})?$/.test(s)) return undefined;
  return Number(s);
}
const round2 = (n: number) => Math.round(n * 100) / 100;

function modeOf(r: AmountRange): Mode {
  if (r.amount_min == null && r.amount_max == null) return "any";
  if (r.amount_min == null) return "upTo";
  if (r.amount_max == null) return "moreThan";
  return "between";
}

/** La etiqueta legible de una condición — la misma lógica que usa la celda "Monto" de la hoja. */
export function amountLabel(r: AmountRange, t: (k: string, o?: any) => string): string {
  const mode = modeOf(r);
  if (mode === "any") return t("rules.amount.any", { defaultValue: "Any amount" });
  if (mode === "upTo") return t("rules.amount.upTo", { defaultValue: "Up to {{x}}", x: money(r.amount_max!) });
  if (mode === "moreThan") {
    return t("rules.amount.moreThan", { defaultValue: "More than {{x}}", x: money(round2(r.amount_min! - 0.01)) });
  }
  return t("rules.amount.between", {
    defaultValue: "Between {{x}} and {{y}}", x: money(r.amount_min!), y: money(r.amount_max!),
  });
}

// Límite inferior/superior "ordenable" de un rango (±Infinity = sin límite de ese lado).
const lo = (r: AmountRange) => r.amount_min ?? -Infinity;
const hi = (r: AmountRange) => r.amount_max ?? Infinity;
const overlapsRange = (a: AmountRange, b: AmountRange) => lo(a) <= hi(b) && lo(b) <= hi(a);

type Finding = { kind: "overlap" | "seamlessBelow" | "seamlessAbove" | "gapBelow" | "gapAbove";
                  sib: AmountSibling; gap?: number };

/** Contra las reglas de la MISMA palabra clave: ¿se cruza con alguna? ¿sigue justo después/antes de
 *  otra, sin huecos? ¿deja un hueco (esas filas quedarían Pendientes)? Solo informa — no bloquea. */
function findFit(current: AmountRange, siblings: AmountSibling[]): Finding[] {
  const findings: Finding[] = [];
  let nearestBelow: { sib: AmountSibling; gap: number } | null = null;  // sibling justo ANTES (termina antes de que current empiece)
  let nearestAbove: { sib: AmountSibling; gap: number } | null = null;  // sibling justo DESPUÉS (empieza después de que current termine)
  for (const sib of siblings) {
    if (overlapsRange(current, sib)) { findings.push({ kind: "overlap", sib }); continue; }
    if (isFinite(hi(sib)) && isFinite(lo(current)) && hi(sib) < lo(current)) {
      const gap = round2(lo(current) - hi(sib));
      if (!nearestBelow || hi(sib) > hi(nearestBelow.sib)) nearestBelow = { sib, gap };
    } else if (isFinite(lo(sib)) && isFinite(hi(current)) && lo(sib) > hi(current)) {
      const gap = round2(lo(sib) - hi(current));
      if (!nearestAbove || lo(sib) < lo(nearestAbove.sib)) nearestAbove = { sib, gap };
    }
  }
  if (nearestBelow) {
    findings.push({ kind: nearestBelow.gap <= 0.01 ? "seamlessBelow" : "gapBelow", sib: nearestBelow.sib, gap: nearestBelow.gap });
  }
  if (nearestAbove) {
    findings.push({ kind: nearestAbove.gap <= 0.01 ? "seamlessAbove" : "gapAbove", sib: nearestAbove.sib, gap: nearestAbove.gap });
  }
  return findings;
}

const label: React.CSSProperties = { fontSize: 12, fontWeight: 700, color: "#5b6b62", marginBottom: 4,
                                      textTransform: "uppercase", letterSpacing: ".04em" };
const field: React.CSSProperties = { width: "100%", padding: "8px 10px", borderRadius: 8,
                                      border: "1px solid #d5d0c4", fontSize: 14, background: "#fff" };

export default function AmountConditionEditor({ keyword, current, siblings, onClose, onSave }: {
  keyword: string;
  current: AmountRange;
  siblings: AmountSibling[];
  onClose: () => void;
  onSave: (next: AmountRange) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<Mode>(modeOf(current));
  const [a, setA] = useState(() => {
    const m = modeOf(current);
    if (m === "upTo") return String(current.amount_max);
    if (m === "moreThan") return String(round2(current.amount_min! - 0.01));
    if (m === "between") return String(current.amount_min);
    return "";
  });
  const [b, setB] = useState(() => (modeOf(current) === "between" ? String(current.amount_max) : ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const na = parseAmt(a);
  const nb = parseAmt(b);
  const backwards = mode === "between" && na != null && nb != null && na > nb;
  const incomplete = (mode === "upTo" || mode === "moreThan") && na === undefined
    || (mode === "between" && (na === undefined || nb === undefined));
  const next: AmountRange | null = incomplete || backwards ? null
    : mode === "any" ? { amount_min: null, amount_max: null }
    : mode === "upTo" ? { amount_min: null, amount_max: na! }
    : mode === "moreThan" ? { amount_min: round2(na! + 0.01), amount_max: null }
    : { amount_min: na!, amount_max: nb! };

  const findings = useMemo(() => (next ? findFit(next, siblings) : []), [next, siblings]);
  const canSave = !busy && next != null;

  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    document.addEventListener("keydown", esc, true);
    return () => document.removeEventListener("keydown", esc, true);
  }, [onClose]);

  async function submit() {
    if (!next) return;
    setBusy(true); setError(null);
    try { await onSave(next); }
    catch (e) { setError(String((e as Error)?.message ?? e)); setBusy(false); }
  }

  const MODES: { k: Mode; label: string }[] = [
    { k: "any", label: t("rules.amount.modeAny", { defaultValue: "Any" }) },
    { k: "upTo", label: t("rules.amount.modeUpTo", { defaultValue: "Up to" }) },
    { k: "moreThan", label: t("rules.amount.modeMoreThan", { defaultValue: "More than" }) },
    { k: "between", label: t("rules.amount.modeBetween", { defaultValue: "Between" }) },
  ];

  return createPortal(
    <div onMouseDown={onClose} style={{ position: "fixed", inset: 0, zIndex: 2500, background: "rgba(15,23,42,.28)",
      display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "10vh 16px", overflowY: "auto" }}>
      <div onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label={t("rules.amount.title", { defaultValue: "Amount condition" })}
        style={{ background: "#fff", borderRadius: 12, width: "min(420px, 100%)", padding: 20,
                 boxShadow: "0 24px 60px rgba(15,23,42,.28)" }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: "0 0 2px" }}>
          {t("rules.amount.title", { defaultValue: "Amount condition" })}
        </h2>
        <p style={{ fontSize: 12.5, color: "#5b6b62", margin: "0 0 14px" }}>
          {t("rules.amount.subtitle", { defaultValue: "For “{{k}}”", k: keyword })}
        </p>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
          {MODES.map((m) => (
            <button key={m.k} onClick={() => setMode(m.k)}
              style={{ padding: "7px 8px", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer",
                       border: mode === m.k ? "1px solid #1A8754" : "1px solid #d5d0c4",
                       background: mode === m.k ? "rgba(26,135,84,.1)" : "#fff",
                       color: mode === m.k ? "#15803d" : "#334155" }}>
              {m.label}
            </button>
          ))}
        </div>

        {(mode === "upTo" || mode === "moreThan" || mode === "between") && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
            <div style={{ flex: 1 }}>
              <div style={label}>
                {mode === "between" ? t("rules.amount.from", { defaultValue: "From" }) : t("rules.amount.amount", { defaultValue: "Amount" })}
              </div>
              <input value={a} onChange={(e) => setA(e.target.value)} placeholder="0.00" inputMode="decimal" style={field} />
            </div>
            {mode === "between" && (
              <div style={{ flex: 1 }}>
                <div style={label}>{t("rules.amount.to", { defaultValue: "To" })}</div>
                <input value={b} onChange={(e) => setB(e.target.value)} placeholder="0.00" inputMode="decimal" style={field} />
              </div>
            )}
          </div>
        )}
        {backwards && (
          <p style={{ fontSize: 12.5, color: "#991b1b", marginTop: 8 }}>
            {t("rules.amountBackwards", { defaultValue: "The minimum amount is above the maximum." })}
          </p>
        )}

        {next && (
          <div style={{ marginTop: 12, padding: "8px 10px", borderRadius: 8, background: "#FBF7EE", fontSize: 13 }}>
            <div style={{ fontWeight: 700, color: "#1A8754" }}>{amountLabel(next, t)}</div>
            {findings.length === 0 && (
              <p style={{ margin: "4px 0 0", color: "#7a8a80" }}>
                {t("rules.amount.noOtherRules", { defaultValue: "No other rules use “{{k}}” with an amount condition.", k: keyword })}
              </p>
            )}
            {findings.map((f, i) => (
              <p key={i} style={{ margin: "4px 0 0", color: f.kind === "overlap" || f.kind === "gapBelow" || f.kind === "gapAbove" ? "#b45309" : "#5b6b62" }}>
                {f.kind === "overlap" && t("rules.amount.overlaps", {
                  defaultValue: "Overlaps “{{label}}” ({{cat}}): a transaction in between could match either rule.",
                  label: amountLabel(f.sib, t), cat: f.sib.subcategory || f.sib.category,
                })}
                {f.kind === "seamlessBelow" && t("rules.amount.seamlessBelow", {
                  defaultValue: "Picks up right after “{{label}}” ({{cat}}): no gaps or overlaps.",
                  label: amountLabel(f.sib, t), cat: f.sib.subcategory || f.sib.category,
                })}
                {f.kind === "seamlessAbove" && t("rules.amount.seamlessAbove", {
                  defaultValue: "“{{label}}” ({{cat}}) picks up right where this leaves off: no gaps or overlaps.",
                  label: amountLabel(f.sib, t), cat: f.sib.subcategory || f.sib.category,
                })}
                {f.kind === "gapBelow" && t("rules.amount.gapBelow", {
                  defaultValue: "Leaves a {{gap}} gap after “{{label}}” ({{cat}}): transactions there would stay Pending.",
                  gap: money(round2((f.gap ?? 0) - 0.01)), label: amountLabel(f.sib, t), cat: f.sib.subcategory || f.sib.category,
                })}
                {f.kind === "gapAbove" && t("rules.amount.gapAbove", {
                  defaultValue: "Leaves a {{gap}} gap before “{{label}}” ({{cat}}): transactions there would stay Pending.",
                  gap: money(round2((f.gap ?? 0) - 0.01)), label: amountLabel(f.sib, t), cat: f.sib.subcategory || f.sib.category,
                })}
              </p>
            ))}
          </div>
        )}

        {error && <p style={{ color: "#991b1b", fontSize: 13, marginTop: 10 }}>{error}</p>}

        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 18 }}>
          <span style={{ flex: 1 }} />
          <button onClick={onClose} style={{ border: "1px solid #d5d0c4", background: "#fff", borderRadius: 8,
            padding: "7px 14px", fontWeight: 600, cursor: "pointer" }}>{t("sheet.editorAddCancel")}</button>
          <button onClick={() => void submit()} disabled={!canSave}
            style={{ border: "1px solid #1A8754", background: "#1A8754", color: "#fff", borderRadius: 8,
                     padding: "7px 16px", fontWeight: 700, cursor: canSave ? "pointer" : "default",
                     opacity: canSave ? 1 : 0.45 }}>
            {t("rules.amount.save", { defaultValue: "Save" })}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
