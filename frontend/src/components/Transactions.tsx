import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { getTransactions, getCategories, updateTransaction, money, type ChartOfAccounts } from "../api";
import type { Transaction } from "../types";

const SECTIONS = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE", "NON_PL"];

// "2026-06-15" → "06/15/26" (sin zona horaria, robusto).
const fmtDate = (iso: string) => {
  const [y, m, d] = iso.split("-");
  return y && m && d ? `${m}/${d}/${y.slice(2)}` : iso;
};

type SortKey = "date" | "category" | "subcategory";

export default function Transactions() {
  const { t } = useTranslation();
  const [txs, setTxs] = useState<Transaction[] | null>(null);
  const [chart, setChart] = useState<ChartOfAccounts>({});
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Transaction | null>(null);
  const [search, setSearch] = useState("");
  const [account, setAccount] = useState<string>(""); // "" = todas las cuentas
  const [sortKey, setSortKey] = useState<SortKey>("date");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const load = () =>
    getTransactions().then(setTxs).catch((e) => setError(e instanceof Error ? e.message : "Error"));

  useEffect(() => {
    load();
    getCategories().then(setChart).catch(() => {});
  }, []);

  const catToType: Record<string, string> = useMemo(() => {
    const m: Record<string, string> = {};
    for (const [at, cats] of Object.entries(chart)) for (const c of cats) m[c] = at;
    return m;
  }, [chart]);

  // Cuentas bancarias distintas presentes en las transacciones (para el filtro).
  const accounts = useMemo(() => {
    if (!txs) return [];
    return [...new Set(txs.map((tx) => tx.bank_account).filter((a): a is string => !!a))].sort();
  }, [txs]);

  const visible = useMemo(() => {
    if (!txs) return [];
    const q = search.trim().toLowerCase();
    const filtered = txs.filter((tx) => {
      if (account && tx.bank_account !== account) return false;
      if (q)
        return [tx.description, tx.category, tx.subcategory, tx.bank_account].some((f) =>
          (f ?? "").toLowerCase().includes(q),
        );
      return true;
    });
    const key = (tx: Transaction) =>
      sortKey === "date" ? tx.date : sortKey === "category" ? tx.category ?? "" : tx.subcategory ?? "";
    const sorted = [...filtered].sort((a, b) => {
      const av = key(a);
      const bv = key(b);
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === "asc" ? cmp : -cmp;
    });
    return sorted;
  }, [txs, search, account, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir(key === "date" ? "desc" : "asc");
    }
  }
  const arrow = (key: SortKey) => (sortKey === key ? (sortDir === "asc" ? " ↑" : " ↓") : "");

  if (error) return <div className="alert alert-error">{error}</div>;
  if (!txs)
    return (
      <div className="flex justify-center py-16">
        <span className="loading loading-spinner loading-lg text-brand-green" />
      </div>
    );

  if (txs.length === 0)
    return (
      <div className="text-center py-12 text-slate-500">
        <p className="font-medium">{t("tx.empty")}</p>
        <p className="text-sm mt-1">{t("tx.emptyHint")}</p>
      </div>
    );

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-3 flex-wrap">
        <h2 className="font-semibold text-slate-900 shrink-0">{t("tx.title")}</h2>
        {accounts.length > 0 && (
          <select
            value={account}
            onChange={(e) => setAccount(e.target.value)}
            className="h-8 text-sm rounded-lg bg-slate-50 border border-slate-200 px-2 ms-auto focus:outline-none focus:ring-2 focus:ring-brand-green/40"
          >
            <option value="">{t("tx.allAccounts")}</option>
            {accounts.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        )}
        <div className={`relative flex-1 max-w-[14rem] ${accounts.length > 0 ? "" : "ms-auto"}`}>
          <svg viewBox="0 0 24 24" className="size-4 absolute start-2.5 top-1/2 -translate-y-1/2 text-slate-400" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="7" /><path d="M21 21l-4-4" strokeLinecap="round" />
          </svg>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("tx.search")}
            className="w-full h-8 text-sm rounded-lg bg-slate-50 border border-slate-200 ps-8 pe-2 focus:outline-none focus:ring-2 focus:ring-brand-green/40"
          />
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[34rem]">
          <thead>
            <tr className="text-start text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-100 select-none">
              <th className="px-3 py-2 font-medium text-start">
                <button onClick={() => toggleSort("date")} className="hover:text-slate-600">{t("tx.cols.date")}{arrow("date")}</button>
              </th>
              <th className="px-3 py-2 font-medium text-start">{t("tx.cols.movement")}</th>
              <th className="px-2 py-2 font-medium text-start">
                <button onClick={() => toggleSort("category")} className="hover:text-slate-600">{t("tx.cols.category")}{arrow("category")}</button>
              </th>
              <th className="px-2 py-2 font-medium text-start">
                <button onClick={() => toggleSort("subcategory")} className="hover:text-slate-600">{t("tx.cols.subcategory")}{arrow("subcategory")}</button>
              </th>
              <th className="px-3 py-2 font-medium text-end">{t("tx.cols.amount")}</th>
              {accounts.length > 0 && (
                <th className="px-2 py-2 font-medium text-start">{t("tx.cols.account")}</th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {visible.map((tx) => {
              const conf = tx.confidence;
              const lowConfidence = conf != null && conf < 0.9;
              // Cuanto MENOR la confianza, MÁS intenso el rojo (de 0.9 tenue a ≤0.3 fuerte).
              const redAlpha =
                conf != null && conf < 0.9 ? 0.06 + Math.min(1, (0.9 - conf) / 0.6) * 0.34 : 0;
              return (
                <tr
                  key={tx.id}
                  onClick={() => setEditing(tx)}
                  style={lowConfidence ? { backgroundColor: `rgba(244, 63, 94, ${redAlpha.toFixed(2)})` } : undefined}
                  className={`cursor-pointer ${
                    lowConfidence ? "hover:brightness-95" : "hover:bg-slate-50 active:bg-slate-100"
                  }`}
                >
                  <td className="px-3 py-2.5 text-slate-500 whitespace-nowrap tabular-nums">{fmtDate(tx.date)}</td>
                  <td className="px-3 py-2.5 text-slate-500 max-w-[6.5rem] truncate" title={tx.description}>
                    {tx.description}
                  </td>
                  <td className="px-2 py-2.5 text-slate-800 whitespace-nowrap">
                    {tx.category ?? "—"}
                    {tx.confidence != null && (
                      <span className="ms-1 text-[10px] text-slate-400 tabular-nums">
                        {Math.round(tx.confidence * 100)}%
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-2.5 text-slate-500 max-w-[6rem] truncate uppercase">{tx.subcategory ?? "—"}</td>
                  <td className="px-3 py-2.5 text-end tabular-nums font-medium text-slate-700 whitespace-nowrap">
                    {money(Number(tx.amount))}
                  </td>
                  {accounts.length > 0 && (
                    <td className="px-2 py-2.5 text-slate-500 max-w-[7rem] truncate" title={tx.bank_account ?? undefined}>
                      {tx.bank_account ?? "—"}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {editing && (
        <EditModal
          tx={editing}
          chart={chart}
          catToType={catToType}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            load();
          }}
        />
      )}
    </div>
  );
}

// Desplegable de categoría con traducción translúcida al lado (solo si el idioma ≠ inglés).
function CategorySelect({
  chart,
  value,
  onChange,
}: {
  chart: ChartOfAccounts;
  value: string;
  onChange: (c: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const showTr = (i18n.language || "en").split("-")[0] !== "en";
  const tr = (c: string) => (showTr ? t(`cat.${c}`, { defaultValue: "" }) : "");

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full text-start rounded-lg border border-slate-200 px-3 py-2 flex items-center justify-between focus:outline-none focus:ring-2 focus:ring-brand-green/40"
      >
        <span className="truncate">
          {value || "—"}
          {tr(value) && <span className="ms-1.5 text-slate-400/60 italic">· {tr(value)}</span>}
        </span>
        <svg viewBox="0 0 24 24" className="size-4 text-slate-400 shrink-0" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-full max-h-60 overflow-auto rounded-lg border border-slate-200 bg-white shadow-lg">
          {SECTIONS.filter((s) => chart[s]?.length).map((s) => (
            <div key={s}>
              <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-slate-400 bg-slate-50 sticky top-0">
                {t(`sections.${s}`)}
              </div>
              {chart[s].map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => {
                    onChange(c);
                    setOpen(false);
                  }}
                  className={`w-full text-start px-3 py-2 flex items-center justify-between gap-2 hover:bg-slate-50 ${
                    c === value ? "bg-brand-green/5" : ""
                  }`}
                >
                  <span className="text-slate-800">{c}</span>
                  {tr(c) && <span className="text-slate-400/60 italic text-sm truncate">{tr(c)}</span>}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EditModal({
  tx,
  chart,
  catToType,
  onClose,
  onSaved,
}: {
  tx: Transaction;
  chart: ChartOfAccounts;
  catToType: Record<string, string>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [category, setCategory] = useState(tx.category ?? "");
  const [subcategory, setSubcategory] = useState(tx.subcategory ?? "");
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      await updateTransaction(tx.id, {
        category,
        account_type: catToType[category],
        subcategory,
      });
      onSaved();
    } catch {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-20 bg-black/40 flex items-end sm:items-center justify-center sm:p-4"
      onClick={onClose}
    >
      <div
        className="bg-white w-full sm:max-w-md rounded-t-2xl sm:rounded-2xl p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div>
          <h3 className="font-bold text-slate-900">{t("tx.edit.title")}</h3>
          <p className="text-sm text-slate-500 truncate" title={tx.description}>
            {tx.description} · {money(Number(tx.amount))}
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-800 mb-1">{t("tx.edit.category")}</label>
          <CategorySelect chart={chart} value={category} onChange={setCategory} />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-800 mb-1">{t("tx.edit.subcategory")}</label>
          <input
            value={subcategory}
            onChange={(e) => setSubcategory(e.target.value)}
            className="w-full rounded-lg border border-slate-200 px-3 py-2 uppercase focus:outline-none focus:ring-2 focus:ring-brand-green/40"
          />
        </div>

        <div className="flex gap-2 pt-1">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-xl border border-slate-200 font-medium text-slate-600">
            {t("tx.edit.cancel")}
          </button>
          <button
            onClick={save}
            disabled={saving || !category}
            className="flex-1 py-2.5 rounded-xl font-semibold text-white shadow-sm disabled:opacity-60"
            style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
          >
            {saving ? t("tx.edit.saving") : t("tx.edit.save")}
          </button>
        </div>
        <p className="text-xs text-slate-400">{t("tx.edit.learn")}</p>
      </div>
    </div>
  );
}
