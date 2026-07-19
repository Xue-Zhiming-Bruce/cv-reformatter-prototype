import { useTranslation } from "react-i18next"
import { LogoIcon } from "./icons"
import "./Footer.css"

type FooterProps = {
  onHome?: () => void
  onPricing?: () => void
  onTerms?: () => void
  onPrivacy?: () => void
}

function XIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.744l7.73-8.835L2.02 2.25h6.948l4.255 5.626L18.244 2.25zm-1.161 17.52h1.833L7.084 4.126H5.117L17.083 19.77z" />
    </svg>
  )
}

function LinkedInIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 0 1-2.063-2.065 2.064 2.064 0 1 1 2.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
    </svg>
  )
}

function InstagramIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838a6.162 6.162 0 1 0 0 12.324 6.162 6.162 0 0 0 0-12.324zM12 16a4 4 0 1 1 0-8 4 4 0 0 1 0 8zm6.406-11.845a1.44 1.44 0 1 0 0 2.881 1.44 1.44 0 0 0 0-2.881z" />
    </svg>
  )
}

function GlobeIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  )
}

function ChevronDownIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 9l6 6 6-6" />
    </svg>
  )
}

export function Footer({ onHome, onPricing, onTerms, onPrivacy }: FooterProps) {
  const { t, i18n } = useTranslation()
  const lng = i18n.resolvedLanguage ?? i18n.language
  const lngLabel = lng === "ko" ? "한국어" : "English"

  function toggleLang() {
    i18n.changeLanguage(lng === "ko" ? "en" : "ko")
  }

  return (
    <footer className="site-footer">
      {/* ── Link columns ── */}
      <div className="footer-upper">
        <div className="footer-upper__inner">
          {/* Brand column */}
          <div className="footer-brand-col">
            <div className="brand footer-brand">
              <LogoIcon className="brand__icon" />
              <span className="brand__name">Reform</span>
            </div>
            <p className="footer-brand__desc">{t("footer.brandDesc")}</p>
          </div>

          {/* Product */}
          <div className="footer-col">
            <h3 className="footer-col__heading">{t("footer.product")}</h3>
            <ul className="footer-col__list">
              <li>
                <button type="button" className="footer-col__link footer-col__link--btn" onClick={onHome}>
                  {t("footer.home")}
                </button>
              </li>
              <li>
                <button type="button" className="footer-col__link footer-col__link--btn" onClick={onPricing}>
                  {t("footer.pricing")}
                </button>
              </li>
            </ul>
          </div>

          {/* Company */}
          <div className="footer-col">
            <h3 className="footer-col__heading">{t("footer.company")}</h3>
            <ul className="footer-col__list">
              <li>
                <a href="mailto:hello@reformcv.com" className="footer-col__link">{t("footer.contact")}</a>
              </li>
            </ul>
          </div>

          {/* Legal */}
          <div className="footer-col">
            <h3 className="footer-col__heading">{t("footer.legal")}</h3>
            <ul className="footer-col__list">
              <li>
                <button type="button" className="footer-col__link footer-col__link--btn" onClick={onTerms}>
                  {t("footer.terms")}
                </button>
              </li>
              <li>
                <button type="button" className="footer-col__link footer-col__link--btn" onClick={onPrivacy}>
                  {t("footer.privacy")}
                </button>
              </li>
            </ul>
          </div>
        </div>
      </div>

      {/* ── Bottom bar ── */}
      <div className="footer-bar">
        <div className="footer-bar__inner">
          <button type="button" className="footer-lang" onClick={toggleLang} aria-label="Switch language">
            <GlobeIcon />
            <span>{lngLabel}</span>
            <ChevronDownIcon />
          </button>

          <div className="footer-socials">
            <a href="#" className="footer-social" aria-label="X (Twitter)"><XIcon /></a>
            <a href="#" className="footer-social" aria-label="LinkedIn"><LinkedInIcon /></a>
            <a href="#" className="footer-social" aria-label="Instagram"><InstagramIcon /></a>
          </div>

          <div className="footer-legal">
            <span className="footer-legal__copy">{t("footer.copyright")}</span>
            <button type="button" className="footer-legal__link" onClick={onTerms}>{t("footer.terms")}</button>
            <button type="button" className="footer-legal__link" onClick={onPrivacy}>{t("footer.privacy")}</button>
          </div>
        </div>
      </div>
    </footer>
  )
}
