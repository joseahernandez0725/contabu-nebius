import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import en from "./locales/en.json";
import es from "./locales/es.json";

// Idiomas soportados: EN + ES (FASE 10, 26-sep-2026). Los otros tres (PT/AR/HI) existían por la
// narrativa del XPRIZE; con el pivote al bookkeeper el mercado real es inglés y español, y cada
// pantalla nueva costaba cinco traducciones en vez de dos.
export const LANGS = [
  { code: "en", label: "English", short: "EN" },
  { code: "es", label: "Español", short: "ES" },
];

i18n
  .use(LanguageDetector) // SOLO localStorage (20-oct): la primera vez, nadie eligió → cae en fallbackLng
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      es: { translation: es },
    },
    fallbackLng: "en",
    load: "languageOnly", // "en-US" → "en"
    interpolation: { escapeValue: false }, // React ya protege contra XSS
    react: { useSuspense: false }, // evita la pantalla en blanco (no usamos Suspense)
    // La primera vez que alguien abre ContaBu el idioma debe ser INGLÉS, no el del navegador (Jose,
    // 20-oct): sin esto el order por defecto de i18next-browser-languagedetector incluye "navigator",
    // y el demo abría en español para cualquier jurado con el navegador en ese idioma. Si la persona
    // elige en el selector, LanguageSwitcher.changeLanguage() la guarda en localStorage y se queda ahí.
    detection: { order: ["localStorage"], caches: ["localStorage"] },
  });

// Ajusta el `lang` del documento. Ya no hay RTL: se fue con el árabe.
function applyDirection(lng: string) {
  document.documentElement.lang = lng;
  document.documentElement.dir = "ltr";
}
applyDirection(i18n.language);
i18n.on("languageChanged", applyDirection);

export default i18n;
