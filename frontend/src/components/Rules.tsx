import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getRules, updateRule, deleteRule, cleanupRules, getCategories,
  type ChartOfAccounts, type Rule,
} from "../api";
import { SECTION_LABEL, sectionFamily, FAMILY_STYLE } from "../accounting";
import RuleProposals from "./RuleProposals";

const SECTIONS = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"];

// Tabla de reglas editable: el "reglamento" (comercio → categoría) que el dueño/firma cura.
// Editar una regla re-aplica a las transacciones existentes (los libros se ajustan al instante).
export default function Rules() {
  const { t } = useTranslation();
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [chart, setChart] = useState<ChartOfAccounts>({});
  const [editing, setEditing] = useState<Rule | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    getRules().then(setRules).catch((e) => setError(e instanceof Error ? e.message : "Error"));
  useEffect(() => {
    load();
    getCategories().then(setChart).catch(() => {});
  }, []);

  const catToType = useMemo(() => {
    const m: Record<string, string> = {};
    for (const [at, cats] of Object.entries(chart)) for (const c of cats) m[c] = at;
    return m;
  }, [chart]);

  if (error) return <div className="text-sm text-rose-600">{error}</div>;
  if (!rules)
    return (
      <div className="flex justify-center py-16">
        <span className="loading loading-spinner loading-lg text-brand-green" />
      </div>
    );

  return (
    <div className="space-y-4">
      <RuleProposals />
      <div className="border-t border-slate-100" />
      <div>
        <h2 className="text-lg font-bold text-slate-900">
          {t("rules.title", { defaultValue: "Rules" })}
        </h2>
        <p className="text-sm text-slate-500 mt-1">
          {t("rules.subtitle", { defaultValue: "Your rulebook: each merchant maps to a category. Edit one and all its transactions update." })}
        </p>
        {rules.length > 0 && (
          <div className="flex items-center gap-3 mt-2 text-[11px] text-slate-500">
            {(["income", "expense", "nonpl"] as const).map((f) => (
              <span key={f} className="flex items-center gap-1.5">
                <span className={`inline-block size-2 rounded-full ${FAMILY_STYLE[f].dot}`} />
                {t(`rules.family.${f}`, { defaultValue: FAMILY_STYLE[f].label })}
              </span>
            ))}
          </div>
        )}
        {rules.some((r) => r.matches === 0) && (
          <button
            onClick={async () => {
              const { removed } = await cleanupRules();
              setNote(t("rules.cleaned", { defaultValue: "Removed {{count}} unused rules.", count: removed }));
              load();
            }}
            className="mt-2 text-sm text-brand-green font-medium hover:underline"
          >
            {t("rules.cleanup", { defaultValue: "Clean unused rules" })} ({rules.filter((r) => r.matches === 0).length})
          </button>
        )}
      </div>

      {note && <div className="text-sm text-brand-green bg-brand-green/10 rounded-lg px-3 py-2">{note}</div>}

      {rules.length === 0 ? (
        <div className="text-center py-12 text-slate-500">
          <p className="font-medium">{t("rules.empty", { defaultValue: "No rules yet." })}</p>
          <p className="text-sm mt-1">
            {t("rules.emptyHint", { defaultValue: "Correct a transaction and ContaBu learns a rule automatically." })}
          </p>
        </div>
      ) : (
        <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-start text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-100">
                <th className="px-3 py-2 font-medium text-start">{t("rules.merchant", { defaultValue: "Merchant" })}</th>
                <th className="px-2 py-2 font-medium text-start">{t("rules.category", { defaultValue: "Category" })}</th>
                <th className="px-2 py-2 font-medium text-end">{t("rules.affects", { defaultValue: "Affects" })}</th>
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rules.map((r) => {
                const fam = sectionFamily(r.account_type);
                const style = FAMILY_STYLE[fam];
                return (
                <tr key={r.id} className="hover:bg-slate-50">
                  <td
                    className="px-3 py-2.5 font-medium text-slate-800 cursor-pointer uppercase"
                    onClick={() => { setEditing(r); setNote(null); }}
                  >
                    <span className="flex items-center gap-2">
                      <span
                        className={`inline-block size-2 shrink-0 rounded-full ${style.dot}`}
                        title={style.label}
                        aria-label={style.label}
                      />
                      {r.match_text}
                    </span>
                  </td>
                  <td
                    className={`px-2 py-2.5 cursor-pointer ${style.text}`}
                    onClick={() => { setEditing(r); setNote(null); }}
                  >
                    {r.category}
                  </td>
                  <td className="px-2 py-2.5 text-end tabular-nums text-slate-500">{r.matches}</td>
                  <td className="px-2 py-2.5 text-end">
                    <button
                      onClick={async () => {
                        if (!confirm(t("rules.confirmDelete", { defaultValue: "Delete this rule?" }))) return;
                        await deleteRule(r.id);
                        await load();
                      }}
                      className="text-slate-400 hover:text-rose-600"
                      aria-label="delete"
                    >
                      <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
                      </svg>
                    </button>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <EditRuleModal
          rule={editing}
          chart={chart}
          catToType={catToType}
          onClose={() => setEditing(null)}
          onSaved={(applied) => {
            setEditing(null);
            setNote(t("rules.applied", { defaultValue: "Updated {{count}} transactions.", count: applied }));
            load();
          }}
        />
      )}
    </div>
  );
}

function EditRuleModal({
  rule,
  chart,
  catToType,
  onClose,
  onSaved,
}: {
  rule: Rule;
  chart: ChartOfAccounts;
  catToType: Record<string, string>;
  onClose: () => void;
  onSaved: (applied: number) => void;
}) {
  const { t } = useTranslation();
  const [category, setCategory] = useState(rule.category);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      const res = await updateRule(rule.id, { category, account_type: catToType[category] });
      onSaved(res.applied);
    } catch {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-20 bg-black/40 flex items-end sm:items-center justify-center sm:p-4" onClick={onClose}>
      <div className="bg-white w-full sm:max-w-md rounded-t-2xl sm:rounded-2xl p-5 space-y-4" onClick={(e) => e.stopPropagation()}>
        <div>
          <h3 className="font-bold text-slate-900">{t("rules.edit", { defaultValue: "Edit rule" })}</h3>
          <p className="text-sm text-slate-500 uppercase">{rule.match_text}</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-800 mb-1">
            {t("rules.category", { defaultValue: "Category" })}
          </label>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="w-full rounded-lg border border-slate-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-green/40"
          >
            {SECTIONS.filter((s) => chart[s]?.length).map((s) => (
              <optgroup key={s} label={SECTION_LABEL[s] ?? s}>
                {chart[s].map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
        <p className="text-xs text-slate-400">
          {t("rules.editHint", { defaultValue: "Saving re-applies this rule to all matching transactions." })}
        </p>
        <div className="flex gap-2 pt-1">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-xl border border-slate-200 font-medium text-slate-600">
            {t("rules.cancel", { defaultValue: "Cancel" })}
          </button>
          <button
            onClick={save}
            disabled={saving || !category}
            className="flex-1 py-2.5 rounded-xl font-semibold text-white shadow-sm disabled:opacity-60"
            style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
          >
            {saving ? t("rules.saving", { defaultValue: "Saving…" }) : t("rules.save", { defaultValue: "Save" })}
          </button>
        </div>
      </div>
    </div>
  );
}
