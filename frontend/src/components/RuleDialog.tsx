import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import {
  addCustomCategory, createRule, previewRule, money,
  type ChartOfAccounts, type RulePreview,
} from "../api";
import { sectionsFor, setEditorChart, type MoneyDirection } from "./CategoryEditor";
import { GhostInput, ghostRest } from "./gridKit";

/**
 * CREAR REGLA, en una ventana dentro de la Hoja (FASE 10, Movimientos · 4-oct-2026).
 *
 * Por qué (Jose: "pienso como usuario… no me parece que se esté haciendo bien"):
 * - Antes ponía la descripción COMPLETA del banco como palabra clave (fecha, ORIG ID, TRACE#…) y la
 *   regla solo cazaba ESE movimiento. Ahora el backend SUGIERE una palabra limpia (`PAYROLL SERVICE`).
 * - Antes había que irse a la pestaña Reglas y la regla se creaba sola al elegir la categoría, sin ver
 *   qué iba a cazar. Ahora se ve EN VIVO cuántos movimientos caza y cuáles, y se crea con un botón.
 * - Las secciones para una categoría nueva se ofrecen según el dinero entre o salga, con una línea
 *   que dice qué va en cada una (así "Account" no termina en Ingresos con un pago de nómina).
 */

type SeedRow = { id: number; description: string; description_extra?: string | null; is_inflow?: boolean | null };

const SECTION_COLOR: Record<string, string> = {
  REVENUE: "#15803d", COGS: "#b45309", OPERATING_EXPENSE: "#0369a1",
  OTHER_INCOME: "#15803d", OTHER_EXPENSE: "#991b1b", NON_PL: "#5b6b62",
};

export default function RuleDialog({ seed, chart, onClose, onCreated, onChartChanged, onOpenRules }: {
  seed: SeedRow | null;
  chart: ChartOfAccounts;
  onClose: () => void;
  onCreated: (rule: { id: number; keyword: string; applied: number }) => void;
  onChartChanged: (chart: ChartOfAccounts) => void;
  onOpenRules: (keyword: string) => void;
}) {
  const { t } = useTranslation();
  const [keyword, setKeyword] = useState("");
  const [preview, setPreview] = useState<RulePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [category, setCategory] = useState<{ sec: string; name: string } | null>(null);
  const [q, setQ] = useState("");
  const [subcategory, setSubcategory] = useState("");
  const [newCat, setNewCat] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [localChart, setLocalChart] = useState<ChartOfAccounts>(chart);
  const kwRef = useRef<HTMLInputElement>(null);
  const firstLoad = useRef(true);

  // Primera carga: la palabra clave SUGERIDA para la fila (sin fechas, IDs ni números de rastreo).
  useEffect(() => {
    let alive = true;
    (seed ? previewRule({ transaction_id: seed.id }) : Promise.resolve(null))
      .then((p) => { if (!alive) return; if (p) { setKeyword(p.keyword); setPreview(p); } })
      .catch(() => {})
      .finally(() => { if (alive) { setLoading(false); setTimeout(() => kwRef.current?.select(), 0); } });
    return () => { alive = false; };
  }, [seed]);

  // Mientras escribes la palabra clave: qué cazaría (con una pausa corta para no pedir en cada tecla).
  useEffect(() => {
    if (firstLoad.current) { firstLoad.current = false; if (seed) return; }
    const k = keyword.trim();
    if (k.length < 2) { setPreview(null); return; }
    const id = setTimeout(() => {
      previewRule({ keyword: k, transaction_id: seed?.id }).then(setPreview).catch(() => {});
    }, 250);
    return () => clearTimeout(id);
  }, [keyword, seed]);

  const direction: MoneyDirection = seed?.is_inflow === true ? "in" : seed?.is_inflow === false ? "out"
    : preview && preview.matches ? (preview.outflow >= preview.inflow ? "out" : "in") : null;
  const sections = sectionsFor(direction);
  const secName = (sec: string) => t(`sheet.section.${sec}`);

  const options = useMemo(() => {
    const s = q.trim().toLowerCase();
    return sections.map(({ sec, unlikely }) => ({
      sec, unlikely,
      names: (localChart[sec] ?? []).filter((n) => !s || n.toLowerCase().includes(s)),
    })).filter((g) => g.names.length > 0);
  }, [sections, localChart, q]);

  const unlikelyPicked = !!category && !!sections.find((x) => x.sec === category.sec)?.unlikely;
  const k = keyword.trim();
  // La subcategoría es OBLIGATORIA (Jose, 4-oct): es el nombre limpio que se ve en los libros.
  const sub = subcategory.trim();
  const canCreate = !busy && k.length >= 2 && !!category && sub.length > 0 && !preview?.duplicate;

  async function createCategory(sec: string) {
    const name = q.trim();
    if (!name) return;
    setBusy(true); setError(null);
    try {
      await addCustomCategory(sec, name);
      const next = { ...localChart, [sec]: [...(localChart[sec] ?? []), name] };
      setLocalChart(next); setEditorChart(next); onChartChanged(next);
      setCategory({ sec, name }); setNewCat(false); setQ("");
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally { setBusy(false); }
  }

  async function submit() {
    if (!canCreate || !category) return;
    setBusy(true); setError(null);
    try {
      const r = await createRule({ match_text: k, account_type: category.sec, category: category.name,
                                   subcategory: sub });
      onCreated({ id: r.id, keyword: k, applied: r.applied });
    } catch (e) {
      setError((e as Error).message === "dup"
        ? t("rules.dup", { defaultValue: "There is already a rule with that keyword." })
        : String((e as Error).message));
      setBusy(false);
    }
  }

  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    document.addEventListener("keydown", esc, true);
    return () => document.removeEventListener("keydown", esc, true);
  }, [onClose]);

  const label: React.CSSProperties = { fontSize: 12, fontWeight: 700, color: "#5b6b62", marginBottom: 4,
                                        textTransform: "uppercase", letterSpacing: ".04em" };
  const field: React.CSSProperties = { width: "100%", padding: "8px 10px", borderRadius: 8,
                                        border: "1px solid #d5d0c4", fontSize: 14, background: "#fff" };

  return createPortal(
    <div onMouseDown={onClose} style={{ position: "fixed", inset: 0, zIndex: 2500, background: "rgba(15,23,42,.28)",
      display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "6vh 16px", overflowY: "auto" }}>
      <div onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label={t("rules.dialog.title")}
        style={{ background: "#fff", borderRadius: 12, width: "min(640px, 100%)", padding: 20,
                 boxShadow: "0 24px 60px rgba(15,23,42,.28)" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4 }}>
          <span style={{ fontStyle: "italic", fontWeight: 800, color: "#1A8754" }}>ƒ+</span>
          <h2 style={{ fontSize: 17, fontWeight: 700, margin: 0 }}>{t("rules.dialog.title")}</h2>
        </div>
        {seed && (
          <p style={{ fontSize: 12.5, color: "#5b6b62", margin: "0 0 14px", overflowWrap: "anywhere" }}>
            {t("rules.dialog.from")} <span style={{ color: "#0f172a" }}>{seed.description}{seed.description_extra ? ` - ${seed.description_extra}` : ""}</span>
          </p>
        )}

        {/* 1. PALABRA CLAVE + lo que caza en vivo */}
        <div style={label}>{t("rules.dialog.keyword")}</div>
        <input ref={kwRef} value={keyword} onChange={(e) => setKeyword(e.target.value.toUpperCase())}
          placeholder={loading ? t("rules.dialog.suggesting") : t("rules.keywordHint", { defaultValue: "e.g. HOME DEPOT" })}
          style={{ ...field, fontWeight: 700, letterSpacing: ".02em" }} />
        <p style={{ fontSize: 11.5, color: "#7a8a80", margin: "4px 0 0" }}>{t("rules.dialog.keywordHelp")}</p>

        <div style={{ marginTop: 8, padding: "8px 10px", borderRadius: 8, background: "#FBF7EE", fontSize: 13 }}>
          {k.length < 2 ? (
            <span style={{ color: "#7a8a80" }}>{t("rules.dialog.typeKeyword")}</span>
          ) : !preview ? (
            <span style={{ color: "#7a8a80" }}>…</span>
          ) : (
            <>
              <div style={{ fontWeight: 700, color: preview.matches ? "#1A8754" : "#991b1b" }}>
                {preview.matches
                  ? t("rules.dialog.catches", { count: preview.matches, pending: preview.pending })
                  : t("rules.dialog.catchesNone")}
              </div>
              {preview.duplicate && (
                <div style={{ color: "#991b1b", marginTop: 4 }}>
                  {t("rules.dialog.duplicate", { k: preview.duplicate.match_text, c: preview.duplicate.category })}
                </div>
              )}
              {seed && preview.seed_matched === false && (
                <div style={{ color: "#b45309", marginTop: 4 }}>{t("rules.dialog.missesSeed")}</div>
              )}
              {preview.manual > 0 && (
                <div style={{ color: "#7a5b00", marginTop: 4 }}>{t("rules.dialog.manualKept", { count: preview.manual })}</div>
              )}
              {preview.examples.length > 0 && (
                <ul style={{ margin: "6px 0 0", padding: 0, listStyle: "none", maxHeight: 132, overflowY: "auto" }}>
                  {preview.examples.map((e) => (
                    <li key={e.id} style={{ display: "flex", gap: 8, fontSize: 12, padding: "2px 0", color: "#334155" }}>
                      <span style={{ flex: "none", color: "#7a8a80", fontVariantNumeric: "tabular-nums" }}>
                        {new Date(e.date + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "2-digit" })}
                      </span>
                      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.description}</span>
                      <span style={{ flex: "none", fontVariantNumeric: "tabular-nums", color: e.is_inflow ? "#15803d" : "#0f172a" }}>
                        {(e.is_inflow ? "" : "−") + money(e.amount)}
                      </span>
                      <span style={{ flex: "none", width: 110, textAlign: "end", overflow: "hidden", textOverflow: "ellipsis",
                                     whiteSpace: "nowrap", color: e.category ? "#5b6b62" : "#991b1b" }}>
                        {e.source === "manual" ? "✎ " : ""}{e.category ?? t("sheet.pending", { defaultValue: "Pending" })}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>

        {/* 2. CATEGORÍA, con las secciones según el dinero */}
        <div style={{ ...label, marginTop: 16 }}>
          {t("rules.dialog.category")}
          {direction && <span style={{ fontWeight: 600, textTransform: "none", letterSpacing: 0, marginInlineStart: 8, color: "#7a8a80" }}>
            {t(direction === "out" ? "rules.dialog.moneyOut" : "rules.dialog.moneyIn")}
          </span>}
        </div>
        {category && !newCat ? (
          <div style={{ display: "flex", alignItems: "center", gap: 10, ...field }}>
            <span style={{ fontWeight: 700 }}>{category.name}</span>
            <span style={{ fontSize: 12, fontWeight: 600, color: SECTION_COLOR[category.sec] }}>{secName(category.sec)}</span>
            <button onClick={() => setCategory(null)} style={{ marginInlineStart: "auto", border: 0, background: "transparent",
              color: "#1A8754", fontWeight: 600, cursor: "pointer" }}>{t("rules.dialog.change")}</button>
          </div>
        ) : (
          <div style={{ border: "1px solid #d5d0c4", borderRadius: 8, overflow: "hidden" }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("sheet.editorPlaceholder")}
              style={{ ...field, border: 0, borderBottom: "1px solid #e3ded2", borderRadius: 0 }} />
            {!newCat ? (
              <>
                <div style={{ maxHeight: 190, overflowY: "auto" }}>
                  {options.map((g) => (
                    <div key={g.sec}>
                      <div style={{ padding: "5px 10px 2px", fontSize: 10.5, fontWeight: 700, letterSpacing: ".06em",
                                    textTransform: "uppercase", background: "#FBF7EE",
                                    color: g.unlikely ? "#9a9384" : "#5b6b62" }}>
                        {secName(g.sec)}{g.unlikely && ` · ${t(direction === "out" ? "sheet.unlikelyOut" : "sheet.unlikelyIn")}`}
                      </div>
                      {g.names.map((n) => (
                        <button key={n} onClick={() => setCategory({ sec: g.sec, name: n })}
                          style={{ display: "block", width: "100%", textAlign: "start", border: 0, background: "transparent",
                                   padding: "6px 12px", fontSize: 13.5, cursor: "pointer",
                                   color: g.unlikely ? "#7a8a80" : "#0f172a" }}
                          onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(26,135,84,.08)"; }}
                          onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>
                          {n}
                        </button>
                      ))}
                    </div>
                  ))}
                  {options.length === 0 && <div style={{ padding: "8px 12px", fontSize: 12.5, color: "#7a8a80" }}>
                    {t("rules.dialog.noMatchCat", { q: q.trim() })}</div>}
                </div>
                <button onClick={() => setNewCat(true)}
                  style={{ display: "block", width: "100%", textAlign: "start", border: 0, borderTop: "1px solid #e3ded2",
                           background: "#FBF7EE", padding: "8px 12px", fontSize: 13, fontWeight: 700, color: "#1A8754", cursor: "pointer" }}>
                  {q.trim() ? t("sheet.editorAddNamed", { q: q.trim() }) : t("sheet.editorAdd")}
                </button>
              </>
            ) : (
              <div style={{ padding: "8px 10px", background: "#FBF7EE" }}>
                {!q.trim() ? (
                  <div style={{ fontSize: 12.5, color: "#5b6b62" }}>{t("sheet.editorAddType")}</div>
                ) : (
                  <>
                    <div style={{ fontSize: 12.5, color: "#5b6b62", marginBottom: 4 }}>
                      {t("rules.dialog.createUnder", { q: q.trim() })}
                    </div>
                    {sections.map(({ sec, unlikely }) => (
                      <button key={sec} disabled={busy} onClick={() => void createCategory(sec)}
                        style={{ display: "block", width: "100%", textAlign: "start", border: 0, background: "transparent",
                                 padding: "5px 6px", cursor: busy ? "wait" : "pointer", borderRadius: 6 }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(26,135,84,.08)"; }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>
                        <span style={{ fontSize: 13, fontWeight: 700, color: unlikely ? "#9a9384" : SECTION_COLOR[sec] }}>
                          + {secName(sec)}
                        </span>
                        {unlikely && <span style={{ fontSize: 11, color: "#9a9384" }}> · {t(direction === "out" ? "sheet.unlikelyOut" : "sheet.unlikelyIn")}</span>}
                        <span style={{ display: "block", fontSize: 11.5, color: "#7a8a80" }}>{t(`sheet.sectionHelp.${sec}`)}</span>
                      </button>
                    ))}
                  </>
                )}
                <button onClick={() => setNewCat(false)} style={{ marginTop: 4, border: 0, background: "transparent",
                  color: "#5b6b62", fontSize: 12, cursor: "pointer" }}>{t("sheet.editorAddCancel")}</button>
              </div>
            )}
          </div>
        )}
        {unlikelyPicked && (
          <p style={{ fontSize: 12.5, color: "#b45309", margin: "6px 0 0" }}>
            ⚠ {t(direction === "out" ? "rules.dialog.warnOut" : "rules.dialog.warnIn", { s: secName(category!.sec) })}
          </p>
        )}

        {/* 3. SUBCATEGORÍA (obligatoria). La palabra clave se sugiere en gris: Tab la pone; al escribir,
            lo que falta de ella sigue en gris (autocompletar). */}
        <div style={{ ...label, marginTop: 16 }}>{t("rules.dialog.subcategory")}</div>
        <GhostInput value={subcategory} onChange={setSubcategory} suggestion={k || null}
          onKeyDown={(e, accept) => {
            if (accept && (e.key === "Tab" || e.key === "Enter") && !e.shiftKey) { e.preventDefault(); setSubcategory(accept); }
          }}
          style={field} />
        <p style={{ fontSize: 11.5, color: "#7a8a80", margin: "4px 0 0" }}>
          {t("rules.dialog.subcategoryHelp")}
          {k.length >= 2 && (!sub || ghostRest(subcategory, k)) && <> {t("rules.dialog.subcategoryTab")}</>}
        </p>

        {error && <p style={{ color: "#991b1b", fontSize: 13, marginTop: 10 }}>{error}</p>}

        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 18 }}>
          <button onClick={() => onOpenRules(k)} style={{ border: 0, background: "transparent", color: "#5b6b62",
            fontSize: 12.5, textDecoration: "underline", cursor: "pointer" }}>{t("rules.dialog.openRules")}</button>
          <span style={{ flex: 1 }} />
          <button onClick={onClose} style={{ border: "1px solid #d5d0c4", background: "#fff", borderRadius: 8,
            padding: "7px 14px", fontWeight: 600, cursor: "pointer" }}>{t("sheet.editorAddCancel")}</button>
          <button onClick={() => void submit()} disabled={!canCreate}
            title={!sub && k.length >= 2 && category ? t("rules.dialog.needSub") : undefined}
            style={{ border: "1px solid #1A8754", background: "#1A8754", color: "#fff", borderRadius: 8,
                     padding: "7px 16px", fontWeight: 700, cursor: canCreate ? "pointer" : "default",
                     opacity: canCreate ? 1 : 0.45 }}>
            {preview?.matches
              ? t("rules.dialog.create", { count: preview.matches })
              : t("rules.dialog.createPlain")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
