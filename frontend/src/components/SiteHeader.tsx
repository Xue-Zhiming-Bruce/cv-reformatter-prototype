import { LogoIcon } from "./icons"
import "./SiteHeader.css"

type SiteHeaderProps = {
  activePage?: "home" | "pricing"
  onHome: () => void
  onPricing: () => void
  onLogin: () => void
  onSignup: () => void
}

export function SiteHeader({ activePage, onHome, onPricing, onLogin, onSignup }: SiteHeaderProps) {
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
            Home
          </button>
          <button
            type="button"
            className={`site-nav__btn${activePage === "pricing" ? " site-nav__btn--active" : ""}`}
            onClick={onPricing}
          >
            Pricing
          </button>
        </nav>
        <div className="site-header__auth">
          <button type="button" className="login-button" onClick={onLogin}>Log in</button>
          <button type="button" className="signup-button" onClick={onSignup}>Sign up</button>
        </div>
      </div>
    </header>
  )
}
