import { useState } from "react"
import { SiteHeader } from "../components/SiteHeader"
import { Footer } from "../components/Footer"
import { PRICING } from "../constants/pricing"
import "./PricingScreen.css"

const { currency, free, pro } = PRICING

const FREE_FEATURES = [
  `${free.conversionsPerMonth} conversions per month`,
  "Full review & edit screen",
  "Export to JSON",
  "No credit card required",
]

const PRO_FEATURES = [
  "Unlimited conversions (fair use)",
  "Saved templates",
  "Priority processing",
  "Everything in Free",
]

const FAQ: Array<{ q: string; a: string; isTodo?: boolean }> = [
  {
    q: "What if the result has mistakes?",
    a: "You review and edit every field on the review screen before exporting. Nothing ships until you say so.",
  },
  {
    q: "What happens to uploaded files?",
    a: "TODO: Files are processed in memory and not permanently stored on our servers. Update with real data-retention policy before launch.",
    isTodo: true,
  },
  {
    q: "Can I cancel anytime?",
    a: "Yes — Pro is billed monthly with no lock-in. Cancel any time from your account settings.",
  },
]

type PricingScreenProps = {
  onHome: () => void
  onPricing: () => void
  onLogin: () => void
  onSignup: () => void
}

export function PricingScreen({ onHome, onPricing, onLogin, onSignup }: PricingScreenProps) {
  const [openFaq, setOpenFaq] = useState<number | null>(null)

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
        <h1 className="pricing-hero__heading">Simple pricing</h1>
        <p className="pricing-hero__sub">Start free. Upgrade when you need more.</p>
      </section>

      {/* ── Plan cards ── */}
      <section className="pricing-cards-section">
        <div className="pricing-cards">
          {/* Free */}
          <div className="plan-card">
            <div className="plan-card__top">
              <span className="plan-card__name">Free</span>
              <div className="plan-card__price-row">
                <span className="plan-card__price">{currency}0</span>
                <span className="plan-card__period">/month</span>
              </div>
            </div>
            <ul className="plan-card__features">
              {FREE_FEATURES.map((f) => (
                <li key={f} className="plan-card__feature">
                  <span className="plan-card__check" aria-hidden="true">✓</span>
                  {f}
                </li>
              ))}
            </ul>
            <button type="button" className="plan-card__cta plan-card__cta--free" onClick={onHome}>
              Start free
            </button>
            <p className="plan-card__footnote">No credit card required</p>
          </div>

          {/* Pro — emphasized */}
          <div className="plan-card plan-card--pro">
            <div className="plan-badge">{pro.badge}</div>
            <div className="plan-card__top">
              <span className="plan-card__name">Pro</span>
              <div className="plan-card__price-row">
                <span className="plan-card__price">{currency}{pro.price.toLocaleString()}</span>
                <span className="plan-card__period">/month</span>
                <span className="plan-card__original">{currency}{pro.originalPrice.toLocaleString()}</span>
              </div>
            </div>
            <ul className="plan-card__features">
              {PRO_FEATURES.map((f) => (
                <li key={f} className="plan-card__feature">
                  <span className="plan-card__check plan-card__check--pro" aria-hidden="true">✓</span>
                  {f}
                </li>
              ))}
            </ul>
            <button type="button" className="plan-card__cta plan-card__cta--pro" onClick={onSignup}>
              Get Pro
            </button>
            <p className="plan-card__footnote plan-card__footnote--pro">Cancel anytime · no lock-in</p>
          </div>
        </div>
      </section>

      {/* ── FAQ ── */}
      <section className="faq-section">
        <div className="faq-inner">
          <h2 className="faq-heading">Frequently asked</h2>
          <div className="faq-list">
            {FAQ.map((item, i) => (
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
