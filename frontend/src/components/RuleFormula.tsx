import { useTranslation } from "react-i18next";
import type { Rule } from "../api";

/**
 * VER COMO FÓRMULA (FASE 10, Movimientos · 8-oct-2026, elegido por Jose).
 *
 * El libro entero como UNA fórmula de Excel: la cadena de SI en el ORDEN REAL en que el motor prueba
 * las reglas (`order` = `rules.rule_order`: prioridad → con condiciones (fechas o monto) → la palabra más
 * larga → A–Z), terminando en "¿es una transferencia entre tus cuentas?" y, si nada coincide, Pendiente.
 *
 * Es solo una VISTA de las mismas reglas: no se ejecuta ni se escribe nada. Click en un SI = esa regla
 * se selecciona en la hoja. Marca las reglas que propuso el agente (`origin = "agent"`) y las que pierden
 * filas contra otra (`lost_to`).
 */

const MONTHS: Record<string, string[]> = {
  en: ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"],
  es: ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"],
};

const SECTION_INK: Record<string, string> = {
  REVENUE: "#15803d", OTHER_INCOME: "#15803d", COGS: "#b45309", OPERATING_EXPENSE: "#1d4ed8",
  OTHER_EXPENSE: "#7c3aed", NON_PL: "#475569",
};

const C = {
  fn: "#0f766e",        // SI / IF
  op: "#7c2d12",        // CONTIENE / Y / ENTRE
  field: "#334155",     // DESCRIPCIÓN / FECHA
  str: "#9a3412",       // "STARBUCKS"
  punct: "#94a3b8",     // ( ; )
};

export default function RuleFormula({ rules, selectedId, onPick, onClose }: {
  rules: Rule[];
  selectedId?: number | null;
  onPick: (id: number) => void;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").startsWith("es") ? "es" : "en";
  const sep = lang === "es" ? ";" : ",";
  const day = (iso: string) => {
    const [y, m, d] = iso.split("-").map(Number);
    return `${lang === "es" ? d : String(d).padStart(2, "0")}-${MONTHS[lang][m - 1]}-${y}`;
  };
  // Solo las que corren (tienen lugar en el orden); las demás no deciden nada.
  const chain = rules.filter((r) => r.order != null).sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const idle = rules.filter((r) => r.order == null && r.scope === "COMPANY");

  const w = (text: string, color: string, bold = false) => (
    <span style={{ color, fontWeight: bold ? 700 : 400 }}>{text}</span>
  );
  const F = (k: string, d: string) => t(`rules.formula.${k}`, { defaultValue: d });

  const condition = (r: Rule) => {
    const parts = [<span key="d">{w(F("description", "DESCRIPTION"), C.field, true)} {w(F("contains", "CONTAINS"), C.op, true)} {w(`"${r.match_text}"`, C.str, true)}</span>];
    if (r.valid_from && r.valid_to) {
      parts.push(<span key="w"> {w(F("and", "AND"), C.op, true)} {w(F("date", "DATE"), C.field, true)} {w(F("between", "BETWEEN"), C.op, true)} {w(day(r.valid_from), C.str)} {w(F("and", "AND"), C.op, true)} {w(day(r.valid_to), C.str)}</span>);
    } else if (r.valid_from) {
      parts.push(<span key="w"> {w(F("and", "AND"), C.op, true)} {w(F("date", "DATE"), C.field, true)} {w("≥", C.op, true)} {w(day(r.valid_from), C.str)}</span>);
    } else if (r.valid_to) {
      parts.push(<span key="w"> {w(F("and", "AND"), C.op, true)} {w(F("date", "DATE"), C.field, true)} {w("≤", C.op, true)} {w(day(r.valid_to), C.str)}</span>);
    }
    // MONTO (8-oct): la magnitud del movimiento, inclusivo.
    const money = (n: number) => "$" + n.toLocaleString("en-US", { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
    if (r.amount_min != null && r.amount_max != null) {
      parts.push(<span key="a"> {w(F("and", "AND"), C.op, true)} {w(F("amount", "AMOUNT"), C.field, true)} {w(F("between", "BETWEEN"), C.op, true)} {w(money(r.amount_min), C.str)} {w(F("and", "AND"), C.op, true)} {w(money(r.amount_max), C.str)}</span>);
    } else if (r.amount_min != null) {
      parts.push(<span key="a"> {w(F("and", "AND"), C.op, true)} {w(F("amount", "AMOUNT"), C.field, true)} {w("≥", C.op, true)} {w(money(r.amount_min), C.str)}</span>);
    } else if (r.amount_max != null) {
      parts.push(<span key="a"> {w(F("and", "AND"), C.op, true)} {w(F("amount", "AMOUNT"), C.field, true)} {w("≤", C.op, true)} {w(money(r.amount_max), C.str)}</span>);
    }
    return parts;
  };

  const indent = (i: number) => `${Math.min(i, 10) * 1.1}em`;
  const chip = (text: string, bg: string, fg: string, title?: string) => (
    <span title={title} style={{ marginInlineStart: 8, background: bg, color: fg, borderRadius: 999, padding: "1px 8px",
                                 fontSize: 11, fontWeight: 700, fontFamily: "system-ui, sans-serif", whiteSpace: "nowrap" }}>{text}</span>
  );

  return (
    <div style={{ border: "1px solid #d5d0c4", borderRadius: 12, background: "#fffdf7", marginBottom: 10,
                  display: "flex", flexDirection: "column", maxHeight: "48vh", minHeight: 160 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "10px 14px 8px", borderBottom: "1px solid #eee9de" }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700, color: "#0f172a", fontSize: 14 }}>
            <span style={{ color: "#1A8754", fontStyle: "italic", marginInlineEnd: 6 }}>ƒ</span>
            {F("title", "This book's formula")}
          </div>
          <div style={{ fontSize: 12, color: "#5b6b62", marginTop: 2 }}>
            {F("subtitle", "Read-only. This is how the sheet decides each transaction, top to bottom: the first IF that matches wins. Click an IF to select that rule.")}
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", fontSize: 11.5, color: "#5b6b62" }}>
          {chip(F("legendAgent", "proposed by the agent"), "#ede9fe", "#5b21b6")}
          {chip(F("legendClash", "loses rows to another rule"), "#fef3c7", "#92400e")}
          {chip(F("legendPriority", "manual priority"), "#dcfce7", "#166534")}
        </div>
        <button onClick={onClose} aria-label={F("close", "Close")} title={F("close", "Close")}
          style={{ border: 0, background: "transparent", cursor: "pointer", color: "#8a958f", fontSize: 18, lineHeight: 1 }}>×</button>
      </div>

      <div style={{ overflow: "auto", padding: "10px 14px 14px",
                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", fontSize: 13, lineHeight: 1.75 }}>
        {chain.map((r, i) => {
          const selected = r.id === selectedId;
          const loses = !!r.lost_to?.length;
          return (
            <div key={r.id} role="button" tabIndex={0} onClick={() => onPick(r.id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(r.id); } }}
              title={F("pick", "Select this rule in the sheet")}
              style={{ paddingInlineStart: indent(i), cursor: "pointer", borderRadius: 6, whiteSpace: "nowrap",
                       background: selected ? "rgba(26,135,84,.12)" : loses ? "rgba(251,191,36,.12)" : "transparent",
                       outline: selected ? "1px solid rgba(26,135,84,.45)" : "none" }}>
              {w(i === 0 ? "=" : "", C.fn, true)}{w(F("if", "IF"), C.fn, true)}{w("( ", C.punct)}
              {condition(r)}
              {w(` ${sep} `, C.punct)}
              <span style={{ color: SECTION_INK[r.account_type ?? ""] ?? "#0f172a", fontWeight: 700 }}>"{r.category}"</span>
              {w(` ${sep}`, C.punct)}
              <span style={{ fontFamily: "system-ui, sans-serif" }}>
                <span style={{ marginInlineStart: 10, color: "#94a3b8", fontSize: 11.5 }}>#{r.order}</span>
                {r.priority != null && chip(`P${r.priority}`, "#dcfce7", "#166534", F("legendPriority", "manual priority"))}
                {r.origin === "agent" && chip(F("agent", "agent"), "#ede9fe", "#5b21b6", F("legendAgent", "proposed by the agent"))}
                {loses && chip(`⚠ ${r.lost_to!.map((c) => t("rules.losesTo", { defaultValue: "loses {{n}} to {{k}}", n: c.rows, k: c.match_text })).join(" · ")}`,
                               "#fef3c7", "#92400e")}
              </span>
            </div>
          );
        })}
        {/* Lo que ninguna regla cubre: el motor prueba la transferencia entre cuentas y, si no, Pendiente. */}
        <div style={{ paddingInlineStart: indent(chain.length), whiteSpace: "nowrap" }}>
          {w(chain.length ? "" : "=", C.fn, true)}{w(F("if", "IF"), C.fn, true)}{w("( ", C.punct)}
          {w(F("isTransfer", "IS A TRANSFER BETWEEN YOUR ACCOUNTS"), C.field, true)}
          {w(` ${sep} `, C.punct)}
          <span style={{ color: SECTION_INK.NON_PL, fontWeight: 700 }}>"{F("transfer", "Transfer")}"</span>
          {w(` ${sep} `, C.punct)}
          <span style={{ color: "#991b1b", fontWeight: 700, background: "rgba(220,38,38,.08)", borderRadius: 4, padding: "0 4px" }}>
            {F("pending", "PENDING")}
          </span>
          {w(" " + ")".repeat(chain.length + 1), C.punct, true)}
        </div>
        {idle.length > 0 && (
          <div style={{ marginTop: 10, fontFamily: "system-ui, sans-serif", fontSize: 12, color: "#8a958f" }}>
            {t("rules.formula.idle", { defaultValue: "Not in the formula (no P&L section): {{k}}", k: idle.map((r) => r.match_text).join(", ") })}
          </div>
        )}
      </div>
    </div>
  );
}
