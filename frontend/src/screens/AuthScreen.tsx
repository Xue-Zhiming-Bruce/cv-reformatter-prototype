import { LogoIcon, GoogleIcon } from "../components/icons"
import "./AuthScreen.css"

type AuthScreenProps = {
  mode: "login" | "signup"
  onSwitchMode: () => void
  onGoHome: () => void
}

export function AuthScreen({ mode, onSwitchMode, onGoHome }: AuthScreenProps) {
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
              {isLogin ? "Welcome back" : "Create your account"}
            </h1>
            <p className="auth-subtitle">
              {isLogin ? "Sign in to Reform" : "Start reformatting resumes"}
            </p>
          </div>

          <form className="auth-form" onSubmit={(e) => e.preventDefault()}>
            {!isLogin && (
              <div className="field">
                <label className="field__label" htmlFor="auth-name">Full name</label>
                <input
                  id="auth-name"
                  className="field__input"
                  type="text"
                  placeholder="Jane Smith"
                  autoComplete="name"
                />
              </div>
            )}

            <div className="field">
              <label className="field__label" htmlFor="auth-email">Email address</label>
              <input
                id="auth-email"
                className="field__input"
                type="email"
                placeholder="jane@example.com"
                autoComplete="email"
              />
            </div>

            <div className="field">
              <label className="field__label" htmlFor="auth-password">Password</label>
              <input
                id="auth-password"
                className="field__input"
                type="password"
                placeholder="••••••••"
                autoComplete={isLogin ? "current-password" : "new-password"}
              />
            </div>

            <button type="submit" className="auth-submit">
              {isLogin ? "Log in" : "Sign up"}
            </button>
          </form>

          <div className="auth-divider">
            <span className="auth-divider__text">or</span>
          </div>

          <button type="button" className="google-button">
            <GoogleIcon size={18} />
            {isLogin ? "Sign in with Google" : "Sign up with Google"}
          </button>

          <p className="auth-switch">
            {isLogin ? "Don't have an account?" : "Already have an account?"}{" "}
            <button type="button" className="auth-switch__link" onClick={onSwitchMode}>
              {isLogin ? "Sign up" : "Log in"}
            </button>
          </p>
        </div>
      </main>
    </div>
  )
}
