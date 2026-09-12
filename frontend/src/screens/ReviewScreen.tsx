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
  const [pendingFormat, setPendingFormat] = useState<"docx" | "pdf" | null>(null)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [reviewCursor, setReviewCursor] = useState(0)
  const [highlightedField, setHighlightedField] = useState<string | null>(null)
  const exportAreaRef = useRef<HTMLDivElement>(null)
  const [htmlUrl, setHtmlUrl] = useState<string | null>(null)
  const [autoGenerating, setAutoGenerating] = useState(true)
  const [generateStep, setGenerateStep] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [editOverlay, setEditOverlay] = useState<EditOverlay | null>(null)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const overlayInputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null)
  const [emailPopoverOpen, setEmailPopoverOpen] = useState(false)
  const [emailDraft, setEmailDraft] = useState("")
  const [emailState, setEmailState] = useState<"idle" | "loading" | "ready">("idle")
  const [copied, setCopied] = useState(false)
  const emailAreaRef = useRef<HTMLDivElement>(null)
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
      node[isNaN(Number(last)) ? last : Number(last)] = value
      // Clear PENDING_CONFIRMATION so the backend uses the filled value instead of "To be confirmed"
      if (copy.client_display_rules[parts[0]] === "pending_confirmation") {
        delete copy.client_display_rules[parts[0]]
      }
    }
    return copy as typeof profile
  }

  async function approveProfile(): Promise<string | null> {
    const approveRes = await fetch(`/api/artifacts/${data.artifact_id}/profiles/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: applyEditsToProfile(profile) }),
    })
    if (!approveRes.ok) return null
    const approveData = await approveRes.json() as { profile_version_id: string }
    return approveData.profile_version_id
  }

  async function doGenerate(format: "docx" | "pdf") {
    setDropdownOpen(false)
    setExportState("generating")
    setPendingFormat(null)
    try {
      const profileVersionId = await approveProfile()
      if (!profileVersionId) {
        setExportState("error")
        setTimeout(() => setExportState("idle"), 3500)
        return
      }
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          approved_profile_version_id: profileVersionId,
          blind_profile: blindProfile,
          artifact_id: data.artifact_id,
        }),
      })
      if (!res.ok) {
        setExportState("error")
        setTimeout(() => setExportState("idle"), 3500)
        return
      }
      const result = await res.json() as { html_surface_url: string; docx_download_url?: string; pdf_download_url: string }
      // Refresh iframe with freshly generated HTML
      setHtmlUrl(result.html_surface_url + "?t=" + Date.now())
      const url: string = format === "docx" ? (result.docx_download_url ?? result.pdf_download_url) : result.pdf_download_url
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

  // Auto-generate HTML preview on mount
  async function runGenerate() {
    setAutoGenerating(true)
    setPreviewError(null)
    setGenerateStep("Approving profile…")

    const versionId = await approveProfile()
    if (!versionId) {
      setPreviewError("Profile approval failed")
      setAutoGenerating(false)
      setGenerateStep(null)
      return
    }

    const tryGenerate = async (): Promise<boolean> => {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          approved_profile_version_id: versionId,
          blind_profile: false,
          artifact_id: data.artifact_id,
        }),
      })
      if (res.ok) {
        const result = await res.json() as { html_surface_url: string }
        setHtmlUrl(result.html_surface_url + "?t=" + Date.now())
        return true
      }
      const body = await res.json().catch(() => ({})) as { detail?: { error_code?: string } }
      const errorCode = body.detail?.error_code
      if (res.status === 409 && errorCode === "layout_proof_required") {
        return false
      }
      throw new Error(`Generate failed (${res.status}): ${JSON.stringify(body.detail ?? body)}`)
    }

    try {
      setGenerateStep("Generating preview…")
      const ok = await tryGenerate()
      if (!ok) {
        // Auto-create and approve layout proofs, then retry
        setGenerateStep("Validating layout proofs…")
        const proofRes = await fetch(`/api/artifacts/${data.artifact_id}/layout-proofs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        })
        if (!proofRes.ok) {
          const body = await proofRes.json().catch(() => ({})) as { detail?: unknown }
          throw new Error(`Layout proof creation failed (${proofRes.status}): ${JSON.stringify(body.detail ?? body)}`)
        }
        setGenerateStep("Approving layout proofs…")
        const approveRes = await fetch(`/api/artifacts/${data.artifact_id}/layout-proofs/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state: "approved", reviewer_id: "system-auto" }),
        })
        if (!approveRes.ok) {
          const body = await approveRes.json().catch(() => ({})) as { detail?: unknown }
          throw new Error(`Layout proof approval failed (${approveRes.status}): ${JSON.stringify(body.detail ?? body)}`)
        }
        setGenerateStep("Generating preview…")
        const ok2 = await tryGenerate()
        if (!ok2) throw new Error("Layout proof approved but generate still returned layout_proof_required")
      }
      setAutoGenerating(false)
      setGenerateStep(null)
    } catch (e) {
      setPreviewError(String(e))
      setAutoGenerating(false)
      setGenerateStep(null)
    }
  }

  useEffect(() => { runGenerate() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Focus overlay input when it appears
  useEffect(() => {
    if (editOverlay) overlayInputRef.current?.focus()
  }, [editOverlay])

  function handleIframeLoad() {
    const iframe = iframeRef.current
    if (!iframe?.contentDocument) return
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

    // Patch iframe DOM immediately for instant feedback
    const iframe = iframeRef.current
    if (iframe?.contentDocument) {
      const el = iframe.contentDocument.querySelector(
        `[data-field="${CSS.escape(field)}"]`
      ) as HTMLElement | null
      if (el) {
        if (el.tagName === "UL") {
          el.innerHTML = value
            .split("\n")
            .map(b => b.trim())
            .filter(Boolean)
            .map(b => `<li>${b}</li>`)
            .join("")
        } else {
          el.textContent = value
        }
      }
    }
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
            onChange={(e) => setBlindProfile(e.target.checked)}
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
                src={htmlUrl}
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
