import { useEffect, useState } from "react"
import { MainScreen } from "./screens/MainScreen"
import { ReviewScreen } from "./screens/ReviewScreen"
import { AuthScreen } from "./screens/AuthScreen"
import { PricingScreen } from "./screens/PricingScreen"
import { LegalScreen } from "./screens/LegalScreen"
import type { ProcessResponse, PreviewJob, ReviewStateResponse } from "./types"
import "./App.css"

type AuthFrom = "idle" | "pricing" | "terms" | "privacy"

type AppState =
  | { status: "idle" }
  | { status: "pricing" }
  | { status: "terms" }
  | { status: "privacy" }
  | { status: "login";  from: AuthFrom }
  | { status: "signup"; from: AuthFrom }
  | { status: "loading"; fileName: string; targetFormatName: string }
  | { status: "error"; message: string }
  | { status: "restoring" }
  | {
      status: "done"
      data: ProcessResponse
      fileName: string
      targetFormatName: string
      previewJob?: PreviewJob | null
      blindProfile: boolean
    }

export default function App() {
  const [state, setState] = useState<AppState>(() => ({ status: location.hash.startsWith("#review/") ? "restoring" : "idle" }))

  function goHome() {
    history.replaceState(null, "", location.pathname + location.search)
    setState({ status: "idle" })
  }

  useEffect(() => {
    if (!location.hash.startsWith("#review/")) return
    let cancelled = false
    async function restore() {
      try {
        const artifactId = location.hash.slice("#review/".length)
        if (!/^artifact_[a-zA-Z0-9_-]+$/.test(artifactId)) throw new Error("Invalid saved review link.")
        const response = await fetch(`/api/artifacts/${artifactId}/review`)
        const body = await response.json()
        if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Could not restore the saved review.")
        const saved = body as ReviewStateResponse
        if (!cancelled) setState({ status: "done", data: saved.data, fileName: saved.source_filename,
          targetFormatName: saved.target_format?.input_filename ?? "Standard format", previewJob: saved.preview_job, blindProfile: saved.blind_profile })
      } catch (error) {
        if (!cancelled) setState({ status: "error", message: String(error) })
      }
    }
    restore()
    return () => { cancelled = true }
  }, [])

  async function handleConvert(resumeFile: File, targetFile: File) {
    setState({ status: "loading", fileName: resumeFile.name, targetFormatName: targetFile.name })

    const formData = new FormData()
    formData.append("file", resumeFile)

    try {
      const res = await fetch("/api/process", { method: "POST", body: formData })

      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        const detail: string = body.detail ?? "Unknown error"
        if (res.status === 400) {
          setState({ status: "error", message: `Unsupported file — ${detail}` })
        } else {
          setState({ status: "error", message: `Server error — please try again. (${detail})` })
        }
        return
      }

      const data: ProcessResponse = await res.json()
      const targetFormData = new FormData()
      targetFormData.append("file", targetFile)
      targetFormData.append("artifact_id", data.artifact_id)
      const targetRes = await fetch("/api/target-format", { method: "POST", body: targetFormData })

      if (!targetRes.ok) {
        const body = await targetRes.json().catch(() => ({}))
        const detail: string = body.detail ?? "Unknown error"
        setState({ status: "error", message: `Target format error — ${detail}` })
        return
      }

      history.replaceState(null, "", `${location.pathname}${location.search}#review/${data.artifact_id}`)
      setState({
        status: "done",
        data,
        fileName: resumeFile.name,
        targetFormatName: targetFile.name,
        blindProfile: false,
      })
    } catch {
      setState({
        status: "error",
        message: "Could not reach the server — check your connection and try again.",
      })
    }
  }

  if (state.status === "login" || state.status === "signup") {
    const from = state.from
    return (
      <AuthScreen
        mode={state.status}
        onSwitchMode={() => setState({ status: state.status === "login" ? "signup" : "login", from })}
        onGoHome={() => setState({ status: from })}
      />
    )
  }

  if (state.status === "done") {
    return (
      <ReviewScreen
        data={state.data}
        resumeFileName={state.fileName}
        formatName={state.targetFormatName}
        previewJob={state.previewJob}
        initialBlindProfile={state.blindProfile}
        onBack={goHome}
      />
    )
  }

  if (state.status === "restoring") return <p role="status">Restoring saved review…</p>

  if (state.status === "pricing") {
    return (
      <PricingScreen
        onHome={() => setState({ status: "idle" })}
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
        onHome={() => setState({ status: "idle" })}
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
      onConvert={handleConvert}
      isLoading={state.status === "loading"}
      error={state.status === "error" ? state.message : null}
      onDismissError={goHome}
      onLogin={() => setState({ status: "login", from: "idle" })}
      onSignup={() => setState({ status: "signup", from: "idle" })}
      onPricing={() => setState({ status: "pricing" })}
      onTerms={() => setState({ status: "terms" })}
      onPrivacy={() => setState({ status: "privacy" })}
    />
  )
}
