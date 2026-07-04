import { useState } from "react"
import { Dropzone } from "../components/Dropzone"
import { Chip } from "../components/Chip"
import { FileIcon, GridIcon, LogoIcon, PencilIcon, ShieldCheckIcon } from "../components/icons"
import "./MainScreen.css"

const SAVED_FORMATS = ["Apex Standard", "Blind", "English"]

type MainScreenProps = {
  onConvert: (resumeFile: File) => void
  isLoading: boolean
  error: string | null
  onDismissError: () => void
}

export function MainScreen({ onConvert, isLoading, error, onDismissError }: MainScreenProps) {
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
            <button type="button" className="login-button">Log in</button>
            <button type="button" className="signup-button">Sign up</button>
          </div>
        </div>
      </header>

      <main className="hero-layout">
        <div className="hero-left">
          <div className="sample-preview">
            <span className="sample-preview__label">Sample output preview</span>
          </div>
          <h1 className="hero-headline">Reformat<br />any resume.</h1>
          <p className="hero-subtitle">Instantly, in your format.</p>
          <span className="hero-tag">
            <ShieldCheckIcon size={13} />
            Lossless
          </span>
        </div>

        <div className="upload-card">
          <Dropzone
            icon={<FileIcon className="dropzone__icon--blue" />}
            title="Received resume"
            subtitle="Candidate's PDF · DOCX · image"
            file={resumeFile}
            onFileSelected={setResumeFile}
            accept=".docx,.pdf,.png,.jpg,.jpeg"
          />
          <Dropzone
            icon={<GridIcon className="dropzone__icon--green" />}
            title="Your target format"
            subtitle="Upload your template · or saved format"
            file={targetFile}
            onFileSelected={handleTargetFileSelected}
          />

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
            Nothing gets dropped — lossless guarantee
          </p>
        </div>
      </main>
    </div>
  )
}
