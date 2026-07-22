import { useState } from "react"
import { useTranslation } from "react-i18next"
import { SiteHeader } from "../components/SiteHeader"
import { Footer } from "../components/Footer"
import { getPricing } from "../constants/pricing"
import "./PricingScreen.css"

type PricingScreenProps = {
  onHome: () => void
  onPricing: () => void
  onLogin: () => void
  onSignup: () => void
  onTerms: () => void
  onPrivacy: () => void
}

function LockIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  )
}

function CheckIcon({ accent }: { accent?: boolean }) {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={accent ? "var(--color-accent)" : "currentColor"} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }}>
      <polyline points="20 6 9 17 4 12" />
    </svg>
  )
}

export function PricingScreen({ onHome, onPricing, onLogin, onSignup, onTerms, onPrivacy }: PricingScreenProps) {
  const { t, i18n } = useTranslation()
  const [openFaq, setOpenFaq] = useState<number | null>(null)
  const [showToast, setShowToast] = useState(false)

  const lng = i18n.resolvedLanguage ?? i18n.language
  const p = getPricing(lng)

  const freeFeatures     = t("pricing.freeFeatures",     { returnObjects: true }) as string[]
  const personalFeatures = t("pricing.personalFeatures", { returnObjects: true }) as string[]
  const teamFeatures     = t("pricing.teamFeatures",     { returnObjects: true }) as string[]
  const enterpriseFeatures = t("pricing.enterpriseFeatures", { returnObjects: true }) as string[]
  const faqItems         = t("pricing.faq",              { returnObjects: true }) as Array<{ q: string; a: string }>

  function handlePaidPlan() {
    setShowToast(true)
    setTimeout(() => setShowToast(false), 2500)
  }

  return (
    <div className="pricing-page">
      <SiteHeader
        activePage="pricing"
        onHome={onHome}
        onPricing={onPricing}
        onLogin={onLogin}
        onSignup={onSignup}
      />

      {/* ── Hero ── */}
      <section className="pricing-hero">
        <h1 className="pricing-hero__heading">{t("pricing.heading")}</h1>
        <p className="pricing-hero__sub">{t("pricing.sub")}</p>
      </section>

      {/* ── Plan cards ── */}
      <section className="pricing-cards-section">
        <div className="pricing-cards">

          {/* Free */}
          <div className="plan-card">
            <div className="plan-card__top">
              <span className="plan-card__name">{t("pricing.freeName")}</span>
              <div className="plan-card__price-row">
                <span className="plan-card__price">{p.currency}0</span>
                <span className="plan-card__period">{t("pricing.freePeriod")}</span>
              </div>
            </div>
            <ul className="plan-card__features">
              {freeFeatures.map((f) => (
                <li key={f} className="plan-card__feature">
                  <CheckIcon />
                  {f}
                </li>
              ))}
            </ul>
            <button type="button" className="plan-card__cta plan-card__cta--free" onClick={onHome}>
              {t("pricing.freeBtn")}
            </button>
          </div>

          {/* Personal — featured */}
          <div className="plan-card plan-card--featured">
            <div className="plan-badge">{t("pricing.mostPopularBadge")}</div>
            <div className="plan-card__top">
              <span className="plan-card__name">{t("pricing.personalName")}</span>
              <div className="plan-card__price-row">
                <span className="plan-card__price">{p.currency}{p.personal.price.toLocaleString()}</span>
                <span className="plan-card__period">{t("pricing.perMonth")}</span>
              </div>
            </div>
            <ul className="plan-card__features">
              {personalFeatures.map((f) => (
                <li key={f} className="plan-card__feature">
                  <CheckIcon accent />
                  {f}
                </li>
              ))}
            </ul>
            <button type="button" className="plan-card__cta plan-card__cta--featured" onClick={handlePaidPlan}>
              {t("pricing.personalBtn")}
            </button>
          </div>

          {/* Team */}
          <div className="plan-card">
            <div className="plan-card__top">
              <span className="plan-card__name">{t("pricing.teamName")}</span>
              <div className="plan-card__price-row">
                <span className="plan-card__price">{p.currency}{p.team.price.toLocaleString()}</span>
                <span className="plan-card__period">{t("pricing.perMonth")}</span>
              </div>
            </div>
            <ul className="plan-card__features">
              {teamFeatures.map((f) => (
                <li key={f} className="plan-card__feature">
                  <CheckIcon />
                  {f}
                </li>
              ))}
            </ul>
            <button type="button" className="plan-card__cta plan-card__cta--secondary" onClick={handlePaidPlan}>
              {t("pricing.teamBtn")}
            </button>
          </div>

          {/* Enterprise */}
          <div className="plan-card plan-card--enterprise">
            <div className="plan-card__top">
              <span className="plan-card__name">{t("pricing.enterpriseName")}</span>
              <div className="plan-card__price-row">
                <span className="plan-card__price plan-card__price--custom">{t("pricing.customPrice")}</span>
              </div>
            </div>
            <ul className="plan-card__features">
              {enterpriseFeatures.map((f) => (
                <li key={f} className="plan-card__feature">
                  <CheckIcon />
                  {f}
                </li>
              ))}
            </ul>
            <a
              href="mailto:hello@reformcv.com"
              className="plan-card__cta plan-card__cta--enterprise"
            >
              {t("pricing.enterpriseBtn")}
            </a>
          </div>

        </div>

        {/* Trust line */}
        <p className="pricing-trust">
          <LockIcon />
          {t("pricing.trustLine")}
        </p>
      </section>

      {/* ── FAQ ── */}
      <section className="faq-section">
        <div className="faq-inner">
          <h2 className="faq-heading">{t("pricing.faqHeading")}</h2>
          <div className="faq-list">
            {faqItems.map((item, i) => (
              <div key={i} className={`faq-item${openFaq === i ? " faq-item--open" : ""}`}>
                <button
                  type="button"
                  className="faq-question"
                  onClick={() => setOpenFaq(openFaq === i ? null : i)}
                  aria-expanded={openFaq === i}
                >
                  <span>{item.q}</span>
                  <svg
                    className="faq-chevron"
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                </button>
                {openFaq === i && (
                  <p className="faq-answer">{item.a}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      <Footer onHome={onHome} onPricing={onPricing} onTerms={onTerms} onPrivacy={onPrivacy} />

      {/* Coming soon toast */}
      {showToast && (
        <div className="pricing-toast" role="status" aria-live="polite">
          {t("pricing.comingSoon")}
        </div>
      )}
    </div>
  )
}
