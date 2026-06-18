import { useState } from "react";
import { useTranslation } from "react-i18next";
import { updateThresholds } from "../api";
import type { Company } from "../types";

// Defaults del sistema (espejo de backend/rules.py) si la empresa aún no fijó los suyos.
const DEFAULTS = { supermarket: 50, gas: 30 };

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
