import { useState } from "react"
import { useTranslation } from "react-i18next"
import { LogoIcon } from "../components/icons"
import { useAuth } from "../context/AuthContext"
import "./AuthScreen.css"

type AuthScreenProps = {
  mode: "login" | "signup"
  onSwitchMode: () => void
  onGoHome: () => void
  onSuccess: () => void
  onTerms: () => void
  onPrivacy: () => void
}

export function AuthScreen({ mode, onSwitchMode, onGoHome, onSuccess, onTerms, onPrivacy }: AuthScreenProps) {
  const { t } = useTranslation()
  const { signup, login } = useAuth()
  const isLogin = mode === "login"

  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [agreedToLegal, setAgreedToLegal] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const consentTemplate = t("auth.consentText", { terms: "%%TERMS%%", privacy: "%%PRIVACY%%" })
  const [consentBeforeTerms, consentAfterTerms] = consentTemplate.split("%%TERMS%%")
  const [consentBetween, consentAfterPrivacy] = consentAfterTerms.split("%%PRIVACY%%")

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!isLogin && !agreedToLegal) {
      return
    }
    if (!isLogin && password.length < 8) {
      setError(t("auth.errorPasswordTooShort"))
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      if (isLogin) {
        await login(email, password)
      } else {
        await signup(email, password)
      }
      onSuccess()
    } catch (err: unknown) {
      const raw = err instanceof Error ? err.message : ""
      setError(_mapError(raw, t))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page-wrapper">
      <header className="site-header">
        <div className="site-header__inner">
          <button type="button" className="brand brand--btn" onClick={onGoHome} aria-label="Go to home">
            <LogoIcon className="brand__icon" />
            <span className="brand__name">Reform</span>
          </button>
        </div>
      </header>

      <main className="auth-layout">
        <div className="auth-card">
          <div className="auth-card__heading">
            <h1 className="auth-title">
              {isLogin ? t("auth.loginTitle") : t("auth.signupTitle")}
            </h1>
            <p className="auth-subtitle">
              {isLogin ? t("auth.loginSubtitle") : t("auth.signupSubtitle")}
            </p>
          </div>

          <form className="auth-form" onSubmit={handleSubmit}>
            <div className="field">
              <label className="field__label" htmlFor="auth-email">{t("auth.emailLabel")}</label>
              <input
                id="auth-email"
                className="field__input"
                type="email"
                placeholder={t("auth.emailPlaceholder")}
                autoComplete="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
              />
            </div>

            <div className="field">
              <label className="field__label" htmlFor="auth-password">{t("auth.passwordLabel")}</label>
              <input
                id="auth-password"
                className="field__input"
                type="password"
                placeholder="••••••••"
                autoComplete={isLogin ? "current-password" : "new-password"}
                value={password}
                onChange={e => setPassword(e.target.value)}
                required
              />
            </div>

            {!isLogin && (
              <label className="auth-consent">
                <input
                  type="checkbox"
                  className="auth-consent__checkbox"
                  checked={agreedToLegal}
                  onChange={e => setAgreedToLegal(e.target.checked)}
                />
                <span className="auth-consent__text">
                  {consentBeforeTerms}
                  <button type="button" className="auth-consent__link" onClick={onTerms}>
                    {t("auth.consentTermsLabel")}
                  </button>
                  {consentBetween}
                  <button type="button" className="auth-consent__link" onClick={onPrivacy}>
                    {t("auth.consentPrivacyLabel")}
                  </button>
                  {consentAfterPrivacy}
                </span>
              </label>
            )}

            {error && <p className="auth-error" role="alert">{error}</p>}

            <button type="submit" className="auth-submit" disabled={submitting || (!isLogin && !agreedToLegal)}>
              {submitting ? "…" : (isLogin ? t("auth.loginBtn") : t("auth.signupBtn"))}
            </button>
          </form>

          <p className="auth-switch">
            {isLogin ? t("auth.noAccount") : t("auth.hasAccount")}{" "}
            <button type="button" className="auth-switch__link" onClick={onSwitchMode}>
              {isLogin ? t("auth.switchToSignup") : t("auth.switchToLogin")}
            </button>
          </p>
        </div>
      </main>
    </div>
  )
}

function _mapError(msg: string, t: (k: string) => string): string {
  if (msg.includes("Email already registered")) return t("auth.errorEmailTaken")
  if (msg.includes("Invalid email or password")) return t("auth.errorInvalidCredentials")
  return msg || t("auth.errorGeneric")
}
