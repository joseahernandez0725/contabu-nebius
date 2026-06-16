import { useTranslation } from "react-i18next";
import { LANGS } from "../i18n";

// Selector de idioma (5 idiomas). Cambia el idioma global de la app.
export default function LanguageSwitcher() {
  const { i18n } = useTranslation();
  const current = (i18n.language || "en").split("-")[0];

  return (
    <select
      aria-label="Language"
      value={current}
      onChange={(e) => i18n.changeLanguage(e.target.value)}
      className="select select-sm border-slate-200 bg-white text-slate-700"
    >
      {LANGS.map((l) => (
        <option key={l.code} value={l.code}>
          {l.label}
        </option>
      ))}
    </select>
  );
}
