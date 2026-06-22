import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { updateCompany } from "../api";
import type { Company } from "../types";

// Sectores disponibles (las claves coinciden con categories.py / seed_sector_rules.py).
const SECTORS: { id: string; icon: ReactNode }[] = [
  { id: "construction", icon: <path d="M2 20h20M4 20V8l8-4 8 4v12M9 20v-6h6v6" /> },
  { id: "cleaning", icon: <path d="M19 5l-7 7M16 2l6 6M3 21l6-2 9-9-4-4-9 9-2 6z" /> },
  { id: "restaurant", icon: <path d="M3 2v7a3 3 0 006 0V2M6 2v20M16 2c-2 0-3 2-3 5s1 5 3 5 3-2 3-5-1-5-3-5zM16 12v10" /> },
  { id: "other", icon: <path d="M12 2a10 10 0 100 20 10 10 0 000-20zM9.1 9a3 3 0 015.8 1c0 2-3 3-3 3M12 17h.01" /> },
];

// Onboarding: se muestra cuando la empresa aún no tiene sector elegido. El sector decide las
// reglas de sector y el plan de cuentas, así que se pide al crear la cuenta.
export default function Onboarding({ company, onDone }: {
  company: Company; onDone: (c: Company) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(company.name === "My Business" ? "" : company.name);
  const [sector, setSector] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!sector) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateCompany({ name: name.trim() || "My Business", sector });
      onDone(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
      setSaving(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4 py-10">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center">
          <div className="size-14 rounded-2xl mx-auto flex items-center justify-center text-white shadow-sm"
            style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}>
            <svg viewBox="0 0 24 24" className="size-7" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <path d="M4 20V13M10 20V5M16 20v-9M22 20H2" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-slate-900 mt-4">
            {t("onboarding.title", { defaultValue: "Set up your business" })}
          </h1>
          <p className="text-slate-500 mt-1">
            {t("onboarding.subtitle", { defaultValue: "Your sector tailors the categories and rules to your trade." })}
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-800 mb-1">
            {t("onboarding.nameLabel", { defaultValue: "Business name" })}
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("onboarding.namePlaceholder", { defaultValue: "e.g. Garcia Construction LLC" })}
            className="w-full rounded-xl border border-slate-200 px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-brand-green/40"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-800 mb-2">
            {t("onboarding.sectorLabel", { defaultValue: "What's your business sector?" })}
          </label>
          <div className="grid grid-cols-2 gap-3">
            {SECTORS.map((s) => {
              const active = sector === s.id;
              return (
                <button
                  key={s.id}
                  onClick={() => setSector(s.id)}
                  className={`flex flex-col items-center gap-2 py-5 rounded-2xl border-2 transition-colors ${
                    active ? "border-brand-green bg-brand-green/5" : "border-slate-200 hover:bg-slate-50"
                  }`}
                >
                  <svg viewBox="0 0 24 24" className={`size-7 ${active ? "text-brand-green" : "text-slate-400"}`}
                    fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                    {s.icon}
                  </svg>
                  <span className={`text-sm font-medium ${active ? "text-brand-green" : "text-slate-600"}`}>
                    {t(`sectors.${s.id}`, { defaultValue: s.id })}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {error && <div className="text-sm text-rose-600 text-center">{error}</div>}

        <button
          onClick={save}
          disabled={!sector || saving}
          className="w-full py-3 rounded-xl font-semibold text-white shadow-sm disabled:opacity-50"
          style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}
        >
          {saving ? t("onboarding.saving", { defaultValue: "Setting up…" }) : t("onboarding.continue", { defaultValue: "Continue" })}
        </button>
      </div>
    </div>
  );
}
