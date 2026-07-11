import { useState, useRef } from "react"
import { Dropzone } from "../components/Dropzone"
import { GridIcon, PencilIcon } from "../components/icons"
import { SiteHeader } from "../components/SiteHeader"
import { Footer } from "../components/Footer"
import "./MainScreen.css"

// Insertion point: saved templates for logged-in users will be added to this array
const formats: Array<{ id: string; label: string }> = []

type MainScreenProps = {
  onConvert: (resumeFile: File) => void
  isLoading: boolean
  error: string | null
  onDismissError: () => void
  onLogin: () => void
  onSignup: () => void
  onPricing: () => void
}

export function MainScreen({ onConvert, isLoading, error, onDismissError, onLogin, onSignup, onPricing }: MainScreenProps) {
  const [resumeFile, setResumeFile] = useState<File | null>(null)
  const [templateFile, setTemplateFile] = useState<File | null>(null)
  const [sampleLoading, setSampleLoading] = useState(false)
  const [pageDragging, setPageDragging] = useState(false)
  const resumeInputRef = useRef<HTMLInputElement>(null)

  const canConvert = !!resumeFile && !!templateFile && !isLoading

  async function handleLoadSample() {
    setSampleLoading(true)
    try {
      const res = await fetch("/sample-resume.docx")
      const blob = await res.blob()
      setResumeFile(new File([blob], "sample-resume.docx", {
        type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      }))
    } catch {
      // silently fail
    } finally {
      setSampleLoading(false)
    }
  }

  function handlePageDragOver(e: React.DragEvent) {
    e.preventDefault()
    if (!resumeFile) setPageDragging(true)
  }

  function handlePageDragLeave(e: React.DragEvent) {
    if (!e.relatedTarget || !e.currentTarget.contains(e.relatedTarget as Node)) {
      setPageDragging(false)
    }
  }

  function handlePageDrop(e: React.DragEvent) {
    e.preventDefault()
    setPageDragging(false)
    if (!resumeFile) {
      const file = e.dataTransfer.files?.[0]
      if (file) setResumeFile(file)
    }
  }

  return (
    <div
      className="page-wrapper"
      onDragOver={handlePageDragOver}
      onDragLeave={handlePageDragLeave}
      onDrop={handlePageDrop}
    >
      {pageDragging && (
        <div className="page-drop-overlay">
          <div className="page-drop-overlay__box">
            <p className="page-drop-overlay__text">Drop to upload resume</p>
          </div>
        </div>
      )}

      <SiteHeader
        activePage="home"
        onHome={() => { setResumeFile(null); setTemplateFile(null) }}
        onPricing={onPricing}
        onLogin={onLogin}
        onSignup={onSignup}
      />

      {/* ── Step 1: Upload resume — full-viewport hero ── */}
      <section className={`hero-primary${resumeFile ? " hero-primary--compact" : ""}`}>
        <div className="hero-primary__inner">
          <h1 className="hero-headline">
            Any resume <span className="hero-headline__arrow">→</span> your format.
          </h1>
          <p className="hero-sub">Upload any CV and your template — get it back perfectly reformatted. Nothing lost.</p>

          {!resumeFile ? (
            <div className="upload-zone">
              <input
                ref={resumeInputRef}
                type="file"
                accept=".docx,.pdf,.png,.jpg,.jpeg"
                className="file-input-hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) setResumeFile(f)
                }}
              />
              <button
                type="button"
                className="upload-cta"
                onClick={() => resumeInputRef.current?.click()}
              >
                Upload resume
              </button>
              <p className="upload-hint">or drop it here · PDF, DOCX, image</p>

              {/* Compact before→after strip */}
              <div className="baa-strip" aria-hidden="true">
                <div className="baa-mini">
                  <span className="baa-mini__label">Their resume</span>
                  <div className="baa-mini__doc baa-mini__doc--before">
                    <div className="baa-mini__bar baa-mini__bar--name" />
                    <div className="baa-mini__bar baa-mini__bar--sub" />
                    <div className="baa-mini__gap" />
                    <div className="baa-mini__bar baa-before-bar" style={{ width: "96%" }} />
                    <div className="baa-mini__bar baa-before-bar" style={{ width: "88%", animationDelay: "0.15s" }} />
                    <div className="baa-mini__bar baa-before-bar" style={{ width: "93%", animationDelay: "0.05s" }} />
                    <div className="baa-mini__bar baa-before-bar" style={{ width: "79%", animationDelay: "0.2s" }} />
                    <div className="baa-mini__gap" />
                    <div className="baa-mini__bar baa-before-bar" style={{ width: "91%", animationDelay: "0.1s" }} />
                    <div className="baa-mini__bar baa-before-bar" style={{ width: "72%", animationDelay: "0.08s" }} />
                  </div>
                </div>

                <div className="baa-strip__arrow">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M5 12h14M13 6l6 6-6 6" />
                  </svg>
                </div>

                <div className="baa-mini">
                  <span className="baa-mini__label">Your template</span>
                  <div className="baa-mini__doc baa-mini__doc--after">
                    <div className="baa-mini__header" />
                    <div className="baa-mini__section">
                      <div className="baa-after-label" />
                      <div className="baa-mini__bar baa-after-bar" style={{ width: "85%" }} />
                      <div className="baa-mini__bar baa-after-bar" style={{ width: "68%", animationDelay: "0.12s" }} />
                    </div>
                    <div className="baa-mini__section">
                      <div className="baa-after-label" style={{ animationDelay: "0.22s" }} />
                      <div className="baa-mini__bar baa-after-bar" style={{ width: "90%", animationDelay: "0.3s" }} />
                      <div className="baa-mini__bar baa-after-bar" style={{ width: "61%", animationDelay: "0.42s" }} />
                    </div>
                  </div>
                </div>
              </div>

              <button
                type="button"
                className="sample-link"
                onClick={handleLoadSample}
                disabled={sampleLoading}
              >
                {sampleLoading ? "Loading…" : "Try a sample resume →"}
              </button>
            </div>
          ) : (
            <div className="resume-badge">
              <span className="resume-badge__check">✓</span>
              <span className="resume-badge__name">{resumeFile.name}</span>
              <button
                type="button"
                className="resume-badge__change"
                onClick={() => { setResumeFile(null); setTemplateFile(null) }}
              >
                Change
              </button>
            </div>
          )}
        </div>
      </section>

      {/* ── Step 2: Template upload — slides in after resume is selected ── */}
      {resumeFile && (
        <section className="step2">
          <div className="step2__inner">
            <div className="step2__connector" aria-hidden="true">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 5v14M5 12l7 7 7-7" />
              </svg>
              <span className="step2__connector-label">reformat into</span>
            </div>

            <Dropzone
              icon={<GridIcon className="dropzone__icon--green" />}
              title="Upload your template"
              subtitle="DOCX or PDF · the format to apply"
              file={templateFile}
              onFileSelected={setTemplateFile}
              accept=".docx,.pdf"
              variant="primary"
              uploadLabel="Upload template"
            />

            {/* formats insertion point — logged-in users' saved templates render here */}
            {formats.length > 0 && (
              <div className="saved-formats">
                {formats.map((f) => (
                  <button key={f.id} type="button" className="saved-format-chip">{f.label}</button>
                ))}
              </div>
            )}

            {error && (
              <div className="error-banner" role="alert">
                <span>{error}</span>
                <button type="button" className="error-banner__dismiss" onClick={onDismissError} aria-label="Dismiss">×</button>
              </div>
            )}

            <div className="convert-area">
              <button
                type="button"
                className={`convert-button${canConvert ? " convert-button--ready" : ""}`}
                disabled={!canConvert}
                onClick={() => resumeFile && onConvert(resumeFile)}
              >
                {isLoading ? <span className="spinner" aria-hidden="true" /> : <PencilIcon />}
                {isLoading ? "Converting…" : "Convert"}
              </button>
              {!templateFile && !isLoading && (
                <p className="convert-hint">Upload your template to convert</p>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ── How it works ── */}
      <section className="how-section">
        <div className="how-inner">
          <div className="how-header">
            <h2 className="how-heading">How it works</h2>
            <p className="how-sub">Three steps. About 20 seconds.</p>
          </div>
          <div className="how-steps">
            <div className="how-step">
              <div className="how-step__icon-wrap">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="17 8 12 3 7 8" />
                  <line x1="12" y1="3" x2="12" y2="15" />
                </svg>
              </div>
              <span className="how-step__num">01</span>
              <h3 className="how-step__title">Upload any resume</h3>
              <p className="how-step__body">PDF, DOCX, or scanned image — we handle every format.</p>
            </div>
            <div className="how-connector" aria-hidden="true">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </div>
            <div className="how-step">
              <div className="how-step__icon-wrap">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="3" y="3" width="18" height="18" rx="2" />
                  <path d="M3 9h18M9 21V9" />
                </svg>
              </div>
              <span className="how-step__num">02</span>
              <h3 className="how-step__title">Upload your template</h3>
              <p className="how-step__body">The DOCX or PDF that defines the format you want applied.</p>
            </div>
            <div className="how-connector" aria-hidden="true">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12h14M13 6l6 6-6 6" />
              </svg>
            </div>
            <div className="how-step">
              <div className="how-step__icon-wrap">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              </div>
              <span className="how-step__num">03</span>
              <h3 className="how-step__title">Review and download</h3>
              <p className="how-step__body">Check every field side by side, edit anything, then export.</p>
            </div>
          </div>
        </div>
      </section>

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
                <div className="resume-preview resume-preview--before">
                  <p className="rp-messy-name">Alex Kim &nbsp; New York | alex.kim@gmail.com | 555-0171</p>
                  <p className="rp-messy-head">EXPERIENCE</p>
                  <p className="rp-messy-role">Senior SWE @ Google Inc. Jan 2020 - present</p>
                  <p className="rp-messy-line">- built and maintained microservices</p>
                  <p className="rp-messy-line">- worked primarily in Python and Go</p>
                  <p className="rp-messy-line">- cross functional team collaboration</p>
                  <p className="rp-messy-role" style={{ marginTop: 8 }}>Software Dev, StartupCo 2017-2020</p>
                  <p className="rp-messy-line">Built mobile features, reduced load time 30%. Managed juniors.</p>
                  <p className="rp-messy-head">EDUCATION</p>
                  <p className="rp-messy-line">NYU, BS Computer Science, 2017</p>
                  <p className="rp-messy-head">SKILLS</p>
                  <p className="rp-messy-line">Python, Go, TypeScript, React, PostgreSQL, Docker</p>
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
              <div className="showcase-card showcase-card--after">
                <div className="resume-preview resume-preview--after">
                  <p className="rp-name">Alex Kim</p>
                  <p className="rp-title">Senior Software Engineer</p>
                  <p className="rp-meta">alex.kim@gmail.com · 555-0171 · New York, NY</p>
                  <div className="rp-section">
                    <div className="rp-section-head">Experience</div>
                    <div className="rp-entry">
                      <p className="rp-entry-head">Google Inc. · Senior SWE · Jan 2020 – Present</p>
                      <p className="rp-entry-body">Architected microservices serving 50M+ requests/day</p>
                      <p className="rp-entry-body">Mentored 2 junior engineers; drove cross-functional delivery</p>
                    </div>
                    <div className="rp-entry">
                      <p className="rp-entry-head">StartupCo · Software Developer · 2017 – 2020</p>
                      <p className="rp-entry-body">Reduced mobile load time 30% via caching</p>
                    </div>
                  </div>
                  <div className="rp-section">
                    <div className="rp-section-head">Education</div>
                    <p className="rp-entry-head">New York University · B.S. Computer Science · 2017</p>
                  </div>
                  <div className="rp-section">
                    <div className="rp-section-head">Skills</div>
                    <p className="rp-entry-body">Python · Go · TypeScript · React · PostgreSQL · Docker</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Why Reform ── */}
      <section className="features-section">
        <div className="features-header">
          <h2 className="features-heading">Why Reform</h2>
        </div>
        <div className="features-inner">
          <div className="feature-card">
            <div className="feature-card__num">01</div>
            <h3 className="feature-card__title">Nothing invented, nothing lost</h3>
            <p className="feature-card__body">We never fabricate content. Anything we can't find in the original is flagged for you to fill — never silently invented.</p>
          </div>
          <div className="feature-card">
            <div className="feature-card__num">02</div>
            <h3 className="feature-card__title">Review before it ships</h3>
            <p className="feature-card__body">Side-by-side review screen shows the original next to the reformatted result. Click any field to edit before you export.</p>
          </div>
          <div className="feature-card">
            <div className="feature-card__num">03</div>
            <h3 className="feature-card__title">About 20 seconds</h3>
            <p className="feature-card__body">From upload to formatted output in roughly twenty seconds. No queue, no account required to get started.</p>
          </div>
        </div>
      </section>

      {/* Trust strip */}
      <div className="trust-strip">
        <span>1 resume ≈ 20 seconds</span>
        <span className="trust-sep" aria-hidden="true">·</span>
        <span>Nothing invented, nothing lost</span>
        <span className="trust-sep" aria-hidden="true">·</span>
        <span>★ Trusted by recruiters worldwide</span>
      </div>

      <Footer onHome={() => { setResumeFile(null); setTemplateFile(null) }} onPricing={onPricing} />
    </div>
  )
}
