import { useState } from "react";
import { useTranslation } from "react-i18next";

// EL FILTRO POR FECHAS de la Hoja (Jose, 19-oct; Movimientos): "el filtro está bien, pero falta poder
// filtrar por fechas". Va junto al de cuentas y se combina con él, con la búsqueda y con "Pendientes".
// Atajos (este mes · mes anterior · este trimestre · este año · año anterior · todo) y un rango propio.
// Un atajo se guarda COMO ATAJO (no como fechas): "este mes" sigue siendo este mes al cambiar de mes.
// Las fechas son ISO (`YYYY-MM-DD`, como `transaction.date`): se comparan como texto, sin zonas horarias.

export type DatePreset = "thisMonth" | "lastMonth" | "thisQuarter" | "thisYear" | "lastYear";
/** null = todas las fechas. */
export type DateFilter = { preset: DatePreset } | { preset: "custom"; from: string | null; to: string | null } | null;
export type DateRange = { from: string | null; to: string | null };

const PRESETS: DatePreset[] = ["thisMonth", "lastMonth", "thisQuarter", "thisYear", "lastYear"];

const iso = (y: number, m: number, d: number) => {
  const x = new Date(y, m, d);   // normaliza meses fuera de rango (m = -1 → diciembre del año anterior)
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
};

/** Las fechas que cubre el filtro, con HOY del navegador (día 0 del mes siguiente = último del mes). */
export function dateRangeOf(f: DateFilter, today = new Date()): DateRange | null {
  if (!f) return null;
  if (f.preset === "custom") return f.from || f.to ? { from: f.from, to: f.to } : null;
  const y = today.getFullYear(), m = today.getMonth(), q = Math.floor(m / 3) * 3;
  switch (f.preset) {
    case "thisMonth": return { from: iso(y, m, 1), to: iso(y, m + 1, 0) };
    case "lastMonth": return { from: iso(y, m - 1, 1), to: iso(y, m, 0) };
    case "thisQuarter": return { from: iso(y, q, 1), to: iso(y, q + 3, 0) };
    case "thisYear": return { from: iso(y, 0, 1), to: iso(y, 11, 31) };
    case "lastYear": return { from: iso(y - 1, 0, 1), to: iso(y - 1, 11, 31) };
  }
}

export const inRange = (date: string, r: DateRange | null) =>
  !r || ((!r.from || date >= r.from) && (!r.to || date <= r.to));

// Fecha SIEMPRE en formato de EE.UU., como la columna Fecha de la Hoja (es dato contable).
const us = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "2-digit", year: "numeric" });

function rangeText(r: DateRange | null, t: (k: string, o?: any) => string): string {
  if (!r) return "";
  if (r.from && r.to) return `${us(r.from)} – ${us(r.to)}`;
  return r.from ? t("sheet.dates.since", { d: us(r.from) }) : t("sheet.dates.until", { d: us(r.to as string) });
}

export function DateFilterButton({ value, onChange, inputStyle }: {
  value: DateFilter;
  onChange: (f: DateFilter) => void;
  inputStyle: React.CSSProperties;   // el mismo estilo de los otros controles de la barra
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const range = dateRangeOf(value);
  const [from, setFrom] = useState(value?.preset === "custom" ? value.from ?? "" : "");
  const [to, setTo] = useState(value?.preset === "custom" ? value.to ?? "" : "");
  const active = range != null;
  const label = !active ? t("sheet.dates.all")
    : value!.preset === "custom" ? rangeText(range, t) : t(`sheet.dates.${value!.preset}`);

  const pick = (f: DateFilter) => { onChange(f); setOpen(false); };
  const apply = () => {
    if (!from && !to) { pick(null); return; }
    const [a, b] = from && to && from > to ? [to, from] : [from, to];   // al revés: se ordenan
    pick({ preset: "custom", from: a || null, to: b || null });
  };
  const row: React.CSSProperties = { display: "flex", gap: 8, alignItems: "center", width: "100%", padding: "5px 8px",
    fontSize: 13, cursor: "pointer", border: 0, background: "transparent", textAlign: "start", borderRadius: 6 };
  const date: React.CSSProperties = { ...inputStyle, padding: "4px 6px", fontSize: 12.5, width: "100%" };

  return (
    <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        title={active ? rangeText(range, t) : undefined}
        style={{ ...inputStyle, cursor: "pointer", display: "flex", alignItems: "center", gap: 6,
                 borderColor: active ? "#1A8754" : "#d5d0c4", color: active ? "#1A8754" : undefined,
                 fontWeight: active ? 600 : 400, paddingInlineEnd: active ? 28 : undefined }}>
        <span aria-hidden>📅</span>{label}
        {!active && <span style={{ fontSize: 10 }}>▾</span>}
      </button>
      {/* El filtro activo se quita con una × (Jose, 19-oct). */}
      {active && (
        <button type="button" onClick={() => onChange(null)} aria-label={t("sheet.dates.clear")} title={t("sheet.dates.clear")}
          style={{ position: "absolute", insetInlineEnd: 6, border: 0, background: "transparent", cursor: "pointer",
                   color: "#1A8754", fontSize: 15, lineHeight: 1, padding: "0 3px" }}>×</button>
      )}
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 40 }} />
          <div role="group" aria-label={t("sheet.dates.label")}
            style={{ position: "absolute", top: "100%", left: 0, marginTop: 4, zIndex: 41, minWidth: 270,
                     background: "#fff", border: "1px solid #d5d0c4", borderRadius: 8,
                     boxShadow: "0 8px 24px rgba(15,23,42,.12)", padding: 6 }}>
            {PRESETS.map((p) => (
              <button key={p} type="button" onClick={() => pick({ preset: p })}
                style={{ ...row, fontWeight: value?.preset === p ? 700 : 400, color: value?.preset === p ? "#1A8754" : undefined }}>
                <span style={{ flex: 1 }}>{t(`sheet.dates.${p}`)}</span>
                <span style={{ fontSize: 11.5, color: "#8a958f", fontVariantNumeric: "tabular-nums" }}>{rangeText(dateRangeOf({ preset: p }), t)}</span>
              </button>
            ))}
            <button type="button" onClick={() => pick(null)}
              style={{ ...row, fontWeight: !active ? 700 : 400, color: !active ? "#1A8754" : undefined }}>
              {t("sheet.dates.all")}
            </button>
            <div style={{ borderTop: "1px solid #eee9de", margin: "4px 0" }} />
            <div style={{ padding: "4px 8px", fontSize: 12, fontWeight: 600, color: "#5b6b62" }}>{t("sheet.dates.custom")}</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, padding: "0 8px" }}>
              <label style={{ fontSize: 11, color: "#5b6b62" }}>
                {t("sheet.dates.from")}
                <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} style={date} />
              </label>
              <label style={{ fontSize: 11, color: "#5b6b62" }}>
                {t("sheet.dates.to")}
                <input type="date" value={to} onChange={(e) => setTo(e.target.value)} style={date}
                  onKeyDown={(e) => { if (e.key === "Enter") apply(); }} />
              </label>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", padding: "6px 8px 2px" }}>
              <button type="button" onClick={apply} disabled={!from && !to}
                style={{ border: "1px solid #1A8754", background: "#1A8754", color: "#fff", borderRadius: 6,
                         padding: "3px 12px", fontWeight: 600, fontSize: 12.5,
                         cursor: from || to ? "pointer" : "default", opacity: from || to ? 1 : 0.5 }}>
                {t("sheet.dates.apply")}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
