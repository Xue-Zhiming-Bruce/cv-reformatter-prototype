import { useTranslation } from "react-i18next"
import { SiteHeader } from "../components/SiteHeader"
import { Footer } from "../components/Footer"
import "./LegalScreen.css"

type LegalScreenProps = {
  page: "privacy" | "terms"
  onHome: () => void
  onPricing: () => void
  onLogin: () => void
  onSignup: () => void
  onTerms: () => void
  onPrivacy: () => void
}

export function LegalScreen({ page, onHome, onPricing, onLogin, onSignup, onTerms, onPrivacy }: LegalScreenProps) {
  const { t } = useTranslation()
  const sections = t(`${page}.sections`, { returnObjects: true }) as Array<{ heading: string }>
  const placeholderBody = t("legal.toBeCompleted")

  return (
    <div className="legal-page">
      <SiteHeader onHome={onHome} onPricing={onPricing} onLogin={onLogin} onSignup={onSignup} />
      <main className="legal-main">
        <div className="legal-doc">
          <p className="legal-draft-banner" role="alert">{t("legal.draftBanner")}</p>
          <h1 className="legal-title">{t(`${page}.title`)}</h1>
          {sections.map((s, i) => (
            <section key={i} className="legal-section">
              <h2 className="legal-h2">{s.heading}</h2>
              <p className="legal-body">{placeholderBody}</p>
            </section>
          ))}
        </div>
      </main>
      <Footer onHome={onHome} onPricing={onPricing} onTerms={onTerms} onPrivacy={onPrivacy} />
    </div>
  )
}
