import { useState } from "react"
import { MainScreen } from "./screens/MainScreen"
import { ReviewScreen } from "./screens/ReviewScreen"
import { AuthScreen } from "./screens/AuthScreen"
import type { ProcessResponse } from "./types"
import "./App.css"

type AppState =
  | { status: "idle" }
  | { status: "login" }
  | { status: "signup" }
  | { status: "loading"; fileName: string }
  | { status: "error"; message: string }
  | { status: "done"; data: ProcessResponse; fileName: string }

export default function App() {
  const [state, setState] = useState<AppState>({ status: "idle" })

  async function handleConvert(resumeFile: File) {
    setState({ status: "loading", fileName: resumeFile.name })

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
      setState({ status: "done", data, fileName: resumeFile.name })
    } catch {
      setState({
        status: "error",
        message: "Could not reach the server — check your connection and try again.",
      })
    }
  }

  if (state.status === "login" || state.status === "signup") {
    return (
      <AuthScreen
        mode={state.status}
        onSwitchMode={() => setState({ status: state.status === "login" ? "signup" : "login" })}
        onGoHome={() => setState({ status: "idle" })}
      />
    )
  }

  if (state.status === "done") {
    return (
      <ReviewScreen
        data={state.data}
        resumeFileName={state.fileName}
        formatName="Apex Standard"
        onBack={() => setState({ status: "idle" })}
      />
    )
  }

  return (
    <MainScreen
      onConvert={handleConvert}
      isLoading={state.status === "loading"}
      error={state.status === "error" ? state.message : null}
      onDismissError={() => setState({ status: "idle" })}
      onLogin={() => setState({ status: "login" })}
      onSignup={() => setState({ status: "signup" })}
    />
  )
}
