import { useState } from "react"
import { Dropzone } from "../components/Dropzone"
import { Chip } from "../components/Chip"
import { FileIcon, GridIcon, LogoIcon, PencilIcon, ShieldCheckIcon } from "../components/icons"
import { Footer } from "../components/Footer"
import "./MainScreen.css"

const SAVED_FORMATS = ["Apex Standard", "Blind", "English"]

type MainScreenProps = {
  onConvert: (resumeFile: File) => void
  isLoading: boolean
  error: string | null
  onDismissError: () => void
  onLogin: () => void
  onSignup: () => void
}

export function MainScreen({ onConvert, isLoading, error, onDismissError, onLogin, onSignup }: MainScreenProps) {
  const [resumeFile, setResumeFile] = useState<File | null>(null)
  const [targetFile, setTargetFile] = useState<File | null>(null)
  const [selectedFormat, setSelectedFormat] = useState<string | null>(null)

  function handleTargetFileSelected(file: File | null) {
    setTargetFile(file)
    if (file) setSelectedFormat(null)
  }

  function handleFormatSelected(format: string) {
    setSelectedFormat((current) => (current === format ? null : format))
    setTargetFile(null)
  }

  return (
    <div className="page-wrapper">
      <header className="site-header">
        <div className="site-header__inner">
          <div className="brand">
            <LogoIcon className="brand__icon" />
            <span className="brand__name">Reform</span>
          </div>
          <nav className="site-nav">
            <a className="site-nav__link" href="#">Home</a>
            <a className="site-nav__link" href="#">Pricing</a>
          </nav>
          <div className="site-header__auth">
            <button type="button" className="login-button" onClick={onLogin}>Log in</button>
            <button type="button" className="signup-button" onClick={onSignup}>Sign up</button>
          </div>
        </div>
      </header>

      <main className="hero-layout">
        <div className="hero-left">
          <h1 className="hero-headline">Reformat<br />any resume.</h1>
          <p className="hero-subtitle">Instantly, in your format.</p>
          <span className="hero-tag">
            <ShieldCheckIcon size={13} />
            Nothing lost
          </span>
          <div className="sample-preview">
            <div className="resume-mock">
              <div className="resume-mock__header">
                <div className="rm-name" />
                <div className="rm-title" />
                <div className="rm-contact" />
              </div>
              <div className="resume-mock__body">
                <div>
                  <div className="rm-section">
                    <div className="rm-section-label" />
                    <div className="rm-line" style={{ width: "92%" }} />
                    <div className="rm-line" style={{ width: "76%" }} />
                    <div className="rm-line" style={{ width: "84%" }} />
                    <div className="rm-line" style={{ width: "61%" }} />
                  </div>
                  <div className="rm-section">
                    <div className="rm-section-label" />
                    <div className="rm-line" style={{ width: "88%" }} />
                    <div className="rm-line" style={{ width: "72%" }} />
                    <div className="rm-line" style={{ width: "80%" }} />
                  </div>
                </div>
                <div>
                  <div className="rm-section">
                    <div className="rm-section-label" />
                    <div className="rm-line" style={{ width: "95%" }} />
                    <div className="rm-line" style={{ width: "80%" }} />
                    <div className="rm-line" style={{ width: "90%" }} />
                  </div>
                  <div className="rm-section">
                    <div className="rm-section-label" />
                    <div className="rm-line" style={{ width: "75%" }} />
                    <div className="rm-line" style={{ width: "88%" }} />
                  </div>
                </div>
              </div>
              <div className="resume-mock__watermark">Sample output</div>
            </div>
          </div>
        </div>

        <div className="upload-card">
            <div className="dropzone-row">
              <Dropzone
                icon={<FileIcon className="dropzone__icon--blue" />}
                title="Received resume"
                subtitle="PDF · DOCX · image"
                file={resumeFile}
                onFileSelected={setResumeFile}
                accept=".docx,.pdf,.png,.jpg,.jpeg"
                variant="primary"
                uploadLabel="Upload resume"
              />
              <div className="dropzone-connector" aria-hidden="true">
                <span className="dropzone-connector__label">reformat into</span>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 18l6-6-6-6" />
                </svg>
              </div>
              <Dropzone
                icon={<GridIcon className="dropzone__icon--green" />}
                title="Your target format"
                subtitle="Template · or saved format"
                file={targetFile}
                onFileSelected={handleTargetFileSelected}
                variant="primary"
                uploadLabel="Upload format"
              />
            </div>

            <div className="format-chips">
              {SAVED_FORMATS.map((format) => (
                <Chip
                  key={format}
                  label={format}
                  selected={selectedFormat === format}
                  onClick={() => handleFormatSelected(format)}
                />
              ))}
            </div>

            {error && (
              <div className="error-banner" role="alert">
                <span>{error}</span>
                <button type="button" className="error-banner__dismiss" onClick={onDismissError} aria-label="Dismiss">
                  ×
                </button>
              </div>
            )}

            <button
              type="button"
              className="convert-button"
              disabled={!resumeFile || isLoading}
              onClick={() => resumeFile && onConvert(resumeFile)}
            >
              {isLoading ? <span className="spinner" aria-hidden="true" /> : <PencilIcon />}
              {isLoading ? "Converting…" : "Convert"}
            </button>

            <p className="upload-card__footer">
              <ShieldCheckIcon className="lossless-footer__icon" size={14} />
              Original preserved · Recruiter approved
            </p>
          </div>
      </main>

      {/* ── Before / After showcase ── */}
      <section className="showcase-section">
        <div className="showcase-inner">
          <div className="showcase-header">
            <h2 className="showcase-heading">Any resume, your format.</h2>
            <p className="showcase-sub">Paste any CV in any layout — we reformat it exactly to yours, every time.</p>
          </div>

          <div className="showcase-cards">
            <div className="showcase-doc">
              <span className="showcase-label">Before</span>
              <div className="showcase-card">
                <div className="doc-before">
                  <div className="doc-before__block">
                    <div className="rm-line" style={{ width: "52%", height: "8px", background: "#374151", borderRadius: "3px" }} />
                    <div className="rm-line" style={{ width: "38%" }} />
                    <div className="rm-line" style={{ width: "70%" }} />
                  </div>
                  <div className="doc-before__block">
                    <div className="rm-line" style={{ width: "93%" }} />
                    <div className="rm-line" style={{ width: "89%" }} />
                    <div className="rm-line" style={{ width: "96%" }} />
                    <div className="rm-line" style={{ width: "87%" }} />
                    <div className="rm-line" style={{ width: "92%" }} />
                    <div className="rm-line" style={{ width: "80%" }} />
                  </div>
                  <div className="doc-before__block">
                    <div className="rm-line" style={{ width: "91%" }} />
                    <div className="rm-line" style={{ width: "94%" }} />
                    <div className="rm-line" style={{ width: "84%" }} />
                    <div className="rm-line" style={{ width: "90%" }} />
                    <div className="rm-line" style={{ width: "76%" }} />
                  </div>
                  <div className="doc-before__block">
                    <div className="rm-line" style={{ width: "88%" }} />
                    <div className="rm-line" style={{ width: "92%" }} />
                    <div className="rm-line" style={{ width: "85%" }} />
                    <div className="rm-line" style={{ width: "79%" }} />
                  </div>
                </div>
              </div>
            </div>

            <div className="showcase-arrow" aria-hidden="true">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
              <span className="showcase-arrow__label">reformatted</span>
            </div>

            <div className="showcase-doc">
              <span className="showcase-label showcase-label--after">After</span>
              <div className="showcase-card">
                <div className="doc-after">
                  <div className="doc-after__header">
                    <div className="rm-name" />
                    <div className="rm-title" />
                    <div className="rm-contact" />
                  </div>
                  <div className="doc-after__body">
                    <div>
                      <div className="doc-after__section">
                        <div className="rm-section-label" />
                        <div className="rm-line" style={{ width: "90%" }} />
                        <div className="rm-line" style={{ width: "74%" }} />
                        <div className="rm-line" style={{ width: "83%" }} />
                        <div className="rm-line" style={{ width: "60%" }} />
                      </div>
                      <div className="doc-after__section">
                        <div className="rm-section-label" />
                        <div className="rm-line" style={{ width: "87%" }} />
                        <div className="rm-line" style={{ width: "71%" }} />
                        <div className="rm-line" style={{ width: "78%" }} />
                      </div>
                      <div className="doc-after__section">
                        <div className="rm-section-label" />
                        <div className="rm-line" style={{ width: "82%" }} />
                        <div className="rm-line" style={{ width: "65%" }} />
                      </div>
                    </div>
                    <div>
                      <div className="doc-after__section">
                        <div className="rm-section-label" />
                        <div className="rm-line" style={{ width: "92%" }} />
                        <div className="rm-line" style={{ width: "78%" }} />
                        <div className="rm-line" style={{ width: "85%" }} />
                      </div>
                      <div className="doc-after__section">
                        <div className="rm-section-label" />
                        <div className="rm-line" style={{ width: "70%" }} />
                        <div className="rm-line" style={{ width: "82%" }} />
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <Footer />
    </div>
  )
}
