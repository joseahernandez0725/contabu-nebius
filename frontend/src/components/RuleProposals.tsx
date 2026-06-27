import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  generateRuleProposals, applyRuleProposals, getCategories,
  type RuleProposal, type ChartOfAccounts,
} from "../api";
import { SECTION_LABEL, sectionFamily, FAMILY_STYLE } from "../accounting";

const SECTIONS = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"];

type Row = RuleProposal & { approved: boolean };

// Proponedor proactivo: la IA mira las transacciones y SUGIERE reglas (mergea marcas, salta
// gas/persona-a-persona). El dueño aprueba/edita/rechaza; al aplicar, se crean y se re-aplican.
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

  function setRow(i: number, patch: Partial<Row>) {
    setRows((rs) => rs && rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }

  const approvedCount = rows?.filter((r) => r.approved).length ?? 0;

  async function apply() {
    if (!rows) return;
    const approved = rows.filter((r) => r.approved);
    if (approved.length === 0) return;
    setApplying(true); setError(null);
    try {
      const res = await applyRuleProposals(
        approved.map((r) => ({ keyword: r.keyword, account_type: r.account_type, category: r.category })),
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
          {t("proposals.subtitle", { defaultValue: "ContaBu's AI scans your transactions and proposes rules for recurring merchants. Approve the ones you want." })}
        </p>
      </div>

      {error && <div className="text-sm text-rose-600">{error}</div>}
      {note && <div className="text-sm text-brand-green bg-brand-green/10 rounded-lg px-3 py-2">{note}</div>}

      {rows == null ? (
        <button
          onClick={generate}
          disabled={loading}
          className="px-4 py-2.5 rounded-xl font-semibold text-white shadow-sm disabled:opacity-60"
          style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
        >
          {loading
            ? t("proposals.analyzing", { defaultValue: "AI is analyzing your transactions…" })
            : t("proposals.generate", { defaultValue: "Suggest rules with AI" })}
        </button>
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
                  const style = FAMILY_STYLE[sectionFamily(r.account_type)];
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
                        <div className="font-medium text-slate-800 uppercase flex items-center gap-2">
                          <span className={`inline-block size-2 shrink-0 rounded-full ${style.dot}`} />
                          {r.keyword}
                        </div>
                        {r.covers.length > 1 && (
                          <div className="text-[11px] text-slate-400 mt-0.5">
                            {t("proposals.merges", { defaultValue: "merges" })}: {r.covers.join(", ")}
                          </div>
                        )}
                      </td>
                      <td className="px-2 py-2.5 align-top">
                        <select
                          value={r.category}
                          onChange={(e) => setRow(i, { category: e.target.value, account_type: catToType[e.target.value] ?? r.account_type })}
                          className={`rounded-lg border border-slate-200 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-brand-green/40 ${style.text}`}
                        >
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
              disabled={applying || approvedCount === 0}
              className="px-4 py-2.5 rounded-xl font-semibold text-white shadow-sm disabled:opacity-60"
              style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
            >
              {applying
                ? t("proposals.applying", { defaultValue: "Applying…" })
                : t("proposals.apply", { defaultValue: "Approve {{n}} rules", n: approvedCount })}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
