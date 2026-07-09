import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { updateCompany, updateProfile } from "../api";
import type { Company, CompanyProfile } from "../types";

// Sectores disponibles (las claves coinciden con categories.py). Segunda ola 3-jul-2026: los
// oficios inmigrantes más comunes fuera de los 3 verticales originales. "other" usa el plan
// genérico y pregunta el oficio en texto libre (va al perfil como hecho para la IA).
const SECTORS: { id: string; icon: ReactNode }[] = [
  { id: "construction", icon: <path d="M2 20h20M4 20V8l8-4 8 4v12M9 20v-6h6v6" /> },
  { id: "cleaning", icon: <path d="M19 5l-7 7M16 2l6 6M3 21l6-2 9-9-4-4-9 9-2 6z" /> },
  { id: "restaurant", icon: <path d="M3 2v7a3 3 0 006 0V2M6 2v20M16 2c-2 0-3 2-3 5s1 5 3 5 3-2 3-5-1-5-3-5zM16 12v10" /> },
  { id: "landscaping", icon: <path d="M12 22V11M12 11C12 7 9 4 4 4c0 5 3 8 8 7zM12 11c0-4 3-7 8-7 0 5-3 8-8 7z" /> },
  { id: "painting", icon: <path d="M3 3h13v5H3zM16 5h4v5h-8v4M12 14v3M10 17h4v5h-4z" /> },
  { id: "trucking", icon: <path d="M1 7h13v9H1zM14 10h4l4 3v3h-8M6 19a2 2 0 100-4 2 2 0 000 4zM17 19a2 2 0 100-4 2 2 0 000 4z" /> },
  { id: "beauty", icon: <path d="M6 8a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM6 21a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM20 4L8 16M20 20L8 8" /> },
  { id: "auto_repair", icon: <path d="M14 7a4 4 0 00-5 5L3 18l3 3 6-6a4 4 0 005-5l-2.5 2.5-3-3L14 7z" /> },
  { id: "retail", icon: <path d="M6 7h12l1.5 14h-15L6 7zM9 10V6a3 3 0 016 0v4" /> },
  { id: "other", icon: <path d="M12 2a10 10 0 100 20 10 10 0 000-20zM9.1 9a3 3 0 015.8 1c0 2-3 3-3 3M12 17h.01" /> },
];

// La ENTREVISTA del bookkeeper — RONDA 1 (FASE 9 Paso 5). Preguntas ESTRUCTURADAS: la UI se
// traduce, pero los VALORES que viajan al backend son ingleses fijos (Regla de Oro). `multi` =
// se pueden elegir varias. Saltable pregunta a pregunta y completa (la fricción mata activación).
const QUESTIONS: { key: keyof CompanyProfile; options: string[]; multi?: boolean }[] = [
  { key: "uses_subcontractors", options: ["yes", "no"] },
  { key: "client_payment_methods", options: ["checks", "zelle", "cash", "card", "bank_transfer"], multi: true },
  { key: "payroll", options: ["none", "w2", "1099", "both"] },
  { key: "work_vehicles", options: ["yes", "no"] },
  { key: "personal_expenses_mixed", options: ["yes", "sometimes", "no"] },
];

// Etiqueta i18n de una opción: las de payroll llevan prefijo (sus valores chocarían con yes/no…).
function optionLabel(t: TFunction, key: string, value: string): string {
  const i18nKey = key === "payroll" ? `interview.opt.payroll_${value}` : `interview.opt.${value}`;
  return t(i18nKey, { defaultValue: value });
}

// Onboarding: se muestra cuando la empresa aún no tiene sector elegido. Paso 1: nombre + sector
// (deciden reglas de sector y plan de cuentas). Paso 2: la entrevista del bookkeeper (perfil).
export default function Onboarding({ company, onDone }: {
  company: Company; onDone: (c: Company) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(company.name === "My Business" ? "" : company.name);
  const [sector, setSector] = useState<string>("");
  const [otherTrade, setOtherTrade] = useState(""); // "other": el oficio en texto libre → perfil
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Paso 2 — entrevista: empresa ya guardada, índice de pregunta y respuestas acumuladas.
  const [ready, setReady] = useState<Company | null>(null);
  const [qIndex, setQIndex] = useState(0);
  const [answers, setAnswers] = useState<Partial<CompanyProfile>>({});
  const [multiPick, setMultiPick] = useState<string[]>([]);

  async function save() {
    if (!sector) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateCompany({ name: name.trim() || "My Business", sector });
      // "other" + oficio escrito → primer hecho del perfil (la IA lo recibe como contexto).
      if (sector === "other" && otherTrade.trim()) setAnswers({ trade: otherTrade.trim() });
      setReady(updated); // la empresa quedó lista → arranca la entrevista (no salir todavía)
      setSaving(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
      setSaving(false);
    }
  }

  // Termina la entrevista: guarda lo respondido (si hay algo) y entra a la app. Un fallo del
  // perfil NO bloquea la entrada (el perfil se puede completar después; la empresa ya existe).
  async function finishInterview(finalAnswers: Partial<CompanyProfile>) {
    if (!ready) return;
    setSaving(true);
    try {
      if (Object.keys(finalAnswers).length > 0) await updateProfile(finalAnswers);
    } catch {
      /* el perfil es opcional: no bloquear la entrada a la app */
    }
    onDone(ready);
  }

  function advance(nextAnswers: Partial<CompanyProfile>) {
    setAnswers(nextAnswers);
    setMultiPick([]);
    if (qIndex + 1 >= QUESTIONS.length) {
      void finishInterview(nextAnswers);
    } else {
      setQIndex(qIndex + 1);
    }
  }

  // ---- Paso 2: la entrevista ----
  if (ready) {
    const q = QUESTIONS[qIndex];
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4 py-10">
        <div className="w-full max-w-md space-y-6">
          <div className="text-center">
            <div className="size-14 rounded-2xl mx-auto flex items-center justify-center text-white shadow-sm"
              style={{ background: "linear-gradient(135deg, #15803d, #f4b400)" }}>
              <svg viewBox="0 0 24 24" className="size-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M8 10h8M8 14h5M21 12a9 9 0 11-4.5-7.8L21 3l-.7 4.5A9 9 0 0121 12z" />
              </svg>
            </div>
            <h1 className="text-2xl font-bold text-slate-900 mt-4">
              {t("interview.title", { defaultValue: "A few quick questions" })}
            </h1>
            <p className="text-slate-500 mt-1">
              {t("interview.subtitle", { defaultValue: "Your bookkeeper would ask the same — your answers make the rules smarter from day one." })}
            </p>
          </div>

          {/* Progreso: puntos, una pregunta a la vez (una entrevista, no un formulario) */}
          <div className="flex justify-center gap-1.5">
            {QUESTIONS.map((_, i) => (
              <span key={i} className={`size-2 rounded-full ${i === qIndex ? "bg-brand-green" : i < qIndex ? "bg-brand-green/40" : "bg-slate-200"}`} />
            ))}
          </div>

          <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
            <p className="font-medium text-slate-900 mb-3">
              {t(`interview.q.${q.key}`, { defaultValue: q.key })}
            </p>
            <div className="flex flex-wrap gap-2">
              {q.options.map((opt) => {
                const active = q.multi ? multiPick.includes(opt) : false;
                return (
                  <button
                    key={opt}
                    onClick={() => {
                      if (q.multi) {
                        setMultiPick((prev) => prev.includes(opt) ? prev.filter((p) => p !== opt) : [...prev, opt]);
                      } else {
                        advance({ ...answers, [q.key]: opt });
                      }
                    }}
                    className={`px-3.5 py-2 rounded-xl border text-sm font-medium transition-colors ${
                      active ? "border-brand-green bg-brand-green/10 text-brand-green"
                             : "border-slate-200 text-slate-700 hover:bg-slate-50"
                    }`}
                  >
                    {optionLabel(t, q.key, opt)}
                  </button>
                );
              })}
            </div>
            {q.multi && (
              <button
                onClick={() => advance(multiPick.length > 0 ? { ...answers, [q.key]: multiPick } : answers)}
                className="mt-4 w-full py-2.5 rounded-xl font-semibold text-white bg-brand-green disabled:opacity-50"
                disabled={saving}
              >
                {t("interview.next", { defaultValue: "Next" })}
              </button>
            )}
          </div>

          <div className="flex justify-center gap-6 text-sm">
            <button onClick={() => advance(answers)} className="text-slate-500 hover:underline" disabled={saving}>
              {t("interview.skip", { defaultValue: "Skip this question" })}
            </button>
            <button onClick={() => void finishInterview(answers)} className="text-slate-500 hover:underline" disabled={saving}>
              {saving ? t("interview.saving", { defaultValue: "Saving…" }) : t("interview.skipAll", { defaultValue: "Skip for now" })}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ---- Paso 1: nombre + sector ----
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
          {/* "Other": el bookkeeper preguntaría "¿y a qué te dedicas?" — texto libre al perfil. */}
          {sector === "other" && (
            <div className="mt-3">
              <label className="block text-sm font-medium text-slate-800 mb-1">
                {t("onboarding.otherTradeLabel", { defaultValue: "What does your business do?" })}
              </label>
              <input
                value={otherTrade}
                onChange={(e) => setOtherTrade(e.target.value)}
                maxLength={120}
                placeholder={t("onboarding.otherTradePlaceholder", { defaultValue: "e.g. mobile car wash, tailoring…" })}
                className="w-full rounded-xl border border-slate-200 px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-brand-green/40"
              />
            </div>
          )}
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
