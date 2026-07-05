import { useId, useRef, useState } from "react"
import type { DragEvent, ReactNode } from "react"
import { FileIcon } from "./icons"
import "./Dropzone.css"

type DropzoneProps = {
  icon: ReactNode
  title: string
  subtitle: string
  file: File | null
  onFileSelected: (file: File | null) => void
  accept?: string
  variant?: "primary" | "secondary"
  uploadLabel?: string
}

export function Dropzone({ icon, title, subtitle, file, onFileSelected, accept, variant, uploadLabel }: DropzoneProps) {
  const inputId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const isPrimary = variant === "primary"

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setIsDragging(false)
    const dropped = event.dataTransfer.files?.[0] ?? null
    if (dropped) onFileSelected(dropped)
  }

  return (
    <div
      className={`dropzone${isPrimary ? " dropzone--primary" : ""}${isDragging ? " dropzone--dragging" : ""}`}
      role="button"
      tabIndex={0}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") inputRef.current?.click()
      }}
      onDragOver={(event) => {
        event.preventDefault()
        setIsDragging(true)
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
    >
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept={accept}
        className="dropzone__input"
        onChange={(event) => onFileSelected(event.target.files?.[0] ?? null)}
      />
      {file ? (
        <>
          <FileIcon size={28} className="dropzone__icon dropzone__icon--file" />
          <p className="dropzone__title">{file.name}</p>
          <p className="dropzone__subtitle">Click or drop to replace</p>
        </>
      ) : (
        <>
          <span className="dropzone__icon">{icon}</span>
          <p className="dropzone__title">{title}</p>
          {uploadLabel && <span className="dropzone__upload-btn">{uploadLabel}</span>}
          <p className="dropzone__subtitle">{subtitle}</p>
        </>
      )}
    </div>
  )
}
