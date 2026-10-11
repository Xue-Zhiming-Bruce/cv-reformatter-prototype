import { useState, useEffect, useMemo, useRef } from "react"
import { useTranslation } from "react-i18next"
import { LogoIcon, PencilIcon } from "../components/icons"
import type { ProcessResponse, GenerateResponse, TargetFormatMetadata } from "../types"
import "./ReviewScreen.css"

type ReviewScreenProps = {
  data: ProcessResponse
  resumeFile: File
  resumeFileName: string
  formatName: string
  targetFormat: TargetFormatMetadata
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

  if (isEmpty) {
    return (
      <button
        type="button"
        className={`empty-slot${isHighlighted ? " empty-slot--active" : ""}${className ? ` ${className}` : ""}`}
        onClick={startEdit}
        data-review-field={path}
      >
        <span className="empty-slot__badge">{t(isMissing ? "review.emptySlot" : "review.addValue")}</span>
      </button>
    )
  }


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

function editableValues(value: unknown, path = ""): Array<{ path: string; value: string | null }> {
  const metadata = new Set(["schema_version", "missing_fields", "client_display_rules", "source_block_id", "source_block_ids", "section_type", "disposition", "evidence", "review_state", "confidence"])
  if (value === null || typeof value === "string") return [{ path, value }]
  if (!value || typeof value !== "object") return []
  return Object.entries(value).flatMap(([key, child]) => metadata.has(key) ? [] : editableValues(child, path ? `${path}.${key}` : key))
}

function pdfViewerUrl(url: string): string {
  return `${url}${url.includes("#") ? "&" : "#"}toolbar=0&navpanes=0&view=FitH`
}

type EditOverlay = {
  field: string
  value: string
  top: number
  left: number
  width: number
  multiline: boolean
}

export function ReviewScreen({ data, resumeFile, resumeFileName, formatName, onBack }: ReviewScreenProps) {
  const { t, i18n } = useTranslation()
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [blindProfile, setBlindProfile] = useState(false)
  const [exportState, setExportState] = useState<"idle" | "confirm" | "generating" | "error">("idle")
  const [pendingFormat, setPendingFormat] = useState<"pdf" | null>(null)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [reviewCursor, setReviewCursor] = useState(0)
  const [highlightedField, setHighlightedField] = useState<string | null>(null)
  const exportAreaRef = useRef<HTMLDivElement>(null)
  const [output, setOutput] = useState<GenerateResponse | null>(null)
  const [dirty, setDirty] = useState(false)
  const [proofsNeeded, setProofsNeeded] = useState(false)
  const [proofVariants, setProofVariants] = useState<string[]>([])
  const [checkingProofs, setCheckingProofs] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [showPdf, setShowPdf] = useState(false)
  const [htmlUrl, setHtmlUrl] = useState<string | null>(null)
  const [autoGenerating, setAutoGenerating] = useState(true)
  const [generateStep, setGenerateStep] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [editOverlay, setEditOverlay] = useState<EditOverlay | null>(null)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const overlayInputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null)
  const [helpOpen, setHelpOpen] = useState(false)
  const [emailPopoverOpen, setEmailPopoverOpen] = useState(false)
  const [emailDraft, setEmailDraft] = useState("")
  const [emailState, setEmailState] = useState<"idle" | "loading" | "ready">("idle")
  const [copied, setCopied] = useState(false)
  const emailAreaRef = useRef<HTMLDivElement>(null)
  const { profile, ledger } = data

  const missingNames = new Set(profile.missing_fields.map(field => field.field_name))

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

  useEffect(() => {
    if (!emailPopoverOpen) return
    function handleClickOutside(e: MouseEvent) {
      if (emailAreaRef.current && !emailAreaRef.current.contains(e.target as Node)) {
        setEmailPopoverOpen(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [emailPopoverOpen])

  const isPdf = /\.pdf$/i.test(resumeFile.name) || resumeFile.type === "application/pdf"
  const originalPreviewUrl = data.original_pdf_preview_url ?? (isPdf ? objectUrl : null)
  const originalPdfViewerUrl = originalPreviewUrl ? pdfViewerUrl(originalPreviewUrl) : null

  function handleEdit(path: string, value: string) {
    if (autoGenerating || confirming) return
    setDirty(true)
    setOutput(null)
    if (path === highlightedField && value.trim()) {
      setHighlightedField(null)
    }
    setEdits((prev) => {
      const next = { ...prev }
      next[path] = value
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
    setHighlightedField(fieldName)
  }

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
      // work_experience.N.description edited as newline-separated bullets from iframe
      if (/^work_experience\.\d+\.description$/.test(path)) {
        const idx = Number(path.split(".")[1])
        copy.work_experience[idx].description = value.split("\n").map((s: string) => s.trim()).filter(Boolean)
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
      node[isNaN(Number(last)) ? last : Number(last)] = !/^\d+$/.test(last) && value === "" ? null : value
      // Clear PENDING_CONFIRMATION so the backend uses the filled value instead of "To be confirmed"
      if (copy.client_display_rules[parts[0]] === "pending_confirmation") {
        delete copy.client_display_rules[parts[0]]
      }
    }
    if ("full_name" in edits) { copy.given_name = null; copy.family_name = null }
    else if (("given_name" in edits || "family_name" in edits) && copy.given_name && copy.family_name) {
      copy.full_name = `${copy.given_name} ${copy.family_name}`
    }
    return copy as typeof profile
  }

  async function apiJson<T>(url: string, init?: RequestInit): Promise<T> {
    const response = await fetch(url, init)
    const body = await response.json()
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : body.detail?.message ?? JSON.stringify(body.detail))
    return body as T
  }

  function outputIdentity(result: GenerateResponse) {
    return {
      profile_revision_id: result.profile_revision_id,
      profile_sha256: result.profile_sha256,
      layout_template_version: result.layout_template_version,
      layout_spec_sha256: result.layout_spec_sha256,
      html_sha256: result.html_sha256,
      pdf_sha256: result.pdf_sha256,
    }
  }

  async function confirmFinal() {
    if (!output || dirty || autoGenerating) return
    setConfirming(true)
    setPreviewError(null)
    try {
      await apiJson(`/api/artifacts/${data.artifact_id}/final-acceptance`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(outputIdentity(output)),
      })
      setOutput({ ...output, client_ready: true })
    } catch (error) {
      setOutput({ ...output, client_ready: false })
      setPreviewError(String(error))
    } finally { setConfirming(false) }
  }

  function doGenerate(format: "pdf") {
    setDropdownOpen(false)
    setPendingFormat(null)
    setExportState("idle")
    if (format !== "pdf" || !output?.client_ready || dirty || autoGenerating) return
    const a = document.createElement("a")
    a.href = output.pdf_download_url
    a.download = "resume.pdf"
    document.body.appendChild(a); a.click(); a.remove()
  }

  function handleExportClick(format: "pdf") {
    if (!output?.client_ready || dirty || autoGenerating) return
    setDropdownOpen(false)
    if (remainingMissing > 0) { setPendingFormat(format); setExportState("confirm") }
    else doGenerate(format)
  }

  async function reviewTargetProofs(approve = false) {
    setCheckingProofs(true); setPreviewError(null)
    try {
      if (approve) {
        await apiJson(`/api/artifacts/${data.artifact_id}/layout-proofs/approve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state: "approved", reviewer_id: "local-recruiter" }) })
        setProofsNeeded(false); setProofVariants([])
        await runGenerate()
      } else {
        const result = await apiJson<{ variants: Array<{ variant: string }> }>(`/api/artifacts/${data.artifact_id}/layout-proofs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
        setProofVariants(result.variants.map(item => item.variant))
      }
    } catch (error) { setPreviewError(String(error)) }
    finally { setCheckingProofs(false) }
  }

  const operation = useRef(0)
  const currentRevision = useRef(data.profile_revision_id)
  async function runGenerate() {
    if (autoGenerating && htmlUrl) return
    const token = ++operation.current
    setAutoGenerating(true); setPreviewError(null); setOutput(null); setShowPdf(false)
    const json = { "Content-Type": "application/json" }
    try {
      setGenerateStep(t("review.savingEdits"))
      let revisionId = currentRevision.current
      if (dirty) {
        const revision = await apiJson<{ profile_revision_id: string }>(`/api/artifacts/${data.artifact_id}/profiles/revisions`, {
          method: "POST", headers: json, body: JSON.stringify({ profile: applyEditsToProfile(profile) }),
        })
        revisionId = revision.profile_revision_id
        currentRevision.current = revisionId
      }
      let job = await apiJson<{ job_id: string; state: string; result?: GenerateResponse; error?: { message?: string; error_code?: string } }>(`/api/artifacts/${data.artifact_id}/preview-jobs`, {
        method: "POST", headers: json,
        body: JSON.stringify({ profile_revision_id: revisionId, blind_profile: blindProfile }),
      })
      while (job.state === "queued" || job.state === "running") {
        setGenerateStep(t(job.state === "queued" ? "review.previewQueued" : "review.buildingPreview"))
        await new Promise(resolve => setTimeout(resolve, 1000))
        if (token !== operation.current) return
        job = await apiJson(`/api/artifacts/${data.artifact_id}/preview-jobs/${job.job_id}`)
      }
      if (token !== operation.current) return
      if (job.state !== "succeeded" || !job.result) {
        setProofsNeeded(job.error?.error_code === "layout_proof_required")
        throw new Error(job.error?.message ?? t("review.previewFailed"))
      }
      setProofsNeeded(false)
      setOutput(job.result); setDirty(false)
      setHtmlUrl(job.result.html_surface_url + "?v=" + job.result.html_sha256)
    } catch (error) {
      if (token === operation.current) setPreviewError(String(error))
    } finally {
      if (token === operation.current) { setAutoGenerating(false); setGenerateStep(null) }
    }
  }

  useEffect(() => {
    runGenerate()
    return () => { operation.current++ }
  }, []) // Initial preview uses the already persisted extraction revision.

  // Focus overlay input when it appears
  useEffect(() => {
    if (editOverlay) overlayInputRef.current?.focus()
  }, [editOverlay])

  function handleIframeLoad() {
    const iframe = iframeRef.current
    if (autoGenerating || showPdf || !iframe?.contentDocument) return
    const doc = iframe.contentDocument

    // Inject edit highlight styles
    if (!doc.getElementById("cv-edit-styles")) {
      const style = doc.createElement("style")
      style.id = "cv-edit-styles"
      style.textContent = `
        [data-field]:hover {
          outline: 2px solid rgba(99,102,241,0.75);
          outline-offset: 1px;
          border-radius: 2px;
          cursor: text;
        }
      `
      doc.head.appendChild(style)
    }

    // Click → show edit overlay (callback runs in parent window context)
    doc.addEventListener("click", (e) => {
      const target = (e.target as Element).closest("[data-field]") as HTMLElement | null
      if (!target) return
      e.preventDefault()
      const field = target.getAttribute("data-field")!
      const isUl = target.tagName === "UL"
      const value = isUl
        ? Array.from(target.querySelectorAll("li"))
            .map(li => li.textContent?.trim() ?? "")
            .filter(Boolean)
            .join("\n")
        : target.textContent?.trim() ?? ""
      const rect = target.getBoundingClientRect()
      const iframePos = iframe.getBoundingClientRect()
      setEditOverlay({
        field,
        value,
        top: iframePos.top + rect.top,
        left: iframePos.left + rect.left,
        width: Math.max(rect.width, 240),
        multiline: isUl || field === "professional_summary",
      })
    })
  }

  function saveOverlayEdit(value: string) {
    if (!editOverlay) return
    const { field } = editOverlay

    // Update React edits state
    handleEdit(field, value)
    const element = iframeRef.current?.contentDocument?.querySelector(`[data-field="${CSS.escape(field)}"]`)
    if (element) element.textContent = value
    setEditOverlay(null)

  }

  async function handleEmailBtnClick() {
    if (emailPopoverOpen) {
      setEmailPopoverOpen(false)
      return
    }
    setEmailPopoverOpen(true)
    setEmailState("loading")
    try {
      const res = await fetch("/api/followup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          profile: applyEditsToProfile(profile),
          language: i18n.language === "ko" ? "Korean" : "English",
        }),
      })
      if (!res.ok) throw new Error("failed")
      const result = await res.json()
      setEmailDraft(result.followup_message)
      setEmailState("ready")
    } catch {
      setEmailPopoverOpen(false)
      setEmailState("idle")
    }
  }

  async function handleCopyEmail() {
    try {
      await navigator.clipboard.writeText(emailDraft)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* clipboard blocked — silent */ }
  }

  return (
    <div className="review-page">
      {/* ── Missing-fields confirm modal ── */}
      {exportState === "confirm" && (
        <div className="rv-modal-backdrop" onClick={() => { setExportState("idle"); setPendingFormat(null) }}>
          <div className="rv-modal" role="dialog" aria-modal="true" onClick={e => e.stopPropagation()}>
            <div className="rv-modal__icon" aria-hidden="true">⚠️</div>
            <h2 className="rv-modal__title">{t("review.exportConfirmTitle", { count: remainingMissing })}</h2>
            <p className="rv-modal__body">{t("review.exportConfirmBody")}</p>
            <div className="rv-modal__actions">
              <button
                type="button"
                className="rv-modal__cancel"
                onClick={() => { setExportState("idle"); setPendingFormat(null) }}
              >
                {t("review.exportCancel")}
              </button>
              <button
                type="button"
                className="rv-modal__confirm"
                onClick={() => pendingFormat && doGenerate(pendingFormat)}
              >
                {t("review.exportConfirmAction")}
              </button>
            </div>
          </div>
        </div>
      )}

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
            disabled={autoGenerating || confirming} onChange={(e) => { setBlindProfile(e.target.checked); setDirty(true); setOutput(null) }}
          />
          <span className="rv-blind-toggle__body">
            <span className="rv-blind-toggle__label">{t("review.blindProfileLabel")}</span>
            <span className="rv-blind-toggle__hint">{t("review.blindProfileHint")}</span>
          </span>
        </label>

        <div className="rv-export-area" ref={exportAreaRef}>
          <div className="rv-export-split">
              <button
                type="button"
                className={`rv-export-btn${exportState === "generating" ? " rv-export-btn--loading" : ""}${exportState === "error" ? " rv-export-btn--error" : ""}`}
                disabled={autoGenerating || confirming}
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
                  <button type="button" className="rv-export-menu__item" disabled title={t("review.notLaunched")}>
                    {t("review.exportDocx")} — {t("review.notLaunched")}
                  </button>
                  <button type="button" className="rv-export-menu__item" disabled={!output?.client_ready || dirty || autoGenerating} onClick={() => handleExportClick("pdf")}>
                    {t("review.exportPdf")}
                  </button>
                </div>
              )}
            </div>
        </div>

        <button
          type="button"
          className={`rv-help-btn${helpOpen ? " rv-help-btn--active" : ""}`}
          onClick={() => setHelpOpen(v => !v)}
          aria-label="Help"
        >?</button>
      </header>


      {/* ── Two panes ── */}
      <div className="rv-workflow-actions" role="status">
        <button type="button" disabled={autoGenerating || confirming} onClick={runGenerate}>{t("review.regeneratePreview")}</button>
        {output && <button type="button" onClick={() => setShowPdf(value => !value)}>{t(showPdf ? "review.showHtml" : "review.showPdf")}</button>}
        <button type="button" disabled={!output || dirty || autoGenerating || confirming || output.client_ready} onClick={confirmFinal}>{t(confirming ? "review.confirmingFinal" : "review.confirmFinal")}</button>
        <span>{t(dirty ? "review.editsNeedPreview" : output?.client_ready ? "review.finalConfirmed" : "review.previewNotConfirmed")}</span>
        {previewError && <span className="rv-workflow-error">{previewError}</span>}
      </div>
      {proofsNeeded && <div className="rv-workflow-actions">
        <span>{t("review.targetProofsNeeded")}</span>
        <button type="button" disabled={checkingProofs} onClick={() => reviewTargetProofs()}>{t("review.prepareTargetProofs")}</button>
        {proofVariants.map(variant => <a key={variant} target="_blank" rel="noreferrer" href={`/api/artifacts/${data.artifact_id}/layout-proofs/${variant}/pdf`}>{variant} PDF</a>)}
        {proofVariants.length > 0 && <button type="button" disabled={checkingProofs} onClick={() => reviewTargetProofs(true)}>{t("review.approveTargetProofs")}</button>}
      </div>}
      <details className="rv-profile-editor">
        <summary>{t("review.reviewFields")}</summary>
        <div className="rv-profile-fields">
          {editableValues(profile).map(item => <label key={item.path}>
            <span>{item.path.split(".").map(part => /^\d+$/.test(part) ? String(Number(part) + 1) : part.replaceAll("_", " ")).join(" / ")}</span>
            <EditableField path={item.path} value={item.value} edits={edits} onEdit={handleEdit} isMissing={missingNames.has(item.path)} isHighlighted={missingNames.has(item.path) || item.path === highlightedField} />
          </label>)}
          {profile.additional_sections?.map((section, index) => <div key={section.source_block_id}>
            <strong>{section.source_heading}</strong>
            <label>{t("review.sectionVisibility")}
              <select disabled={autoGenerating || confirming} value={edits[`additional_sections.${index}.disposition`] ?? section.disposition} onChange={event => handleEdit(`additional_sections.${index}.disposition`, event.target.value)}>
                <option value="show">{t("review.sectionShow")}</option><option value="hide">{t("review.sectionHide")}</option>
              </select>
            </label>
            <label>{t("review.sectionReview")}
              <select disabled={autoGenerating || confirming} value={edits[`additional_sections.${index}.review_state`] ?? section.review_state} onChange={event => handleEdit(`additional_sections.${index}.review_state`, event.target.value)}>
                <option value="pending_review">{t("review.sectionPending")}</option><option value="reviewed">{t("review.sectionReviewed")}</option>
              </select>
            </label>
          </div>)}
          {Object.entries(profile.client_display_rules).map(([field, rule]) => <label key={field}>
            <span>{field.replaceAll("_", " ")} — {t("review.disclosure")}</span>
            <select disabled={autoGenerating || confirming} value={edits[`client_display_rules.${field}`] ?? rule} onChange={event => handleEdit(`client_display_rules.${field}`, event.target.value)}>
              {["show", "hide", "pending_confirmation", "available_upon_request"].map(value => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}
            </select>
          </label>)}
        </div>
      </details>
      <div className="rv-panes">

        {/* ── Help sidebar ── */}
        {helpOpen && (
          <aside className="rv-help-sidebar" role="complementary" aria-label="Help guide">
            <div className="rv-help-sidebar__header">
              <span>How to use</span>
              <button type="button" className="rv-help-sidebar__close" onClick={() => setHelpOpen(false)} aria-label="Close help">×</button>
            </div>
            <div className="rv-help-sidebar__body">
              <ol className="rv-help-steps">
                <li className="rv-help-step">
                  <span className="rv-help-step__num">1</span>
                  <div>
                    <strong>Review the draft</strong>
                    <p>The right pane shows your resume reformatted into the target layout. Compare it with the original on the left.</p>
                  </div>
                </li>
                <li className="rv-help-step">
                  <span className="rv-help-step__num">2</span>
                  <div>
                    <strong>Click any field to edit</strong>
                    <p>Hover over any text in the preview — it highlights. Click to open an edit box. Press Enter or click Save.</p>
                  </div>
                </li>
                <li className="rv-help-step">
                  <span className="rv-help-step__num">3</span>
                  <div>
                    <strong>Fill in missing fields</strong>
                    <p>Fields marked <em>orange</em> need review — either the original didn't have them or they need confirmation.</p>
                  </div>
                </li>
                <li className="rv-help-step">
                  <span className="rv-help-step__num">4</span>
                  <div>
                    <strong>Export when ready</strong>
                    <p>Click <strong>Export</strong> in the top-right to download as DOCX or PDF.</p>
                  </div>
                </li>
              </ol>

              <div className="rv-help-divider" />

              <div className="rv-help-tips">
                <div className="rv-help-tips__title">Tips</div>
                <ul>
                  <li>Use <strong>Blind profile</strong> to hide candidate identity before sharing.</li>
                  <li>Draft an email to request missing info from the candidate.</li>
                  <li>Nothing is invented — all content comes from the original resume.</li>
                </ul>
              </div>
            </div>
          </aside>
        )}

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

        {/* Right — HTML preview (click any field to edit) */}
        <div className="rv-pane rv-pane--right">
          <div className="pane-label">
            {formatName}
            {!autoGenerating && htmlUrl && (
              <span className="pane-label__hint">{t("review.clickToEditHint")}</span>
            )}
          </div>
          <div className="rv-status-bar">
              <span className="rv-chip rv-chip--neutral">
                {t("review.chipExtracted", { count: ledger.extracted })}
              </span>
              <span className="rv-chip rv-chip--success">
                {t("review.chipPlaced", { count: ledger.placed })}
              </span>
              {remainingMissing > 0 ? (
                <>
                  <button
                    type="button"
                    className="rv-chip rv-chip--warning"
                    onClick={handleReviewChipClick}
                  >
                    {t("review.chipNeedsReview", { count: remainingMissing })}
                  </button>
                  <div className="rv-email-wrap" ref={emailAreaRef}>
                    <button type="button" className="rv-email-btn" onClick={handleEmailBtnClick}>
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <rect x="2" y="4" width="20" height="16" rx="2"/>
                        <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
                      </svg>
                      {t("review.emailDraftBtn")}
                    </button>
                    {emailPopoverOpen && (
                      <div className="rv-email-popover">
                        <div className="rv-email-popover__header">
                          <span className="rv-email-popover__title">{t("review.emailPopoverTitle")}</span>
                          <button type="button" className="rv-email-popover__close" onClick={() => setEmailPopoverOpen(false)}>×</button>
                        </div>
                        {(edits["email"] ?? profile.email) && (
                          <div className="rv-email-popover__to">
                            <span className="rv-email-popover__to-label">{t("review.emailTo")}</span>
                            <span className="rv-email-popover__to-value">{edits["email"] ?? profile.email}</span>
                          </div>
                        )}
                        {emailState === "loading" ? (
                          <div className="rv-email-popover__loading">
                            <span className="rv-spinner" aria-hidden="true" />
                            {t("review.emailLoading")}
                          </div>
                        ) : (
                          <textarea
                            className="rv-email-popover__textarea"
                            value={emailDraft}
                            onChange={(e) => setEmailDraft(e.target.value)}
                            rows={8}
                          />
                        )}
                        <div className="rv-email-popover__actions">
                          <button
                            type="button"
                            className={`rv-email-popover__copy-btn${copied ? " rv-email-popover__copy-btn--copied" : ""}`}
                            disabled={emailState !== "ready"}
                            onClick={handleCopyEmail}
                          >
                            {copied ? t("review.emailCopied") : t("review.emailCopy")}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <span className="rv-chip rv-chip--all-reviewed">
                  {t("review.chipAllReviewed")}
                </span>
              )}
            </div>
          <div className="pane-body pane-body--iframe">
            {autoGenerating ? (
              <div className="rv-iframe-loading">
                <span className="rv-spinner" aria-hidden="true" />
                <span>{generateStep ?? t("review.generatingPreview")}</span>
              </div>
            ) : htmlUrl ? (
              <iframe
                ref={iframeRef}
                sandbox={showPdf ? undefined : "allow-same-origin"}
                key={showPdf ? "pdf" : "html"}
                src={showPdf && output ? pdfViewerUrl(output.pdf_preview_url + "?v=" + output.pdf_sha256) : htmlUrl}
                className="rv-preview-iframe"
                onLoad={handleIframeLoad}
                title="Resume Preview"
              />
            ) : (
              <div className="rv-iframe-loading">
                {previewError && (
                  <pre style={{ fontSize: 11, color: "#ef4444", whiteSpace: "pre-wrap", maxWidth: 380, textAlign: "left", marginBottom: 12 }}>
                    {previewError}
                  </pre>
                )}
                <button type="button" className="rv-field-overlay__save" onClick={runGenerate}>
                  Retry preview
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Field edit overlay ── */}
      {editOverlay && (
        <div
          className="rv-field-overlay"
          style={{ top: editOverlay.top, left: editOverlay.left, width: editOverlay.width }}
        >
          {editOverlay.multiline ? (
            <textarea
              ref={overlayInputRef as React.RefObject<HTMLTextAreaElement>}
              className="rv-field-overlay__input"
              value={editOverlay.value}
              rows={4}
              onChange={e => setEditOverlay(prev => prev ? { ...prev, value: e.target.value } : null)}
              onKeyDown={e => { if (e.key === "Escape") setEditOverlay(null) }}
            />
          ) : (
            <input
              ref={overlayInputRef as React.RefObject<HTMLInputElement>}
              type="text"
              className="rv-field-overlay__input"
              value={editOverlay.value}
              onChange={e => setEditOverlay(prev => prev ? { ...prev, value: e.target.value } : null)}
              onKeyDown={e => {
                if (e.key === "Enter") saveOverlayEdit(editOverlay.value)
                if (e.key === "Escape") setEditOverlay(null)
              }}
            />
          )}
          <div className="rv-field-overlay__actions">
            <button type="button" className="rv-field-overlay__save" onClick={() => saveOverlayEdit(editOverlay.value)}>
              {t("review.overlaySave")}
            </button>
            <button type="button" className="rv-field-overlay__cancel" onClick={() => setEditOverlay(null)}>
              {t("review.overlayCancel")}
            </button>
          </div>
        </div>
      )}

    </div>
  )
}
