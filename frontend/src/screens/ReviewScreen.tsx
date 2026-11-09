import { useState, useEffect, useMemo, useRef } from "react"
import { useTranslation } from "react-i18next"
import { LogoIcon, PencilIcon } from "../components/icons"
import type {
  CandidateProfile,
  FinalAcceptanceResponse,
  GenerateResponse,
  LayoutProofStatus,
  ProcessResponse,
  ProfileRevisionResponse,
  TargetFormatMetadata,
} from "../types"
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
  targetFormat: TargetFormatMetadata | null
  onBack: () => void
}

type RetryTarget = "preview" | "export" | "createProofs" | "approveProofs" | null

type ActionError = {
  title: string
  code: string | null
  message: string
  options: string[]
  retry: RetryTarget
  forceSaveRetry: boolean
  regenerate: boolean
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function profileActionError(
  title: string,
  code: string | null,
  message: string,
  options: string[],
  retry: RetryTarget,
  overrides: Partial<Pick<ActionError, "forceSaveRetry" | "regenerate">> = {},
): ActionError {
  return {
    title,
    code,
    message,
    options,
    retry,
    forceSaveRetry: overrides.forceSaveRetry ?? false,
    regenerate: overrides.regenerate ?? false,
  }
}

async function buildApiError(
  res: Response,
  title: string,
  retry: RetryTarget,
  overrides: Partial<Pick<ActionError, "forceSaveRetry" | "regenerate">> = {},
): Promise<ActionError> {
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  const detail = isRecord(body) ? body.detail : null
  if (isRecord(detail)) {
    const code = typeof detail.error_code === "string" ? detail.error_code : null
    const message = typeof detail.message === "string" ? detail.message : title
    const options = Array.isArray(detail.options)
      ? detail.options.filter((option): option is string => typeof option === "string")
      : []
    return profileActionError(title, code, message, options, retry, {
      forceSaveRetry:
        overrides.forceSaveRetry ?? Boolean(code?.startsWith("approved_profile_")),
      regenerate: overrides.regenerate,
    })
  }
  if (typeof detail === "string") {
    return profileActionError(title, null, detail, [], retry, overrides)
  }
  return profileActionError(title, null, title, [], retry, overrides)
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

function applyEditsToProfile(profile: CandidateProfile, edits: Record<string, string>): CandidateProfile {
  const copy = structuredClone(profile) as unknown as Record<string, unknown>
  for (const [path, value] of Object.entries(edits)) {
    if (path === "skills") {
      copy.skills = value.split(",").map((skill) => skill.trim()).filter(Boolean)
      continue
    }
    const parts = path.split(".")
    let node: unknown = copy
    for (let i = 0; i < parts.length - 1; i += 1) {
      const key = parts[i]
      if (Array.isArray(node)) node = node[Number(key)]
      else if (isRecord(node)) node = node[key]
      else node = undefined
    }
    const last = parts[parts.length - 1]
    if (Array.isArray(node)) node[Number(last)] = value
    else if (isRecord(node)) node[last] = value
    // Clear PENDING_CONFIRMATION so the backend uses the filled value instead of "To be confirmed"
    const rules = copy.client_display_rules
    if (isRecord(rules) && parts[0] && rules[parts[0]] === "pending_confirmation") {
      delete rules[parts[0]]
    }
  }
  return copy as unknown as CandidateProfile
}

export function ReviewScreen({ data, resumeFile, resumeFileName, formatName, targetFormat, onBack }: ReviewScreenProps) {
  const { t, i18n } = useTranslation()
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [blindProfile, setBlindProfile] = useState(false)
  const [exportState, setExportState] = useState<"idle" | "confirm" | "generating" | "error">("idle")
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [reviewCursor, setReviewCursor] = useState(0)
  const [highlightedField, setHighlightedField] = useState<string | null>(null)
  const exportAreaRef = useRef<HTMLDivElement>(null)
  const rightPaneBodyRef = useRef<HTMLDivElement>(null)
  const [previewMode, setPreviewMode] = useState(false)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewState, setPreviewState] = useState<"idle" | "generating" | "error">("idle")
  const [emailPopoverOpen, setEmailPopoverOpen] = useState(false)
  const [emailDraft, setEmailDraft] = useState("")
  const [emailState, setEmailState] = useState<"idle" | "loading" | "ready">("idle")
  const [copied, setCopied] = useState(false)
  const emailAreaRef = useRef<HTMLDivElement>(null)
  const [revisionId, setRevisionId] = useState(data.profile_revision_id)
  const [profileDirty, setProfileDirty] = useState(false)
  const editVersionRef = useRef(0)
  const [generation, setGeneration] = useState<GenerateResponse | null>(null)
  const [acceptanceState, setAcceptanceState] = useState<"idle" | "submitting" | "accepted">("idle")
  const [acceptanceInvalidated, setAcceptanceInvalidated] = useState(false)
  const [actionError, setActionError] = useState<ActionError | null>(null)
  const [proofStatus, setProofStatus] = useState<LayoutProofStatus | null>(null)
  const [proofBusy, setProofBusy] = useState<"idle" | "loading" | "creating" | "approving">("idle")
  const { profile, ledger } = data

  const isPdfTarget = targetFormat?.input_type === "pdf"
  const isDocxTarget = targetFormat?.input_type === "docx"
  const targetBlocked = isDocxTarget
  const isAccepted = acceptanceState === "accepted" || generation?.client_ready === true

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

  useEffect(() => {
    if (!isPdfTarget) return
    let cancelled = false
    async function loadProofStatus() {
      setProofBusy("loading")
      try {
        const res = await fetch(`/api/artifacts/${data.artifact_id}/layout-proofs`)
        if (!res.ok) return
        const status = (await res.json()) as LayoutProofStatus
        if (!cancelled) setProofStatus(status)
      } catch {
        // The target panel keeps a refresh action available when status is unavailable.
      } finally {
        if (!cancelled) setProofBusy("idle")
      }
    }
    void loadProofStatus()
    return () => { cancelled = true }
  }, [data.artifact_id, isPdfTarget])

  const isPdf = /\.pdf$/i.test(resumeFile.name) || resumeFile.type === "application/pdf"
  const originalPreviewUrl = data.original_pdf_preview_url ?? (isPdf ? objectUrl : null)
  const originalPdfViewerUrl = originalPreviewUrl ? pdfViewerUrl(originalPreviewUrl) : null

  function invalidateGeneratedOutput() {
    if (generation?.client_ready || acceptanceState === "accepted") {
      setAcceptanceInvalidated(true)
    }
    setGeneration(null)
    setPreviewMode(false)
    setPreviewUrl(null)
    setAcceptanceState("idle")
  }

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
    editVersionRef.current += 1
    setProfileDirty(true)
    invalidateGeneratedOutput()
  }

  function handleBlindChange(checked: boolean) {
    setBlindProfile(checked)
    invalidateGeneratedOutput()
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

  const remainingMissing = profile.missing_fields.filter((f) => {
    const edited = edits[f.field_name]
    return !edited || !edited.trim()
  }).length

  const failedVariants = proofStatus?.variants.filter((variant) => !variant.structural_passed) ?? []
  const proofCreated = (proofStatus?.variants.length ?? 0) > 0
  const proofState = proofStatus?.state ?? "none"
  const proofLabel = proofBusy === "loading" && !proofStatus
    ? t("review.proofLoading")
    : proofState === "approved"
      ? t("review.proofStateApproved")
      : proofState === "rejected"
        ? t("review.proofStateRejected")
        : proofCreated || proofState === "pending"
          ? t("review.proofStatePending")
          : t("review.proofStateNone")
  const proofChipClass = proofState === "approved"
    ? "rv-chip--success"
    : proofState === "none" && !proofCreated
      ? "rv-chip--neutral"
      : "rv-chip--warning"
  const proofNextStep = proofState === "approved"
    ? t("review.proofNextApproved")
    : proofState === "rejected"
      ? t("review.proofNextRejected")
      : proofCreated || proofState === "pending"
        ? t("review.proofNextPending")
        : t("review.proofNextNone")
  const canApproveProofs = proofCreated && proofState !== "approved" && proofBusy === "idle"

  async function persistRevision(retry: RetryTarget): Promise<string | null> {
    const editVersionAtSave = editVersionRef.current
    try {
      const res = await fetch(`/api/artifacts/${data.artifact_id}/profiles/revisions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: applyEditsToProfile(profile, edits) }),
      })
      if (!res.ok) {
        setActionError(await buildApiError(res, t("review.revisionSaveFailed"), retry))
        return null
      }
      const saved = (await res.json()) as ProfileRevisionResponse
      setRevisionId(saved.profile_revision_id)
      if (editVersionRef.current === editVersionAtSave) setProfileDirty(false)
      return saved.profile_revision_id
    } catch {
      setActionError(profileActionError(t("review.revisionSaveFailed"), null, t("review.networkErrorMessage"), [], retry))
      return null
    }
  }

  async function loadProofStatus(): Promise<LayoutProofStatus | null> {
    setProofBusy("loading")
    try {
      const res = await fetch(`/api/artifacts/${data.artifact_id}/layout-proofs`)
      if (!res.ok) {
        setActionError(await buildApiError(res, t("review.proofStatusFailed"), null))
        return null
      }
      const status = (await res.json()) as LayoutProofStatus
      setProofStatus(status)
      return status
    } catch {
      setActionError(profileActionError(t("review.proofStatusFailed"), null, t("review.networkErrorMessage"), [], null))
      return null
    } finally {
      setProofBusy("idle")
    }
  }

  function proofGateError(status: LayoutProofStatus): ActionError | null {
    const failed = status.variants.filter((variant) => !variant.structural_passed)
    if (failed.length > 0) {
      return profileActionError(
        t("review.proofTitle"),
        "layout_proof_validation_failed",
        t("review.proofVariantsFailed", { variants: failed.map((variant) => variant.variant).join(", ") }),
        ["create_layout_proofs"],
        null,
      )
    }
    if (status.state === "approved") return null
    const created = status.variants.length > 0
    if (status.state === "rejected") {
      return profileActionError(
        t("review.proofTitle"),
        "layout_proof_rejected",
        t("review.proofRejectedMessage"),
        ["create_layout_proofs"],
        null,
      )
    }
    if (created || status.state === "pending") {
      return profileActionError(
        t("review.proofTitle"),
        "layout_proof_approval_required",
        t("review.proofApprovalRequiredMessage"),
        ["approve_layout_proofs"],
        null,
      )
    }
    return profileActionError(
      t("review.proofTitle"),
      "layout_proof_required",
      t("review.proofRequiredMessage"),
      ["create_layout_proofs"],
      null,
    )
  }

  async function runGeneration(source: "preview" | "export", forceSave: boolean): Promise<GenerateResponse | null> {
    if (targetBlocked) return null
    setActionError(null)
    let revisionToUse = revisionId
    if (profileDirty || forceSave || !revisionToUse) {
      const saved = await persistRevision(source)
      if (!saved) return null
      revisionToUse = saved
    }
    // No-target defense: layout proofs are only consulted for an uploaded target.
    if (targetFormat) {
      const status = await loadProofStatus()
      if (!status) return null
      const gateError = proofGateError(status)
      if (gateError) {
        setActionError(gateError)
        return null
      }
    }
    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          profile_revision_id: revisionToUse,
          blind_profile: blindProfile,
          artifact_id: data.artifact_id,
        }),
      })
      if (!res.ok) {
        setActionError(await buildApiError(
          res,
          source === "preview" ? t("review.previewFailedTitle") : t("review.exportFailedTitle"),
          source,
        ))
        return null
      }
      const result = (await res.json()) as GenerateResponse
      setGeneration(result)
      setPreviewUrl(result.pdf_preview_url)
      setAcceptanceState(result.client_ready ? "accepted" : "idle")
      if (result.client_ready) setAcceptanceInvalidated(false)
      return result
    } catch {
      setActionError(profileActionError(
        source === "preview" ? t("review.previewFailedTitle") : t("review.exportFailedTitle"),
        null,
        t("review.networkErrorMessage"),
        [],
        source,
      ))
      return null
    }
  }

  async function handleFinalPreview(forceSave = false) {
    if (previewState === "generating" || targetBlocked) return
    setPreviewState("generating")
    setPreviewMode(false)
    const result = await runGeneration("preview", forceSave)
    if (result) {
      setPreviewMode(true)
      setPreviewState("idle")
    } else {
      setPreviewState("error")
      setTimeout(() => setPreviewState("idle"), 3500)
    }
  }

  async function doExport(forceSave = false) {
    if (targetBlocked) return
    setExportState("generating")
    const result = await runGeneration("export", forceSave)
    if (!result) {
      setExportState("error")
      setTimeout(() => setExportState("idle"), 3500)
      return
    }
    const a = document.createElement("a")
    a.href = result.pdf_download_url
    a.download = "resume.pdf"
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setExportState("idle")
  }

  function handleExportClick() {
    setDropdownOpen(false)
    if (targetBlocked) return
    if (remainingMissing > 0) {
      setExportState("confirm")
    } else {
      void doExport()
    }
  }

  async function handleCreateProofs() {
    if (!isPdfTarget || proofBusy !== "idle") return
    setActionError(null)
    setProofBusy("creating")
    try {
      const res = await fetch(`/api/artifacts/${data.artifact_id}/layout-proofs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      })
      if (!res.ok) {
        const error = await buildApiError(res, t("review.proofCreateFailed"), "createProofs")
        if (error.options.length === 0) {
          error.options = ["reanalyze_target", "upload_new_target"]
        }
        setActionError(error)
        return
      }
      const status = (await res.json()) as LayoutProofStatus
      setProofStatus(status)
    } catch {
      setActionError(profileActionError(t("review.proofCreateFailed"), null, t("review.networkErrorMessage"), [], "createProofs"))
    } finally {
      setProofBusy("idle")
    }
  }

  async function handleApproveProofs() {
    if (!isPdfTarget || proofBusy !== "idle") return
    setActionError(null)
    setProofBusy("approving")
    try {
      const res = await fetch(`/api/artifacts/${data.artifact_id}/layout-proofs/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: "approved", reviewer_id: "local-reviewer" }),
      })
      if (!res.ok) {
        const error = await buildApiError(res, t("review.proofApproveFailed"), "approveProofs")
        if (error.options.length === 0) {
          error.options = ["create_layout_proofs"]
        }
        setActionError(error)
        return
      }
      const status = (await res.json()) as LayoutProofStatus
      setProofStatus(status)
    } catch {
      setActionError(profileActionError(t("review.proofApproveFailed"), null, t("review.networkErrorMessage"), [], "approveProofs"))
    } finally {
      setProofBusy("idle")
    }
  }

  async function handleAcceptFinal() {
    if (!generation || acceptanceState === "submitting") return
    if (!generation.profile_sha256) {
      setActionError(profileActionError(
        t("review.acceptanceFailed"),
        null,
        t("review.acceptanceIdentityMissing"),
        [],
        "preview",
        { forceSaveRetry: true, regenerate: true },
      ))
      return
    }
    setActionError(null)
    setAcceptanceState("submitting")
    try {
      const res = await fetch(`/api/artifacts/${data.artifact_id}/final-acceptance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          profile_revision_id: generation.profile_revision_id,
          profile_sha256: generation.profile_sha256,
          layout_template_version: generation.layout_template_version,
          layout_spec_sha256: generation.layout_spec_sha256,
          html_sha256: generation.html_sha256,
          pdf_sha256: generation.pdf_sha256,
        }),
      })
      if (!res.ok) {
        setActionError(await buildApiError(res, t("review.acceptanceFailed"), "preview", {
          forceSaveRetry: true,
          regenerate: true,
        }))
        setAcceptanceState("idle")
        return
      }
      const accepted = (await res.json()) as FinalAcceptanceResponse
      if (!accepted.client_ready) {
        setActionError(profileActionError(
          t("review.acceptanceFailed"),
          null,
          t("review.acceptanceNotReady"),
          [],
          "preview",
          { regenerate: true },
        ))
        setAcceptanceState("idle")
        return
      }
      setGeneration((current) => (current ? { ...current, client_ready: true } : current))
      setAcceptanceState("accepted")
      setAcceptanceInvalidated(false)
    } catch {
      setActionError(profileActionError(
        t("review.acceptanceFailed"),
        null,
        t("review.networkErrorMessage"),
        [],
        "preview",
        { forceSaveRetry: true, regenerate: true },
      ))
      setAcceptanceState("idle")
    }
  }

  function recoveryLabel(option: string): string | null {
    switch (option) {
      case "save_profile_revision": return t("review.recoverySaveRevision")
      case "create_layout_proofs": return t("review.recoveryCreateLayoutProofs")
      case "approve_layout_proofs": return t("review.recoveryApproveLayoutProofs")
      case "reanalyze_target": return t("review.recoveryReanalyzeTarget")
      case "upload_new_target": return t("review.recoveryUploadNewTarget")
      default: return null
    }
  }

  async function handleRecovery(option: string) {
    switch (option) {
      case "save_profile_revision": {
        const saved = await persistRevision(null)
        if (saved) setActionError(null)
        return
      }
      case "create_layout_proofs":
        await handleCreateProofs()
        return
      case "approve_layout_proofs":
        await handleApproveProofs()
        return
      case "reanalyze_target":
      case "upload_new_target":
        onBack()
        return
      default:
        return
    }
  }

  function retryAction(error: ActionError) {
    switch (error.retry) {
      case "preview": void handleFinalPreview(error.forceSaveRetry); break
      case "export": void doExport(error.forceSaveRetry); break
      case "createProofs": void handleCreateProofs(); break
      case "approveProofs": void handleApproveProofs(); break
    }
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
          profile: applyEditsToProfile(profile, edits),
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
            onChange={(e) => handleBlindChange(e.target.checked)}
          />
          <span className="rv-blind-toggle__body">
            <span className="rv-blind-toggle__label">{t("review.blindProfileLabel")}</span>
            <span className="rv-blind-toggle__hint">{t("review.blindProfileHint")}</span>
          </span>
        </label>

        <button
          type="button"
          className={`rv-preview-btn${previewState === "error" ? " rv-preview-btn--error" : ""}`}
          disabled={previewState === "generating" || targetBlocked}
          title={targetBlocked ? t("review.targetDocxBlockedShort") : undefined}
          onClick={() => void handleFinalPreview()}
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
                onClick={() => setExportState("idle")}
              >
                {t("review.exportCancel")}
              </button>
              <button
                type="button"
                className="rv-export-anyway-btn"
                onClick={() => void doExport()}
              >
                {t("review.exportConfirmAction")}
              </button>
            </div>
          ) : (
            <div className="rv-export-split">
              <button
                type="button"
                className={`rv-export-btn${exportState === "generating" ? " rv-export-btn--loading" : ""}${exportState === "error" ? " rv-export-btn--error" : ""}`}
                disabled={exportState === "generating" || targetBlocked}
                title={targetBlocked ? t("review.targetDocxBlockedShort") : undefined}
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
                  <button type="button" className="rv-export-menu__item" onClick={handleExportClick}>
                    {t("review.exportPdf")}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </header>

      {targetFormat && (
        <section className="rv-target-panel" aria-label={t("review.targetSupportLabel")}>
          <div className="rv-target-panel__row">
            <span className="rv-target-panel__label">{t("review.targetSupportLabel")}</span>
            <span className={`rv-chip rv-chip--static ${isDocxTarget ? "rv-chip--warning" : "rv-chip--neutral"}`}>
              {isPdfTarget ? t("review.targetTypePdf") : t("review.targetTypeDocx")}
            </span>
            <span className="rv-target-panel__meta">
              {t("review.targetAnalysisStatus", { status: targetFormat.analysis_status })}
            </span>
            {targetFormat.structure_contract && (
              <span className="rv-target-panel__meta">
                {t("review.targetStructureContract", { contract: targetFormat.structure_contract })}
              </span>
            )}
            {isPdfTarget && (
              <>
                <span className="rv-target-panel__label">{t("review.proofLabel")}</span>
                <span className={`rv-chip rv-chip--static ${proofChipClass}`}>{proofLabel}</span>
                {proofCreated && (
                  <span className="rv-target-panel__meta">
                    {t("review.proofVariantCount", { count: proofStatus?.variants.length ?? 0 })}
                  </span>
                )}
                {failedVariants.length > 0 && (
                  <span className="rv-target-panel__warning">
                    {t("review.proofVariantsFailed", { variants: failedVariants.map((variant) => variant.variant).join(", ") })}
                  </span>
                )}
              </>
            )}
          </div>
          {isDocxTarget ? (
            <p className="rv-target-panel__blocked">{t("review.targetDocxBlocked")}</p>
          ) : (
            <div className="rv-proof">
              <span className="rv-target-panel__next">{proofNextStep}</span>
              <div className="rv-proof__actions">
                <button type="button" className="rv-proof__btn" onClick={() => void handleCreateProofs()} disabled={proofBusy !== "idle"}>
                  {proofBusy === "creating" ? t("review.proofCreating") : t("review.proofCreateBtn")}
                </button>
                <button type="button" className="rv-proof__btn" onClick={() => void loadProofStatus()} disabled={proofBusy !== "idle"}>
                  {proofBusy === "loading" ? t("review.proofLoading") : t("review.proofRefreshBtn")}
                </button>
                <button type="button" className="rv-proof__btn" onClick={() => void handleApproveProofs()} disabled={!canApproveProofs}>
                  {proofBusy === "approving" ? t("review.proofApproving") : t("review.proofApproveBtn")}
                </button>
              </div>
            </div>
          )}
          {targetFormat.unsupported_features.length > 0 && (
            <p className="rv-target-panel__warning">
              {t("review.targetUnsupportedFeatures", { features: targetFormat.unsupported_features.join(", ") })}
            </p>
          )}
          {targetFormat.analysis_warnings.length > 0 && (
            <p className="rv-target-panel__warning">
              {t("review.targetAnalysisWarnings", { warnings: targetFormat.analysis_warnings.join(" · ") })}
            </p>
          )}
        </section>
      )}

      {actionError && (
        <section className="rv-action-error" role="alert">
          <div className="rv-action-error__head">
            <span className="rv-action-error__title">{actionError.title}</span>
            {actionError.code && (
              <span className="rv-action-error__code">
                {t("review.errorCodeLabel")}: {actionError.code}
              </span>
            )}
          </div>
          <p className="rv-action-error__message">{actionError.message}</p>
          <div className="rv-action-error__actions">
            {actionError.options.map((option) => {
              const label = recoveryLabel(option)
              return label ? (
                <button
                  key={option}
                  type="button"
                  className="rv-action-error__btn"
                  onClick={() => void handleRecovery(option)}
                  disabled={proofBusy !== "idle"}
                >
                  {label}
                </button>
              ) : (
                <span key={option} className="rv-action-error__option">
                  {t("review.recoveryUnknown", { option })}
                </span>
              )
            })}
            {actionError.retry && (
              <button type="button" className="rv-action-error__btn" onClick={() => retryAction(actionError)}>
                {actionError.regenerate ? t("review.regenerateBtn") : t("review.retryBtn")}
              </button>
            )}
            <button type="button" className="rv-action-error__dismiss" onClick={() => setActionError(null)}>
              {t("review.dismissBtn")}
            </button>
          </div>
        </section>
      )}

      {acceptanceInvalidated && !isAccepted && (
        <p className="rv-invalidation-notice" role="status">{t("review.acceptanceInvalidated")}</p>
      )}

      {generation && (
        <section className="rv-acceptance">
          <div className="rv-acceptance__row">
            <span className="rv-target-panel__label">{t("review.acceptanceTitle")}</span>
            <span className={`rv-chip rv-chip--static ${isAccepted ? "rv-chip--success" : "rv-chip--warning"}`}>
              {isAccepted ? t("review.clientReady") : t("review.notClientReady")}
            </span>
            <span className="rv-target-panel__meta">
              {t("review.acceptanceRevision", { revision: generation.profile_revision_id })}
            </span>
          </div>
          <p className="rv-acceptance__hint">
            {isAccepted ? t("review.acceptanceAcceptedHint") : t("review.acceptanceHint")}
          </p>
          <div className="rv-acceptance__actions">
            <a className="rv-acceptance__link" href={generation.html_surface_url} target="_blank" rel="noreferrer">
              {t("review.openHtmlSurface")}
            </a>
            <a className="rv-acceptance__link" href={generation.pdf_preview_url} target="_blank" rel="noreferrer">
              {t("review.openPdfPreview")}
            </a>
            {!isAccepted && (
              <button
                type="button"
                className="rv-acceptance__btn"
                disabled={acceptanceState === "submitting"}
                onClick={() => void handleAcceptFinal()}
              >
                {acceptanceState === "submitting" ? t("review.accepting") : t("review.acceptBtn")}
              </button>
            )}
          </div>
        </section>
      )}

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
