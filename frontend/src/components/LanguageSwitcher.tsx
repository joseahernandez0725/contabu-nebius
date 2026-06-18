import { useTranslation } from "react-i18next";
import { LANGS } from "../i18n";

// Selector de idioma compacto: muestra el código corto (ES, EN, AR…) para ocupar poco espacio.
export default function LanguageSwitcher() {
  const { i18n } = useTranslation();
  const current = (i18n.language || "en").split("-")[0];

  return (
    <select
      aria-label="Language"
      value={current}
      onChange={(e) => i18n.changeLanguage(e.target.value)}
      className="text-xs font-semibold text-slate-500 bg-transparent rounded-md py-1 pe-4 ps-1 focus:outline-none focus:ring-1 focus:ring-brand-green/40 cursor-pointer"
    >
      {LANGS.map((l) => (
        <option key={l.code} value={l.code}>
          {l.short}
        </option>
      ))}
    </select>
  );
}
