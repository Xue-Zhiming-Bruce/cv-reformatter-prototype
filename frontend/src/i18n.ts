import i18n from "i18next"
import { initReactI18next } from "react-i18next"
import LanguageDetector from "i18next-browser-languagedetector"
import ko from "./locales/ko.json"
import en from "./locales/en.json"

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      ko: { translation: ko },
      en: { translation: en },
    },
    fallbackLng: "en",
    supportedLngs: ["ko", "en"],
    load: "languageOnly", // maps "ko-KR" → "ko"
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
      lookupLocalStorage: "reform_lang",
    },
    interpolation: {
      escapeValue: false,
    },
  })

// Keep html[lang] in sync — drives [lang="ko"] CSS rules globally
function syncHtmlLang(lng: string) {
  document.documentElement.lang = lng
}
i18n.on("languageChanged", syncHtmlLang)
// Also apply immediately in case languageChanged already fired during init
if (i18n.language) syncHtmlLang(i18n.language)

export default i18n
