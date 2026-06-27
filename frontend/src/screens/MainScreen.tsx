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
    <div className="app-card">
      <header className="main-header">
        <div className="brand">
          <LogoIcon className="brand__icon" />
          <span className="brand__name">Reform</span>
        </div>
        <button type="button" className="account-button">
          Account
        </button>
      </header>
      <hr className="divider" />

      <section className="hero">
        <h1>Drop two, you're done</h1>
        <p className="hero__subtitle">Any resume, poured into your format — nothing lost.</p>
      </section>

      <section className="dropzones">
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
      </section>

      <section className="format-chips">
        {SAVED_FORMATS.map((format) => (
          <Chip key={format} label={format} selected={selectedFormat === format} onClick={() => handleFormatSelected(format)} />
        ))}
      </section>

      {error && (
        <div className="error-banner" role="alert">
          <span>{error}</span>
          <button type="button" className="error-banner__dismiss" onClick={onDismissError} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}

      <section className="convert-row">
        <button
          type="button"
          className="convert-button"
          disabled={!resumeFile || isLoading}
          onClick={() => resumeFile && onConvert(resumeFile)}
        >
          {isLoading ? <span className="spinner" aria-hidden="true" /> : <PencilIcon />}
          {isLoading ? "Converting…" : "Convert"}
        </button>
      </section>

      <hr className="divider" />
      <footer className="lossless-footer">
        <ShieldCheckIcon className="lossless-footer__icon" />
        <span>Nothing gets dropped — lossless guarantee</span>
      </footer>
    </div>
  )
}
