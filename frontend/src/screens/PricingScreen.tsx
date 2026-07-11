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
}

export function PricingScreen({ onHome, onPricing, onLogin, onSignup }: PricingScreenProps) {
  const { t, i18n } = useTranslation()
  const [openFaq, setOpenFaq] = useState<number | null>(null)

  const lng = i18n.resolvedLanguage ?? i18n.language
  const p = getPricing(lng)

  const freeFeatures = t("pricing.freeFeatures", { returnObjects: true }) as string[]
  const proFeatures = t("pricing.proFeatures", { returnObjects: true }) as string[]
  const faqItems = t("pricing.faq", { returnObjects: true }) as Array<{ q: string; a: string; isTodo?: boolean }>

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
                <span className="plan-card__period">{t("pricing.perMonth")}</span>
              </div>
            </div>
            <ul className="plan-card__features">
              {freeFeatures.map((f) => (
                <li key={f} className="plan-card__feature">
                  <span className="plan-card__check" aria-hidden="true">✓</span>
                  {f}
                </li>
              ))}
            </ul>
            <button type="button" className="plan-card__cta plan-card__cta--free" onClick={onHome}>
              {t("pricing.startFreeBtn")}
            </button>
            <p className="plan-card__footnote">{t("pricing.freeFootnote")}</p>
          </div>

          {/* Pro — emphasized */}
          <div className="plan-card plan-card--pro">
            <div className="plan-badge">{t("pricing.badge")}</div>
            <div className="plan-card__top">
              <span className="plan-card__name">{t("pricing.proName")}</span>
              <div className="plan-card__price-row">
                <span className="plan-card__price">
                  {p.currency}{p.pro.price.toLocaleString()}
                </span>
                <span className="plan-card__period">{t("pricing.perMonth")}</span>
                <span className="plan-card__original">
                  {p.currency}{p.pro.originalPrice.toLocaleString()}
                </span>
              </div>
            </div>
            <ul className="plan-card__features">
              {proFeatures.map((f) => (
                <li key={f} className="plan-card__feature">
                  <span className="plan-card__check plan-card__check--pro" aria-hidden="true">✓</span>
                  {f}
                </li>
              ))}
            </ul>
            <button type="button" className="plan-card__cta plan-card__cta--pro" onClick={onSignup}>
              {t("pricing.getProBtn")}
            </button>
            <p className="plan-card__footnote plan-card__footnote--pro">{t("pricing.proFootnote")}</p>
          </div>
        </div>
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
                  <p className={`faq-answer${item.isTodo ? " faq-answer--todo" : ""}`}>
                    {item.a}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      <Footer onHome={onHome} onPricing={onPricing} />
    </div>
  )
}
