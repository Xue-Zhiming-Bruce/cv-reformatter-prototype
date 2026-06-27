import { useState } from "react"
import { MainScreen } from "./screens/MainScreen"
import { ReviewScreen } from "./screens/ReviewScreen"
import { sampleReview } from "./data/sampleReview"
import "./App.css"

export default function App() {
  const [resumeFileName, setResumeFileName] = useState<string | null>(null)

  function handleConvert(resumeFile: File) {
    // TODO(step 4): replace sampleReview with a real POST /api/process response.
    setResumeFileName(resumeFile.name)
  }

  if (resumeFileName) {
    return (
      <ReviewScreen
        data={sampleReview}
        resumeFileName={resumeFileName}
        formatName="Apex Standard"
        onBack={() => setResumeFileName(null)}
      />
    )
  }

  return <MainScreen onConvert={handleConvert} />
}
