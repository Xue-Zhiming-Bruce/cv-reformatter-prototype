import { useState } from "react"
import { DownloadIcon, LogoIcon } from "../components/icons"
import type { ProcessResponse } from "../types"
import "./ReviewScreen.css"

type ReviewScreenProps = {
  data: ProcessResponse
  resumeFileName: string
  formatName: string
  onBack: () => void
}

function joinDefined(values: Array<string | null | undefined>, separator = " · "): string {
  return values.filter((value): value is string => Boolean(value && value.trim())).join(separator)
}

export function ReviewScreen({ data, resumeFileName, formatName, onBack }: ReviewScreenProps) {
  const [anonymize, setAnonymize] = useState(false)
  const { profile, ledger, original_text } = data

  const headerLine = joinDefined([profile.full_name, profile.work_experience[0]?.title])
  const additionalLine = joinDefined([
    profile.work_authorization,
    profile.portfolio_url ? `Portfolio: ${profile.portfolio_url}` : null,
  ])
  const missingLabels = profile.missing_fields.map((field) => field.label).join(" · ")

  return (
    <div className="app-card review-card">
      <header className="review-header">
        <button type="button" className="brand brand--link" onClick={onBack}>
          <LogoIcon className="brand__icon" />
          <span className="brand__name">Reform</span>
        </button>
        <span className="review-header__file">{resumeFileName}</span>
        <span className="review-header__format">{formatName}</span>
        <div className="review-header__actions">
          <label className="anonymize-toggle">
            <span>Anonymize</span>
            <span
              className={`toggle-switch${anonymize ? " toggle-switch--on" : ""}`}
              role="switch"
              aria-checked={anonymize}
              tabIndex={0}
              onClick={() => setAnonymize((value) => !value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") setAnonymize((value) => !value)
              }}
            >
              <span className="toggle-switch__knob" />
            </span>
          </label>
          <button type="button" className="export-button">
            <DownloadIcon />
            Export
          </button>
        </div>
      </header>
      <hr className="divider" />

      <section className="review-columns">
        <div className="review-column">
          <h2 className="review-column__title">Original (as received)</h2>
          <div className="original-text">
            {original_text.split("\n").map((line, index) => (
              <p key={index}>{line}</p>
            ))}
          </div>
        </div>

        <div className="review-column review-column--converted">
          <h2 className="review-column__title review-column__title--accent">Converted · {formatName}</h2>

          {headerLine && <p className="converted-name">{headerLine}</p>}

          {profile.professional_summary && <p className="converted-summary">{profile.professional_summary}</p>}

          {profile.work_experience.length > 0 && (
            <div className="converted-section">
              <h3>Experience</h3>
              {profile.work_experience.map((entry, index) => (
                <div key={index} className="experience-entry">
                  <p className="experience-entry__title">
                    {joinDefined([entry.company, entry.title, joinDefined([entry.start_date, entry.end_date], "–")])}
                  </p>
                  {entry.description.length > 0 && <p className="experience-entry__description">{entry.description.join(" · ")}</p>}
                </div>
              ))}
            </div>
          )}

          {profile.skills.length > 0 && (
            <div className="converted-section">
              <h3>Skills</h3>
              <p>{profile.skills.join(" · ")}</p>
            </div>
          )}

          {profile.languages.length > 0 && (
            <div className="converted-section">
              <h3>Languages</h3>
              <p>{profile.languages.map((lang) => joinDefined([lang.name, lang.proficiency])).join(" · ")}</p>
            </div>
          )}

          {profile.education.length > 0 && (
            <div className="converted-section">
              <h3>Education</h3>
              {profile.education.map((entry, index) => (
                <p key={index}>{joinDefined([entry.institution, entry.degree, entry.field_of_study])}</p>
              ))}
            </div>
          )}

          {profile.certifications.length > 0 && (
            <div className="converted-section">
              <h3>Certifications</h3>
              <p>{profile.certifications.map((cert) => joinDefined([cert.name, cert.issuer, cert.date])).join(" · ")}</p>
            </div>
          )}

          {additionalLine && (
            <div className="converted-section">
              <h3 className="converted-section__title--accent">Additional · preserved</h3>
              <p>{additionalLine}</p>
            </div>
          )}
        </div>
      </section>

      <hr className="divider" />
      <footer className="ledger-bar">
        <span className="ledger-bar__label">Lossless ledger</span>
        <span>Extracted {ledger.extracted}</span>
        <span>Placed {ledger.placed}</span>
        <span className="needs-review-pill">Needs review {ledger.needs_review}</span>
        {missingLabels && <span className="ledger-bar__missing">{missingLabels} missing</span>}
      </footer>
    </div>
  )
}
