import { useState } from "react"
import { useTranslation } from "react-i18next"
import { formatApiError } from "./apiError"
import { MainScreen } from "./screens/MainScreen"
import { ReviewScreen } from "./screens/ReviewScreen"
import { AuthScreen } from "./screens/AuthScreen"
import { PricingScreen } from "./screens/PricingScreen"
import { LegalScreen } from "./screens/LegalScreen"
import type { ConversionMode, ProcessResponse, TargetFormatMetadata, TargetFormatUploadResponse } from "./types"
import "./App.css"

type AuthFrom = "idle" | "pricing" | "terms" | "privacy"

type AppState =
  | { status: "idle"; mode: ConversionMode }
  | { status: "pricing" }
  | { status: "terms" }
  | { status: "privacy" }
  | { status: "login";  from: AuthFrom }
  | { status: "signup"; from: AuthFrom }
  | { status: "loading"; mode: ConversionMode; fileName: string; targetFormatName: string | null }
  | { status: "error"; mode: ConversionMode; message: string }
  | {
      status: "done"
      mode: ConversionMode
      data: ProcessResponse
      fileName: string
      targetFormatName: string | null
      targetFormat: TargetFormatMetadata | null
      resumeFile: File
    }

export default function App() {
  const { t } = useTranslation()
  const [state, setState] = useState<AppState>({ status: "idle", mode: "built-in" })

  // The mode is chosen on the upload screen and then carried by every state an attempt reaches,
  // so a failed uploaded-target run returns to the upload screen in uploaded-target mode.
  function showUpload(mode: ConversionMode = "built-in") {
    setState({ status: "idle", mode })
  }

  async function processResume(resumeFile: File, mode: ConversionMode): Promise<ProcessResponse | null> {
    const formData = new FormData()
    formData.append("file", resumeFile)
    const res = await fetch("/api/process", { method: "POST", body: formData })
    if (!res.ok) {
      const prefix = res.status === 400 ? "Unsupported file — " : "Server error — please try again. "
      setState({ status: "error", mode, message: await formatApiError(res, prefix) })
      return null
    }
    return (await res.json()) as ProcessResponse
  }

  async function analyzeTarget(artifactId: string, targetFile: File, mode: ConversionMode): Promise<TargetFormatMetadata | null> {
    const formData = new FormData()
    formData.append("file", targetFile)
    formData.append("artifact_id", artifactId)
    const res = await fetch("/api/target-format", { method: "POST", body: formData })
    if (!res.ok) {
      setState({ status: "error", mode, message: await formatApiError(res, "Target format error — ") })
      return null
    }
    const data = (await res.json()) as TargetFormatUploadResponse
    return data.target_format
  }

  // Replacing a blocked target re-analyzes against the same artifact, so the candidate processing
  // already done stands while the target identity starts over.
  async function handleReplaceTarget(artifact_id: string, targetFile: File): Promise<TargetFormatMetadata> {
    const formData = new FormData()
    formData.append("file", targetFile)
    formData.append("artifact_id", artifact_id)
    const res = await fetch("/api/target-format", { method: "POST", body: formData })
    if (!res.ok) throw new Error(await formatApiError(res, "Target format error — "))
    const data = (await res.json()) as TargetFormatUploadResponse
    return data.target_format
  }

  async function handleConvert(resumeFile: File, targetFile: File | null, mode: ConversionMode) {
    // An uploaded-target request never degrades to the built-in template.
    if (mode === "uploaded-target" && !targetFile) {
      setState({ status: "error", mode, message: t("errors.targetRequired") })
      return
    }
    const target = mode === "uploaded-target" ? targetFile : null
    setState({ status: "loading", mode, fileName: resumeFile.name, targetFormatName: target?.name ?? null })

    try {
      const data = await processResume(resumeFile, mode)
      if (!data) return
      const targetFormat = target ? await analyzeTarget(data.artifact_id, target, mode) : null
      if (target && !targetFormat) return
      setState({
        status: "done",
        mode,
        data,
        fileName: resumeFile.name,
        targetFormatName: target?.name ?? null,
        targetFormat,
        resumeFile,
      })
    } catch {
      setState({
        status: "error",
        mode,
        message: t("errors.network"),
      })
    }
  }

  if (state.status === "login" || state.status === "signup") {
    const from = state.from
    return (
      <AuthScreen
        mode={state.status}
        onSwitchMode={() => setState({ status: state.status === "login" ? "signup" : "login", from })}
        onGoHome={() => (from === "idle" ? showUpload() : setState({ status: from }))}
      />
    )
  }

  if (state.status === "done") {
    return (
      <ReviewScreen
        data={state.data}
        resumeFile={state.resumeFile}
        resumeFileName={state.fileName}
        formatName={state.targetFormatName}
        targetFormat={state.targetFormat}
        onReplaceTarget={handleReplaceTarget}
        onBack={() => showUpload(state.mode)}
      />
    )
  }

  if (state.status === "pricing") {
    return (
      <PricingScreen
        onHome={() => showUpload()}
        onPricing={() => setState({ status: "pricing" })}
        onLogin={() => setState({ status: "login", from: "pricing" })}
        onSignup={() => setState({ status: "signup", from: "pricing" })}
        onTerms={() => setState({ status: "terms" })}
        onPrivacy={() => setState({ status: "privacy" })}
      />
    )
  }

  if (state.status === "terms" || state.status === "privacy") {
    const page = state.status
    return (
      <LegalScreen
        page={page}
        onHome={() => showUpload()}
        onPricing={() => setState({ status: "pricing" })}
        onLogin={() => setState({ status: "login", from: page })}
        onSignup={() => setState({ status: "signup", from: page })}
        onTerms={() => setState({ status: "terms" })}
        onPrivacy={() => setState({ status: "privacy" })}
      />
    )
  }

  return (
    <MainScreen
      mode={state.mode}
      onModeChange={showUpload}
      onConvert={handleConvert}
      isLoading={state.status === "loading"}
      error={state.status === "error" ? state.message : null}
      onDismissError={() => showUpload(state.mode)}
      onLogin={() => setState({ status: "login", from: "idle" })}
      onSignup={() => setState({ status: "signup", from: "idle" })}
      onPricing={() => setState({ status: "pricing" })}
      onTerms={() => setState({ status: "terms" })}
      onPrivacy={() => setState({ status: "privacy" })}
    />
  )
}
