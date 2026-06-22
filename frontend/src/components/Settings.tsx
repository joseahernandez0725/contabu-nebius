import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  updateThresholds, getCategories, getCustomCategories, addCustomCategory,
  deleteCustomCategory, type ChartOfAccounts, type CustomCategory,
} from "../api";
import { SECTION_LABEL } from "../accounting";
import type { Company } from "../types";

// Defaults del sistema (espejo del backend) si la empresa aún no fijó los suyos.
const DEFAULTS = { supermarket: 50, gas: 30, settlement: 5 };

// Secciones del P&L que se pueden personalizar (NON_PL no es parte del estado de resultados).
const PL_SECTIONS = ["REVENUE", "COGS", "OPERATING_EXPENSE", "OTHER_INCOME", "OTHER_EXPENSE"];

export default function Settings({
  company,
  onSaved,
}: {
  company: Company | null;
  onSaved: (c: Company) => void;
}) {
  const { t } = useTranslation();
  const [supermarket, setSupermarket] = useState(
    company?.supermarket_threshold != null ? Number(company.supermarket_threshold) : DEFAULTS.supermarket,
  );
  const [gas, setGas] = useState(
    company?.gas_station_threshold != null ? Number(company.gas_station_threshold) : DEFAULTS.gas,
  );
  const [settlement, setSettlement] = useState(
    company?.settlement_buffer_days != null ? company.settlement_buffer_days : DEFAULTS.settlement,
  );
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await updateThresholds({
        supermarket_threshold: supermarket,
        gas_station_threshold: gas,
        settlement_buffer_days: settlement,
      });
      onSaved(updated);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-bold text-slate-900">{t("settings.title")}</h2>
        <p className="text-sm text-slate-500 mt-1">{t("settings.subtitle")}</p>
      </div>

      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5 space-y-5">
        <ThresholdField
          label={t("settings.gasLabel")}
          help={t("settings.gasHelp", { amount: gas })}
          value={gas}
          onChange={(n) => { setGas(n); setSaved(false); }}
        />
        <div className="border-t border-slate-100" />
        <ThresholdField
          label={t("settings.superLabel")}
          help={t("settings.superHelp", { amount: supermarket })}
          value={supermarket}
          onChange={(n) => { setSupermarket(n); setSaved(false); }}
        />
        <div className="border-t border-slate-100" />
        <div>
          <label className="block text-sm font-medium text-slate-800">{t("settings.settleLabel")}</label>
          <div className="mt-2 flex items-center gap-2">
            <input
              type="number"
              min={0}
              inputMode="numeric"
              value={settlement}
              onChange={(e) => { setSettlement(Number(e.target.value)); setSaved(false); }}
              className="w-28 rounded-lg border border-slate-200 px-3 py-2 tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-green/40"
            />
            <span className="text-slate-400">{t("settings.days")}</span>
          </div>
          <p className="text-xs text-slate-500 mt-2">{t("settings.settleHelp", { count: settlement })}</p>
        </div>
      </div>

      {error && <div className="text-sm text-rose-600">{error}</div>}

      <button
        onClick={save}
        disabled={saving}
        className="w-full py-3 rounded-xl font-semibold text-white shadow-sm disabled:opacity-60"
        style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
      >
        {saving ? t("settings.saving") : saved ? t("settings.saved") : t("settings.save")}
      </button>

      <ChartEditor />
    </div>
  );
}

function ThresholdField({
  label,
  help,
  value,
  onChange,
}: {
  label: string;
  help: string;
  value: number;
  onChange: (n: number) => void;
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-800">{label}</label>
      <div className="mt-2 flex items-center gap-2">
        <span className="text-slate-400">$</span>
        <input
          type="number"
          min={0}
          inputMode="decimal"
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="w-28 rounded-lg border border-slate-200 px-3 py-2 tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-green/40"
        />
      </div>
      <p className="text-xs text-slate-500 mt-2">{help}</p>
    </div>
  );
}

// Editor del plan de cuentas (P&L personalizable v1): agregar/eliminar categorías propias por sección.
function ChartEditor() {
  const { t, i18n } = useTranslation();
  const [chart, setChart] = useState<ChartOfAccounts>({});
  const [custom, setCustom] = useState<CustomCategory[]>([]);
  const [addingTo, setAddingTo] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const showTr = (i18n.language || "en").split("-")[0] !== "en";

  const load = () =>
    Promise.all([getCategories(), getCustomCategories()]).then(([c, cu]) => {
      setChart(c);
      setCustom(cu);
    });
  useEffect(() => { load(); }, []);

  const customId = (section: string, catName: string) =>
    custom.find((c) => c.account_type === section && c.name === catName)?.id;

  async function add(section: string) {
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await addCustomCategory(section, name.trim());
      setName("");
      setAddingTo(null);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Error");
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    setBusy(true);
    try { await deleteCustomCategory(id); await load(); } finally { setBusy(false); }
  }

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-5">
      <h3 className="font-semibold text-slate-900">{t("settings.chartTitle", { defaultValue: "Chart of accounts (P&L)" })}</h3>
      <p className="text-sm text-slate-500 mt-1 mb-3">
        {t("settings.chartSubtitle", { defaultValue: "Add your own categories to your income statement." })}
      </p>

      {err && <div className="text-sm text-rose-600 mb-2">{err}</div>}

      <div className="space-y-4">
        {PL_SECTIONS.filter((s) => chart[s]).map((section) => (
          <div key={section}>
            <div className="text-[11px] uppercase tracking-wide text-slate-400 mb-1">
              {SECTION_LABEL[section] ?? section}
            </div>
            <div className="space-y-1">
              {chart[section].map((cat) => {
                const id = customId(section, cat);
                const tr = showTr ? t(`cat.${cat}`, { defaultValue: "" }) : "";
                return (
                  <div key={cat} className="flex items-center justify-between text-sm py-0.5">
                    <span className="text-slate-700">
                      {cat}
                      {tr && <span className="ms-1 text-slate-400/60 italic">· {tr}</span>}
                      {id != null && (
                        <span className="ms-2 text-[10px] uppercase tracking-wide text-brand-green/80 bg-brand-green/10 rounded px-1 py-0.5">
                          {t("settings.chartCustom", { defaultValue: "custom" })}
                        </span>
                      )}
                    </span>
                    {id != null && (
                      <button
                        onClick={() => remove(id)}
                        disabled={busy}
                        className="text-slate-400 hover:text-rose-600 disabled:opacity-50"
                        aria-label="delete"
                      >
                        <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
                        </svg>
                      </button>
                    )}
                  </div>
                );
              })}
            </div>

            {addingTo === section ? (
              <div className="mt-2 flex items-center gap-2">
                <input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && add(section)}
                  placeholder={t("settings.chartName", { defaultValue: "Category name" })}
                  className="flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-green/40"
                />
                <button
                  onClick={() => add(section)}
                  disabled={busy || !name.trim()}
                  className="px-3 py-1.5 rounded-lg text-sm font-medium text-white disabled:opacity-50"
                  style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
                >
                  {t("settings.chartAdd", { defaultValue: "Add" })}
                </button>
                <button
                  onClick={() => { setAddingTo(null); setName(""); setErr(null); }}
                  className="px-2 py-1.5 rounded-lg text-sm text-slate-500"
                >
                  {t("settings.chartCancel", { defaultValue: "Cancel" })}
                </button>
              </div>
            ) : (
              <button
                onClick={() => { setAddingTo(section); setName(""); setErr(null); }}
                className="mt-1.5 text-sm font-medium text-brand-green hover:underline"
              >
                {t("settings.chartAddCategory", { defaultValue: "+ Add category" })}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
