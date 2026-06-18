import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import en from "./locales/en.json";
import es from "./locales/es.json";
import pt from "./locales/pt.json";
import ar from "./locales/ar.json";
import hi from "./locales/hi.json";

// Idiomas soportados (Regla de Oro: UI en 5 idiomas).
export const LANGS = [
  { code: "en", label: "English", short: "EN" },
  { code: "es", label: "Español", short: "ES" },
  { code: "pt", label: "Português", short: "PT" },
  { code: "ar", label: "العربية", short: "AR" },
  { code: "hi", label: "हिन्दी", short: "HI" },
];

const RTL_LANGS = ["ar"]; // idiomas de derecha a izquierda

i18n
  .use(LanguageDetector) // detecta el idioma del navegador / localStorage
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      es: { translation: es },
      pt: { translation: pt },
      ar: { translation: ar },
      hi: { translation: hi },
    },
    fallbackLng: "en",
    load: "languageOnly", // "en-US" → "en"
    interpolation: { escapeValue: false }, // React ya protege contra XSS
    react: { useSuspense: false }, // evita la pantalla en blanco (no usamos Suspense)
  });

// Ajusta dir/lang del documento (RTL para árabe) al cargar y al cambiar de idioma.
function applyDirection(lng: string) {
  document.documentElement.lang = lng;
  document.documentElement.dir = RTL_LANGS.includes(lng) ? "rtl" : "ltr";
}
applyDirection(i18n.language);
i18n.on("languageChanged", applyDirection);

export default i18n;
