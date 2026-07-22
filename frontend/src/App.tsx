import { useState } from "react"
import { MainScreen } from "./screens/MainScreen"
import { ReviewScreen } from "./screens/ReviewScreen"
import { AuthScreen } from "./screens/AuthScreen"
import { PricingScreen } from "./screens/PricingScreen"
import { LegalScreen } from "./screens/LegalScreen"
import type { ProcessResponse, TargetFormatMetadata, TargetFormatUploadResponse } from "./types"
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
  | {
      status: "done"
      data: ProcessResponse
      fileName: string
      targetFormatName: string
      targetFormat: TargetFormatMetadata
      resumeFile: File
    }

export default function App() {
  const [state, setState] = useState<AppState>({ status: "idle" })

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

      const targetData: TargetFormatUploadResponse = await targetRes.json()
      setState({
        status: "done",
        data,
        fileName: resumeFile.name,
        targetFormatName: targetFile.name,
        targetFormat: targetData.target_format,
        resumeFile,
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
        resumeFile={state.resumeFile}
        resumeFileName={state.fileName}
        formatName={state.targetFormatName}
        targetFormat={state.targetFormat}
        onBack={() => setState({ status: "idle" })}
      />
    )
  }

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
      onDismissError={() => setState({ status: "idle" })}
      onLogin={() => setState({ status: "login", from: "idle" })}
      onSignup={() => setState({ status: "signup", from: "idle" })}
      onPricing={() => setState({ status: "pricing" })}
      onTerms={() => setState({ status: "terms" })}
      onPrivacy={() => setState({ status: "privacy" })}
    />
  )
}
