import { useEffect, useMemo, useState, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  generateRuleProposals, applyRuleProposals, importRules, getCategories, validateRuleProposal,
  type ChartOfAccounts,
} from "../api";
import { SECTION_LABEL, sectionFamily, FAMILY_STYLE } from "../accounting";

const SECTIONS = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"];

type Row = {
  keyword: string;
  account_type: string;   // "" si la sección no se pudo resolver (import)
  category: string;
  covers: string[];       // comercios que mergea (proponedor); vacío en import
  approved: boolean;
  status?: string;        // import: ok | exists | duplicate | needs_section
  // Validación contra los datos reales (el "guardián", FASE 9 Paso 3) — solo filas del proponedor.
  covered?: number;       // de su comercio: cuántas transacciones cubre
  missed?: number;        // de su comercio: cuántas se le escapan (sobre-específico)
  conflicts?: number;     // de OTROS comercios: cuántas caza en falso (sub-específico → bloquea)
  conflict_examples?: string[];
  validating?: boolean;   // re-validando un keyword editado
};

// Estado de las filas importadas → color del badge.
const STATUS_STYLE: Record<string, string> = {
  exists: "text-amber-700 bg-amber-100",
  duplicate: "text-slate-500 bg-slate-100",
  needs_section: "text-rose-700 bg-rose-100",
};

// Reglas para el dueño: la IA SUGIERE (proponedor) o el usuario IMPORTA su archivo. Aprueba/edita/aplica.
export default function RuleProposals() {
  const { t } = useTranslation();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [chart, setChart] = useState<ChartOfAccounts>({});
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { getCategories().then(setChart).catch(() => {}); }, []);

  const catToType = useMemo(() => {
    const m: Record<string, string> = {};
    for (const [at, cats] of Object.entries(chart)) for (const c of cats) m[c] = at;
    return m;
  }, [chart]);

  async function generate() {
    setLoading(true); setError(null); setNote(null);
    try {
      const props = await generateRuleProposals();
      setRows(props.map((p) => ({ ...p, approved: true })));
      if (props.length === 0)
        setNote(t("proposals.none", { defaultValue: "No new rules to propose — your rulebook already covers the recurring merchants." }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setLoading(false);
    }
  }

  async function onImportFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // permite re-subir el mismo archivo
    if (!file) return;
    setLoading(true); setError(null); setNote(null);
    try {
      const { rules } = await importRules(file);
      setRows(rules.map((r) => ({
        keyword: r.keyword, account_type: r.account_type, category: r.category,
        covers: [], approved: r.status === "ok", status: r.status,
      })));
      if (rules.length === 0)
        setNote(t("proposals.importEmpty", { defaultValue: "Couldn't read any rules from that file." }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setLoading(false);
    }
  }

  function setRow(i: number, patch: Partial<Row>) {
    setRows((rs) => rs && rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }

  // Re-valida un keyword EDITADO contra los datos (cobertura/colisión) al salir del campo.
  async function revalidate(i: number, row: Row) {
    setRow(i, { validating: true });
    try {
      const v = await validateRuleProposal(row.keyword, row.covers);
      setRow(i, { ...v, validating: false });
    } catch {
      setRow(i, { validating: false });
    }
  }

  // Solo se aplican las aprobadas CON sección resuelta y SIN conflictos: un keyword que caza otros
  // comercios no se puede aprobar sin editarlo (un falso match miscategoriza en silencio).
  const applicable = rows?.filter((r) => r.approved && r.account_type && !(r.conflicts ?? 0)) ?? [];

  async function apply() {
    if (applicable.length === 0) return;
    setApplying(true); setError(null);
    try {
      const res = await applyRuleProposals(
        applicable.map((r) => ({ keyword: r.keyword, account_type: r.account_type, category: r.category })),
      );
      setNote(t("proposals.applied", {
        defaultValue: "Created {{c}} rules and aligned {{a}} transactions.",
        c: res.created, a: res.applied,
      }));
      setRows(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setApplying(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-bold text-slate-900">
          {t("proposals.title", { defaultValue: "Suggested rules" })}
        </h2>
        <p className="text-sm text-slate-500 mt-1">
          {t("proposals.subtitle", { defaultValue: "Let ContaBu's AI propose rules from your transactions, or import your own rules file — the AI reads the columns." })}
        </p>
      </div>

      {error && <div className="text-sm text-rose-600">{error}</div>}
      {note && <div className="text-sm text-brand-green bg-brand-green/10 rounded-lg px-3 py-2">{note}</div>}

      {rows == null ? (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={generate}
            disabled={loading}
            className="px-4 py-2.5 rounded-xl font-semibold text-white shadow-sm disabled:opacity-60"
            style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
          >
            {loading
              ? t("proposals.analyzing", { defaultValue: "AI is working…" })
              : t("proposals.generate", { defaultValue: "Suggest rules with AI" })}
          </button>
          <label className={`px-4 py-2.5 rounded-xl border border-slate-200 font-medium text-slate-700 ${loading ? "opacity-60" : "cursor-pointer hover:bg-slate-50"}`}>
            {t("proposals.import", { defaultValue: "Import my rules file" })}
            <input type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" className="hidden" onChange={onImportFile} disabled={loading} />
          </label>
        </div>
      ) : (
        <>
          <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-start text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-100">
                  <th className="px-3 py-2 w-8" />
                  <th className="px-2 py-2 font-medium text-start">{t("proposals.keyword", { defaultValue: "Keyword" })}</th>
                  <th className="px-2 py-2 font-medium text-start">{t("proposals.category", { defaultValue: "Category" })}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map((r, i) => {
                  const style = FAMILY_STYLE[sectionFamily(r.account_type || null)];
                  return (
                    <tr key={i} className={r.approved ? "" : "opacity-40"}>
                      <td className="px-3 py-2.5 align-top">
                        <input
                          type="checkbox"
                          checked={r.approved}
                          onChange={(e) => setRow(i, { approved: e.target.checked })}
                          className="size-4 accent-brand-green"
                        />
                      </td>
                      <td className="px-2 py-2.5 align-top">
                        {r.conflicts !== undefined ? (
                          // Fila del PROPONEDOR: keyword EDITABLE (para resolver conflictos) que se
                          // re-valida contra los datos al salir del campo.
                          <div className="flex items-center gap-2">
                            <span className={`inline-block size-2 shrink-0 rounded-full ${style.dot}`} />
                            <input
                              value={r.keyword}
                              onChange={(e) => setRow(i, { keyword: e.target.value })}
                              onBlur={() => void revalidate(i, { ...r, keyword: r.keyword })}
                              className={`w-full font-medium uppercase rounded-lg border px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-brand-green/40 ${
                                (r.conflicts ?? 0) > 0 ? "border-rose-300 text-rose-700" : "border-slate-200 text-slate-800"
                              }`}
                            />
                          </div>
                        ) : (
                          <div className="font-medium text-slate-800 uppercase flex items-center gap-2">
                            <span className={`inline-block size-2 shrink-0 rounded-full ${style.dot}`} />
                            {r.keyword}
                          </div>
                        )}
                        {/* El GUARDIÁN: evidencia contra los datos reales — el dueño aprueba con
                            números, no con fe ("cubre N · se le escapan M · K conflictos"). */}
                        {r.conflicts !== undefined && (
                          <div className="text-[11px] mt-1 flex flex-wrap items-center gap-1.5">
                            {r.validating ? (
                              <span className="text-slate-400">…</span>
                            ) : (
                              <>
                                <span className={`rounded px-1 py-0.5 ${
                                  (r.covered ?? 0) > 0 ? "text-brand-green/90 bg-brand-green/10" : "text-rose-700 bg-rose-100"
                                }`}>
                                  {t("proposals.covered", { defaultValue: "covers {{n}}", n: r.covered ?? 0 })}
                                </span>
                                {(r.missed ?? 0) > 0 && (
                                  <span className="text-amber-700 bg-amber-100 rounded px-1 py-0.5">
                                    {t("proposals.missed", { defaultValue: "misses {{n}}", n: r.missed })}
                                  </span>
                                )}
                                {(r.conflicts ?? 0) > 0 && (
                                  <span
                                    className="text-rose-700 bg-rose-100 rounded px-1 py-0.5 cursor-help"
                                    title={(r.conflict_examples ?? []).join("\n")}
                                  >
                                    {t("proposals.conflicts", { defaultValue: "{{n}} conflicts — edit the keyword", n: r.conflicts })}
                                  </span>
                                )}
                              </>
                            )}
                          </div>
                        )}
                        {r.covers.length > 1 && (
                          <div className="text-[11px] text-slate-400 mt-0.5">
                            {t("proposals.merges", { defaultValue: "merges" })}: {r.covers.join(", ")}
                          </div>
                        )}
                        {r.status && r.status !== "ok" && (
                          <div className="mt-0.5">
                            <span className={`text-[10px] uppercase tracking-wide rounded px-1 py-0.5 ${STATUS_STYLE[r.status] ?? "text-slate-500 bg-slate-100"}`}>
                              {t(`proposals.status.${r.status}`, { defaultValue: r.status.replace("_", " ") })}
                            </span>
                          </div>
                        )}
                      </td>
                      <td className="px-2 py-2.5 align-top">
                        <select
                          value={r.category}
                          onChange={(e) => setRow(i, { category: e.target.value, account_type: catToType[e.target.value] ?? r.account_type })}
                          className={`rounded-lg border px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-brand-green/40 ${r.account_type ? `border-slate-200 ${style.text}` : "border-rose-300 text-rose-700"}`}
                        >
                          {!r.account_type && <option value={r.category}>{r.category} — ?</option>}
                          {SECTIONS.filter((s) => chart[s]?.length).map((s) => (
                            <optgroup key={s} label={SECTION_LABEL[s] ?? s}>
                              {chart[s].map((c) => (
                                <option key={c} value={c}>{c}</option>
                              ))}
                            </optgroup>
                          ))}
                        </select>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="flex gap-2">
            <button
              onClick={() => setRows(null)}
              className="px-4 py-2.5 rounded-xl border border-slate-200 font-medium text-slate-600"
            >
              {t("proposals.cancel", { defaultValue: "Cancel" })}
            </button>
            <button
              onClick={apply}
              disabled={applying || applicable.length === 0}
              className="px-4 py-2.5 rounded-xl font-semibold text-white shadow-sm disabled:opacity-60"
              style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
            >
              {applying
                ? t("proposals.applying", { defaultValue: "Applying…" })
                : t("proposals.apply", { defaultValue: "Approve {{n}} rules", n: applicable.length })}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
