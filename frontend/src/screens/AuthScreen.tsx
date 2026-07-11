import { useTranslation } from "react-i18next"
import { LogoIcon, GoogleIcon } from "../components/icons"
import "./AuthScreen.css"

type AuthScreenProps = {
  mode: "login" | "signup"
  onSwitchMode: () => void
  onGoHome: () => void
}

export function AuthScreen({ mode, onSwitchMode, onGoHome }: AuthScreenProps) {
  const { t } = useTranslation()
  const isLogin = mode === "login"

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

          <form className="auth-form" onSubmit={(e) => e.preventDefault()}>
            {!isLogin && (
              <div className="field">
                <label className="field__label" htmlFor="auth-name">{t("auth.nameLabel")}</label>
                <input
                  id="auth-name"
                  className="field__input"
                  type="text"
                  placeholder={t("auth.namePlaceholder")}
                  autoComplete="name"
                />
              </div>
            )}

            <div className="field">
              <label className="field__label" htmlFor="auth-email">{t("auth.emailLabel")}</label>
              <input
                id="auth-email"
                className="field__input"
                type="email"
                placeholder={t("auth.emailPlaceholder")}
                autoComplete="email"
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
              />
            </div>

            <button type="submit" className="auth-submit">
              {isLogin ? t("auth.loginBtn") : t("auth.signupBtn")}
            </button>
          </form>

          <div className="auth-divider">
            <span className="auth-divider__text">{t("auth.orDivider")}</span>
          </div>

          <button type="button" className="google-button">
            <GoogleIcon size={18} />
            {isLogin ? t("auth.googleLogin") : t("auth.googleSignup")}
          </button>

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
