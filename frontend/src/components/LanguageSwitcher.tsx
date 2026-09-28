import { useTranslation } from "react-i18next";
import { LANGS } from "../i18n";

/**
 * Interruptor de idioma. Con solo dos idiomas (EN · ES, FASE 10) un `<select>` sobraba: obligaba a
 * abrir un menú para elegir entre dos cosas. Un interruptor muestra las dos opciones a la vez y
 * cambia de un toque — y de paso se ve cuál está activo sin desplegar nada.
 */
export default function LanguageSwitcher() {
  const { i18n } = useTranslation();
  const current = (i18n.language || "en").split("-")[0];
  const active = LANGS.some((l) => l.code === current) ? current : "en";

  return (
    <div
      role="group"
      aria-label="Language"
      className="inline-flex items-center rounded-full bg-slate-100 p-0.5"
    >
      {LANGS.map((l) => {
        const on = l.code === active;
        return (
          <button
            key={l.code}
            type="button"
            onClick={() => i18n.changeLanguage(l.code)}
            aria-pressed={on}
            title={l.label}
            className={`px-2.5 py-0.5 text-[11px] font-bold rounded-full transition-colors ${
              on ? "bg-brand-green text-white shadow-sm" : "text-slate-500 hover:text-slate-700"
            }`}
          >
            {l.short}
          </button>
        );
      })}
    </div>
  );
}
