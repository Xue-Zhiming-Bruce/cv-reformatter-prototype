import { useState, useEffect, useMemo, useRef } from "react"
import { useTranslation } from "react-i18next"
import { LogoIcon, PencilIcon } from "../components/icons"
import type { ProcessResponse } from "../types"
import "./ReviewScreen.css"

// TODO: When API returns per-field confidence scores, set this to true.
// Expected addition to ProcessResponse: field_confidence?: Record<string, number> (0.0–1.0)
// Fields below 0.7 would be highlighted amber. Render logic gated here.
const SHOW_CONFIDENCE = false

type ReviewScreenProps = {
  data: ProcessResponse
  resumeFile: File
  resumeFileName: string
  formatName: string
  onBack: () => void
}

type EditableFieldProps = {
  value: string | null
  path: string
  edits: Record<string, string>
  onEdit: (path: string, value: string) => void
  isMissing?: boolean
  multiline?: boolean
  className?: string
}

function EditableField({ value, path, edits, onEdit, isMissing, multiline, className }: EditableFieldProps) {
  const { t } = useTranslation()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState("")
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null)

  const effective = path in edits ? edits[path] : (value ?? "")
  const isEmpty = !effective.trim()

  function startEdit() {
    setDraft(effective)
    setEditing(true)
  }

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  function save() {
    onEdit(path, draft.trim())
    setEditing(false)
  }

  function cancel() {
    setEditing(false)
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !multiline) { e.preventDefault(); save() }
    if (e.key === "Escape") cancel()
  }

  if (editing) {
    const cls = `ef-input${multiline ? " ef-input--multiline" : ""}${className ? ` ${className}` : ""}`
    if (multiline) {
      return (
        <textarea
          ref={inputRef as React.RefObject<HTMLTextAreaElement>}
          className={cls}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={save}
          onKeyDown={handleKeyDown}
          rows={3}
        />
      )
    }
    return (
      <input
        ref={inputRef as React.RefObject<HTMLInputElement>}
        className={cls}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={save}
        onKeyDown={handleKeyDown}
      />
    )
  }

  if (isEmpty && isMissing) {
    return (
      <button
        type="button"
        className={`empty-slot${className ? ` ${className}` : ""}`}
        onClick={startEdit}
      >
        <span className="empty-slot__badge">{t("review.emptySlot")}</span>
      </button>
    )
  }

  if (isEmpty) return null

  return (
    <span
      className={`ef${className ? ` ${className}` : ""}`}
      onClick={startEdit}
      role="button"
      tabIndex={0}
      title={t("review.clickToEdit")}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") startEdit() }}
    >
      {effective}
      <PencilIcon className="ef__pencil" size={11} />
    </span>
  )
}

function joinDefined(values: Array<string | null | undefined>, separator = " – "): string {
  return values.filter((v): v is string => Boolean(v?.trim())).join(separator)
}

function pdfViewerUrl(url: string): string {
  return `${url}${url.includes("#") ? "&" : "#"}toolbar=0&navpanes=0&view=FitH`
}

export function ReviewScreen({ data, resumeFile, resumeFileName, formatName, onBack }: ReviewScreenProps) {
  const { t } = useTranslation()
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [blindProfile, setBlindProfile] = useState(false)
  const { profile, ledger } = data

  const missingNames = useMemo(
    () => new Set(profile.missing_fields.map((f) => f.field_name)),
    [profile.missing_fields]
  )

  const objectUrl = useMemo(() => URL.createObjectURL(resumeFile), [resumeFile])
  useEffect(() => () => URL.revokeObjectURL(objectUrl), [objectUrl])

  const isPdf = /\.pdf$/i.test(resumeFile.name) || resumeFile.type === "application/pdf"
  const originalPreviewUrl = data.original_pdf_preview_url ?? (isPdf ? objectUrl : null)
  const originalPdfViewerUrl = originalPreviewUrl ? pdfViewerUrl(originalPreviewUrl) : null

  function handleEdit(path: string, value: string) {
    setEdits((prev) => {
      const next = { ...prev }
      if (value === "") delete next[path]
      else next[path] = value
      return next
    })
  }

  const editCount = Object.keys(edits).length

  function ef(path: string, value: string | null, opts: { multiline?: boolean; className?: string } = {}) {
    return (
      <EditableField
        value={value}
        path={path}
        edits={edits}
        onEdit={handleEdit}
        isMissing={missingNames.has(path)}
        multiline={opts.multiline}
        className={opts.className}
      />
    )
  }

  const contactItems = [
    { key: "email", value: profile.email },
    { key: "phone", value: profile.phone },
    { key: "location", value: profile.location },
    { key: "linkedin_url", value: profile.linkedin_url },
    { key: "portfolio_url", value: profile.portfolio_url },
  ].filter(({ key, value }) => {
    const eff = (edits[key] ?? value ?? "").trim()
    return eff !== "" || missingNames.has(key)
  })

  return (
    <div className="review-page">
      {/* ── Header ── */}
      <header className="rv-header">
        <button type="button" className="brand brand--link rv-brand" onClick={onBack}>
          <LogoIcon className="brand__icon" />
          <span className="brand__name">Reform</span>
        </button>
        <div className="rv-header__meta">
          <span className="rv-header__filename">{resumeFileName}</span>
          <span className="rv-header__arrow" aria-hidden="true">→</span>
          <span className="rv-header__format">{formatName}</span>
        </div>
        <label className="rv-blind-toggle">
          <input
            type="checkbox"
            className="rv-blind-toggle__check"
            checked={blindProfile}
            onChange={(e) => setBlindProfile(e.target.checked)}
          />
          <span className="rv-blind-toggle__body">
            <span className="rv-blind-toggle__label">{t("review.blindProfileLabel")}</span>
            <span className="rv-blind-toggle__hint">{t("review.blindProfileHint")}</span>
          </span>
        </label>
      </header>

      {/* ── Two panes ── */}
      <div className="rv-panes">
        {/* Left — original file */}
        <div className="rv-pane rv-pane--left">
          <div className="pane-label">{t("review.leftLabel")}</div>
          <div className={`pane-body${originalPdfViewerUrl ? " pane-body--pdf" : ""}`}>
            {originalPdfViewerUrl ? (
              <object data={originalPdfViewerUrl} type="application/pdf" className="pdf-embed">
                <p className="pane-fallback">{t("review.pdfFallback")}</p>
              </object>
            ) : (
              <div className="original-text-preview">
                <p className="original-text-preview__title">{t("review.originalTextFallbackTitle")}</p>
                {data.original_preview_error && (
                  <p className="original-text-preview__note">{data.original_preview_error}</p>
                )}
                <pre className="original-text-preview__body">{data.original_text}</pre>
              </div>
            )}
          </div>
        </div>

        {/* Right — editable reformatted doc */}
        <div className="rv-pane rv-pane--right">
          <div className="pane-label">
            {t("review.rightLabel")} · {formatName}
            <span className="pane-label__hint">{t("review.rightLabelHint")}</span>
          </div>
          <div className="pane-body">
            <div className="rdoc">
              {/* Header block */}
              <div className={`rdoc-header${blindProfile ? " rdoc-header--blind" : ""}`}>
                <div className="rdoc-name-row">
                  {ef("full_name", profile.full_name)}
                </div>
                {(profile.work_experience[0]?.title || missingNames.has("current_title")) && (
                  <div className="rdoc-title-row">
                    {ef("current_title", profile.work_experience[0]?.title ?? null)}
                  </div>
                )}
                {contactItems.length > 0 && (
                  <div className="rdoc-contact">
                    {contactItems.map(({ key, value }, i) => (
                      <span key={key} className="rdoc-contact__item-wrap">
                        {i > 0 && <span className="rdoc-sep" aria-hidden="true">·</span>}
                        {ef(key, value)}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Summary */}
              {(profile.professional_summary || missingNames.has("professional_summary")) && (
                <div className="rdoc-section">
                  <div className="rdoc-section__head">{t("review.sectionSummary")}</div>
                  <div className="rdoc-summary">
                    {ef("professional_summary", profile.professional_summary, { multiline: true })}
                  </div>
                </div>
              )}

              {/* Experience */}
              {profile.work_experience.length > 0 && (
                <div className="rdoc-section">
                  <div className="rdoc-section__head">{t("review.sectionExperience")}</div>
                  {profile.work_experience.map((entry, i) => (
                    <div key={i} className="rdoc-entry">
                      <div className="rdoc-entry__top">
                        <span className="rdoc-entry__company">
                          {ef(`work_experience.${i}.company`, entry.company)}
                        </span>
                        {((edits[`work_experience.${i}.company`] ?? entry.company) &&
                          (edits[`work_experience.${i}.title`] ?? entry.title)) && (
                          <span className="rdoc-entry__sep">·</span>
                        )}
                        <span className="rdoc-entry__role">
                          {ef(`work_experience.${i}.title`, entry.title)}
                        </span>
                        <span className="rdoc-entry__dates">
                          {joinDefined([
                            edits[`work_experience.${i}.start_date`] ?? entry.start_date,
                            edits[`work_experience.${i}.end_date`] ?? entry.end_date ?? t("review.present"),
                          ])}
                        </span>
                      </div>
                      {(edits[`work_experience.${i}.location`] ?? entry.location) && (
                        <div className="rdoc-entry__location">
                          {ef(`work_experience.${i}.location`, entry.location)}
                        </div>
                      )}
                      {entry.description.map((bullet, j) => (
                        <div key={j} className="rdoc-entry__bullet">
                          <span className="rdoc-bullet-dot" aria-hidden="true">·</span>
                          <span>{ef(`work_experience.${i}.description.${j}`, bullet)}</span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}

              {/* Education */}
              {profile.education.length > 0 && (
                <div className="rdoc-section">
                  <div className="rdoc-section__head">{t("review.sectionEducation")}</div>
                  {profile.education.map((entry, i) => (
                    <div key={i} className="rdoc-entry">
                      <div className="rdoc-entry__top">
                        <span className="rdoc-entry__company">
                          {ef(`education.${i}.institution`, entry.institution)}
                        </span>
                        {((edits[`education.${i}.institution`] ?? entry.institution) &&
                          (edits[`education.${i}.degree`] ?? entry.degree)) && (
                          <span className="rdoc-entry__sep">·</span>
                        )}
                        <span className="rdoc-entry__role">
                          {ef(`education.${i}.degree`, entry.degree)}
                        </span>
                        {(edits[`education.${i}.field_of_study`] ?? entry.field_of_study) && (
                          <>
                            <span className="rdoc-entry__sep">·</span>
                            {ef(`education.${i}.field_of_study`, entry.field_of_study)}
                          </>
                        )}
                        <span className="rdoc-entry__dates">
                          {joinDefined([
                            edits[`education.${i}.start_date`] ?? entry.start_date,
                            edits[`education.${i}.end_date`] ?? entry.end_date,
                          ])}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Skills */}
              {(profile.skills.length > 0 || missingNames.has("skills")) && (
                <div className="rdoc-section">
                  <div className="rdoc-section__head">{t("review.sectionSkills")}</div>
                  <div className="rdoc-skills">
                    {ef("skills", profile.skills.join(", ") || null, { multiline: false })}
                  </div>
                </div>
              )}

              {/* Languages */}
              {profile.languages.length > 0 && (
                <div className="rdoc-section">
                  <div className="rdoc-section__head">{t("review.sectionLanguages")}</div>
                  <div className="rdoc-skills">
                    {profile.languages.map((lang, i) => (
                      <span key={i}>
                        {ef(`languages.${i}.name`, lang.name)}
                        {(edits[`languages.${i}.proficiency`] ?? lang.proficiency) && (
                          <span className="rdoc-lang-prof">
                            {" ("}
                            {ef(`languages.${i}.proficiency`, lang.proficiency)}
                            {")"}
                          </span>
                        )}
                        {i < profile.languages.length - 1 && <span className="rdoc-sep"> · </span>}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Certifications */}
              {profile.certifications.length > 0 && (
                <div className="rdoc-section">
                  <div className="rdoc-section__head">{t("review.sectionCertifications")}</div>
                  {profile.certifications.map((cert, i) => (
                    <div key={i} className="rdoc-entry">
                      <div className="rdoc-entry__top">
                        <span className="rdoc-entry__company">
                          {ef(`certifications.${i}.name`, cert.name)}
                        </span>
                        {(edits[`certifications.${i}.issuer`] ?? cert.issuer) && (
                          <>
                            <span className="rdoc-entry__sep">·</span>
                            {ef(`certifications.${i}.issuer`, cert.issuer)}
                          </>
                        )}
                        {(edits[`certifications.${i}.date`] ?? cert.date) && (
                          <span className="rdoc-entry__dates">
                            {ef(`certifications.${i}.date`, cert.date)}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Additional */}
              {(profile.work_authorization || profile.notice_period || profile.salary_expectation) && (
                <div className="rdoc-section">
                  <div className="rdoc-section__head rdoc-section__head--muted">{t("review.sectionAdditional")}</div>
                  <div className="rdoc-additional">
                    {ef("work_authorization", profile.work_authorization)}
                    {ef("notice_period", profile.notice_period)}
                    {ef("salary_expectation", profile.salary_expectation)}
                  </div>
                </div>
              )}

              {SHOW_CONFIDENCE && null /* per-field confidence highlights rendered here when enabled */}
            </div>
          </div>
        </div>
      </div>

      {/* ── Ledger bar ── */}
      <footer className="rv-ledger">
        <span className="rv-ledger__label">{t("review.ledgerLabel")}</span>
        <span className="rv-ledger__item">{t("review.ledgerExtracted", { count: ledger.extracted })}</span>
        <span className="rv-ledger__item">{t("review.ledgerPlaced", { count: ledger.placed })}</span>
        {ledger.needs_review > 0
          ? <span className="rv-ledger__item rv-ledger__item--review">{t("review.ledgerNeedsReview", { count: ledger.needs_review })}</span>
          : <span className="rv-ledger__item rv-ledger__item--ok">{t("review.ledgerAllPlaced")}</span>
        }
        {editCount > 0 && (
          <span className="rv-ledger__item rv-ledger__item--edited">{t("review.ledgerEdited", { count: editCount })}</span>
        )}
        {profile.missing_fields.length > 0 && (
          <span className="rv-ledger__missing">
            {t("review.ledgerMissing", { fields: profile.missing_fields.map((f) => f.label).join(" · ") })}
          </span>
        )}
      </footer>

    </div>
  )
}
