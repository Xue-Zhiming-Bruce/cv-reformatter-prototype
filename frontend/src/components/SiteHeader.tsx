import { useTranslation } from "react-i18next"
import { LogoIcon } from "./icons"
import { FEATURES } from "../constants/features"
import "./SiteHeader.css"

type SiteHeaderProps = {
  activePage?: "home" | "pricing"
  onHome: () => void
  onPricing: () => void
  onLogin: () => void
  onSignup: () => void
}

export function SiteHeader({ activePage, onHome, onPricing, onLogin, onSignup }: SiteHeaderProps) {
  const { t, i18n } = useTranslation()
  const lng = i18n.resolvedLanguage ?? i18n.language

  function toggleLang() {
    i18n.changeLanguage(lng === "ko" ? "en" : "ko")
  }

  return (
    <header className="site-header">
      <div className="site-header__inner">
        <button type="button" className="brand brand--link" onClick={onHome}>
          <LogoIcon className="brand__icon" />
          <span className="brand__name">Reform</span>
        </button>
        <nav className="site-nav" aria-label="Main navigation">
          <button
            type="button"
            className={`site-nav__btn${activePage === "home" ? " site-nav__btn--active" : ""}`}
            onClick={onHome}
          >
            {t("header.home")}
          </button>
          <button
            type="button"
            className={`site-nav__btn${activePage === "pricing" ? " site-nav__btn--active" : ""}`}
            onClick={onPricing}
          >
            {t("header.pricing")}
          </button>
        </nav>
        <div className="site-header__auth">
          <button type="button" className="lang-toggle" onClick={toggleLang} aria-label="Switch language">
            <span className={lng === "ko" ? "lang-toggle__opt lang-toggle__opt--active" : "lang-toggle__opt"}>
              {t("lang.ko")}
            </span>
            <span className="lang-toggle__sep">/</span>
            <span className={lng === "en" ? "lang-toggle__opt lang-toggle__opt--active" : "lang-toggle__opt"}>
              {t("lang.en")}
            </span>
          </button>
          {FEATURES.auth && (
            <>
              <button type="button" className="login-button" onClick={onLogin}>{t("header.login")}</button>
              <button type="button" className="signup-button" onClick={onSignup}>{t("header.signup")}</button>
            </>
          )}
        </div>
      </div>
    </header>
  )
}
