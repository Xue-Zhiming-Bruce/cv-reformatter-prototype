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
  isHighlighted?: boolean
  multiline?: boolean
  className?: string
}

function EditableField({ value, path, edits, onEdit, isMissing, isHighlighted, multiline, className }: EditableFieldProps) {
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
        className={`empty-slot${isHighlighted ? " empty-slot--active" : ""}${className ? ` ${className}` : ""}`}
        onClick={startEdit}
        data-review-field={path}
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
  const [exportState, setExportState] = useState<"idle" | "confirm" | "generating" | "error">("idle")
  const [pendingFormat, setPendingFormat] = useState<"docx" | "pdf" | null>(null)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [reviewCursor, setReviewCursor] = useState(0)
  const [highlightedField, setHighlightedField] = useState<string | null>(null)
  const exportAreaRef = useRef<HTMLDivElement>(null)
  const rightPaneBodyRef = useRef<HTMLDivElement>(null)
  const [previewMode, setPreviewMode] = useState(false)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewState, setPreviewState] = useState<"idle" | "generating" | "error">("idle")
  const { profile, ledger } = data

  const missingNames = useMemo(
    () => new Set(profile.missing_fields.map((f) => f.field_name)),
    [profile.missing_fields]
  )

  const objectUrl = useMemo(() => URL.createObjectURL(resumeFile), [resumeFile])
  useEffect(() => () => URL.revokeObjectURL(objectUrl), [objectUrl])

  useEffect(() => {
    if (!dropdownOpen) return
    function handleClickOutside(e: MouseEvent) {
      if (exportAreaRef.current && !exportAreaRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [dropdownOpen])

  const isPdf = /\.pdf$/i.test(resumeFile.name) || resumeFile.type === "application/pdf"
  const originalPreviewUrl = data.original_pdf_preview_url ?? (isPdf ? objectUrl : null)
  const originalPdfViewerUrl = originalPreviewUrl ? pdfViewerUrl(originalPreviewUrl) : null

  function handleEdit(path: string, value: string) {
    if (path === highlightedField && value.trim()) {
      setHighlightedField(null)
    }
    setEdits((prev) => {
      const next = { ...prev }
      if (value === "") delete next[path]
      else next[path] = value
      return next
    })
  }

  function handleReviewChipClick() {
    const unresolved = profile.missing_fields.filter((f) => {
      const edited = edits[f.field_name]
      return !edited || !edited.trim()
    })
    if (!unresolved.length) return
    const idx = reviewCursor % unresolved.length
    const fieldName = unresolved[idx].field_name
    setHighlightedField(fieldName)
    setReviewCursor(reviewCursor + 1)
    const el = rightPaneBodyRef.current?.querySelector(`[data-review-field="${fieldName}"]`)
    el?.scrollIntoView({ behavior: "smooth", block: "center" })
  }

  const editCount = Object.keys(edits).length

  const remainingMissing = profile.missing_fields.filter((f) => {
    const edited = edits[f.field_name]
    return !edited || !edited.trim()
  }).length

  function applyEditsToProfile(p: typeof profile): typeof profile {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const copy = JSON.parse(JSON.stringify(p)) as any
    for (const [path, value] of Object.entries(edits)) {
      if (path === "skills") {
        copy.skills = value.split(",").map((s: string) => s.trim()).filter(Boolean)
        continue
      }
      const parts = path.split(".")
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      let node: any = copy
      for (let i = 0; i < parts.length - 1; i++) {
        const key = parts[i]
        node = node[isNaN(Number(key)) ? key : Number(key)]
      }
      const last = parts[parts.length - 1]
      node[isNaN(Number(last)) ? last : Number(last)] = value
      // Clear PENDING_CONFIRMATION so the backend uses the filled value instead of "To be confirmed"
      if (copy.client_display_rules[parts[0]] === "pending_confirmation") {
        delete copy.client_display_rules[parts[0]]
      }
    }
    return copy as typeof profile
  }

  async function doGenerate(format: "docx" | "pdf") {
    setDropdownOpen(false)
    setExportState("generating")
    setPendingFormat(null)
    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          profile: applyEditsToProfile(profile),
          blind_profile: blindProfile,
          artifact_id: data.artifact_id,
          original_text: data.original_text,
        }),
      })
      if (!res.ok) {
        setExportState("error")
        setTimeout(() => setExportState("idle"), 3500)
        return
      }
      const result = await res.json()
      const url: string = format === "docx" ? result.docx_download_url : result.pdf_download_url
      const a = document.createElement("a")
      a.href = url
      a.download = format === "docx" ? "resume.docx" : "resume.pdf"
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      setExportState("idle")
    } catch {
      setExportState("error")
      setTimeout(() => setExportState("idle"), 3500)
    }
  }

  function handleExportClick(format: "docx" | "pdf") {
    setDropdownOpen(false)
    if (remainingMissing > 0) {
      setPendingFormat(format)
      setExportState("confirm")
    } else {
      doGenerate(format)
    }
  }

  async function handleFinalPreview() {
    if (previewState === "generating") return
    setPreviewState("generating")
    setPreviewMode(false)
    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          profile: applyEditsToProfile(profile),
          blind_profile: blindProfile,
          artifact_id: data.artifact_id,
          original_text: data.original_text,
        }),
      })
      if (!res.ok) {
        setPreviewState("error")
        setTimeout(() => setPreviewState("idle"), 3500)
        return
      }
      const result = await res.json()
      setPreviewUrl(result.pdf_preview_url)
      setPreviewMode(true)
      setPreviewState("idle")
    } catch {
      setPreviewState("error")
      setTimeout(() => setPreviewState("idle"), 3500)
    }
  }

  function ef(path: string, value: string | null, opts: { multiline?: boolean; className?: string } = {}) {
    return (
      <EditableField
        value={value}
        path={path}
        edits={edits}
        onEdit={handleEdit}
        isMissing={missingNames.has(path)}
        isHighlighted={path === highlightedField}
        multiline={opts.multiline}
        className={opts.className}
      />
    )
  }


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

        <button
          type="button"
          className={`rv-preview-btn${previewState === "error" ? " rv-preview-btn--error" : ""}`}
          disabled={previewState === "generating"}
          onClick={handleFinalPreview}
        >
          {previewState === "generating" ? (
            <><span className="rv-spinner" aria-hidden="true" />{t("review.finalPreviewGenerating")}</>
          ) : previewState === "error" ? (
            t("review.finalPreviewError")
          ) : (
            t("review.finalPreviewBtn")
          )}
        </button>

        <div className="rv-export-area" ref={exportAreaRef}>
          {exportState === "confirm" ? (
            <div className="rv-export-confirm">
              <span className="rv-export-confirm__msg">
                {t("review.exportConfirm", { count: remainingMissing })}
              </span>
              <button
                type="button"
                className="rv-export-cancel-btn"
                onClick={() => { setExportState("idle"); setPendingFormat(null) }}
              >
                {t("review.exportCancel")}
              </button>
              <button
                type="button"
                className="rv-export-anyway-btn"
                onClick={() => pendingFormat && doGenerate(pendingFormat)}
              >
                {t("review.exportConfirmAction")}
              </button>
            </div>
          ) : (
            <div className="rv-export-split">
              <button
                type="button"
                className={`rv-export-btn${exportState === "generating" ? " rv-export-btn--loading" : ""}${exportState === "error" ? " rv-export-btn--error" : ""}`}
                disabled={exportState === "generating"}
                onClick={() => {
                  if (exportState === "generating") return
                  setExportState("idle")
                  setDropdownOpen((v) => !v)
                }}
              >
                {exportState === "generating" ? (
                  <>
                    <span className="rv-spinner" aria-hidden="true" />
                    {t("review.exportGenerating")}
                  </>
                ) : exportState === "error" ? (
                  t("review.exportError")
                ) : (
                  <>
                    {t("review.exportBtn")}
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M6 9l6 6 6-6" />
                    </svg>
                  </>
                )}
              </button>
              {dropdownOpen && (
                <div className="rv-export-menu">
                  <button type="button" className="rv-export-menu__item" onClick={() => handleExportClick("docx")}>
                    {t("review.exportDocx")}
                  </button>
                  <button type="button" className="rv-export-menu__item" onClick={() => handleExportClick("pdf")}>
                    {t("review.exportPdf")}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
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
            {previewMode ? (
              <>
                {t("review.finalPreviewLabel")}
                <button type="button" className="rv-preview-back-btn" onClick={() => setPreviewMode(false)}>
                  ← {t("review.finalPreviewBack")}
                </button>
              </>
            ) : (
              <>
                {t("review.rightLabel")} · {formatName}
                <span className="pane-label__hint">{t("review.rightLabelHint")}</span>
              </>
            )}
          </div>
          {!previewMode && (
            <div className="rv-status-bar">
              <span className="rv-chip rv-chip--neutral">
                {t("review.chipExtracted", { count: ledger.extracted })}
              </span>
              <span className="rv-chip rv-chip--success">
                {t("review.chipPlaced", { count: ledger.placed })}
              </span>
              {remainingMissing > 0 ? (
                <button
                  type="button"
                  className="rv-chip rv-chip--warning"
                  onClick={handleReviewChipClick}
                >
                  {t("review.chipNeedsReview", { count: remainingMissing })}
                </button>
              ) : (
                <span className="rv-chip rv-chip--all-reviewed">
                  {t("review.chipAllReviewed")}
                </span>
              )}
            </div>
          )}
          <div className={`pane-body${previewMode ? " pane-body--pdf" : ""}`} ref={rightPaneBodyRef}>
            {previewMode && previewUrl ? (
              <object data={pdfViewerUrl(previewUrl)} type="application/pdf" className="pdf-embed">
                <p className="pane-fallback">{t("review.pdfFallback")}</p>
              </object>
            ) : (
            <div className="rdoc">
              {/* Document title — static template chrome, matches DOCX level-0 heading */}
              <div className="rdoc-doc-title">Candidate Profile</div>

              {/* Header — name + title subheading */}
              <div className={`rdoc-header${blindProfile ? " rdoc-header--blind" : ""}`}>
                <div className="rdoc-name-row">
                  {ef("full_name", profile.full_name)}
                </div>
                {(profile.work_experience[0]?.title || missingNames.has("current_title")) && (
                  <div className="rdoc-title-row">
                    {ef("current_title", profile.work_experience[0]?.title ?? null)}
                  </div>
                )}
              </div>

              {/* Contact */}
              <div className="rdoc-section">
                <div className="rdoc-section__head">{t("review.sectionContact")}</div>
                <div className="rdoc-additional">
                  <div className="rdoc-detail-row">
                    <span className="rdoc-detail-label">Location</span>
                    {ef("location", profile.location)}
                  </div>
                  {!blindProfile && (edits["email"] ?? profile.email) && (
                    <div className="rdoc-detail-row">
                      <span className="rdoc-detail-label">Email</span>
                      {ef("email", profile.email)}
                    </div>
                  )}
                  {!blindProfile && (edits["phone"] ?? profile.phone) && (
                    <div className="rdoc-detail-row">
                      <span className="rdoc-detail-label">Phone</span>
                      {ef("phone", profile.phone)}
                    </div>
                  )}
                  {!blindProfile && (edits["linkedin_url"] ?? profile.linkedin_url) && (
                    <div className="rdoc-detail-row">
                      <span className="rdoc-detail-label">LinkedIn</span>
                      {ef("linkedin_url", profile.linkedin_url)}
                    </div>
                  )}
                  {!blindProfile && (edits["portfolio_url"] ?? profile.portfolio_url) && (
                    <div className="rdoc-detail-row">
                      <span className="rdoc-detail-label">Portfolio</span>
                      {ef("portfolio_url", profile.portfolio_url)}
                    </div>
                  )}
                </div>
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

              {/* Additional Details — always rendered, all 4 fields with labels */}
              <div className="rdoc-section">
                <div className="rdoc-section__head">{t("review.sectionAdditional")}</div>
                <div className="rdoc-additional">
                  <div className="rdoc-detail-row">
                    <span className="rdoc-detail-label">Salary expectation</span>
                    {ef("salary_expectation", profile.salary_expectation)}
                  </div>
                  <div className="rdoc-detail-row">
                    <span className="rdoc-detail-label">Notice period</span>
                    {ef("notice_period", profile.notice_period)}
                  </div>
                  <div className="rdoc-detail-row">
                    <span className="rdoc-detail-label">Work authorization</span>
                    {ef("work_authorization", profile.work_authorization)}
                  </div>
                  <div className="rdoc-detail-row">
                    <span className="rdoc-detail-label">Interview availability</span>
                    {ef("interview_availability", profile.interview_availability)}
                  </div>
                </div>
              </div>

              {SHOW_CONFIDENCE && null /* per-field confidence highlights rendered here when enabled */}
            </div>
            )}
          </div>
        </div>
      </div>

    </div>
  )
}
