import { useState, useRef, useEffect, useCallback } from "react"
import "./ChatWidget.css"

type Message = { role: "user" | "assistant"; content: string }

const GREETING: Message = {
  role: "assistant",
  content: "Hi! I'm Reform's assistant. Ask me anything about how the tool works, or what features we offer.",
}

const MIN_HEIGHT = 300
const MAX_HEIGHT = window.innerHeight - 120
const DEFAULT_HEIGHT = 480

export function ChatWidget() {
  const [open, setOpen] = useState(true)
  const [height, setHeight] = useState(DEFAULT_HEIGHT)
  const [messages, setMessages] = useState<Message[]>([GREETING])
  const [input, setInput] = useState("")
  const [streaming, setStreaming] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const dragStartY = useRef<number | null>(null)
  const dragStartH = useRef<number>(DEFAULT_HEIGHT)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, streaming])

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragStartY.current = e.clientY
    dragStartH.current = height

    function onMove(ev: MouseEvent) {
      if (dragStartY.current === null) return
      const delta = dragStartY.current - ev.clientY
      const next = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, dragStartH.current + delta))
      setHeight(next)
    }
    function onUp() {
      dragStartY.current = null
      window.removeEventListener("mousemove", onMove)
      window.removeEventListener("mouseup", onUp)
    }
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
  }, [height])

  async function send() {
    const text = input.trim()
    if (!text || streaming) return
    setInput("")

    const next: Message[] = [...messages, { role: "user", content: text }]
    setMessages(next)
    setStreaming(true)

    const assistantMsg: Message = { role: "assistant", content: "" }
    setMessages([...next, assistantMsg])

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: next }),
      })
      if (!res.ok || !res.body) throw new Error("Chat unavailable")

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ""

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split("\n")
        buf = lines.pop() ?? ""
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue
          try {
            const parsed = JSON.parse(line.slice(6)) as { text?: string; error?: string }
            if (parsed.error) throw new Error(parsed.error)
            if (parsed.text) {
              assistantMsg.content += parsed.text
              setMessages(prev => {
                const updated = [...prev]
                updated[updated.length - 1] = { ...assistantMsg }
                return updated
              })
            }
          } catch { /* ignore malformed chunk */ }
        }
      }
    } catch {
      setMessages(prev => {
        const updated = [...prev]
        updated[updated.length - 1] = { role: "assistant", content: "Sorry, something went wrong. Please try again." }
        return updated
      })
    } finally {
      setStreaming(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <>
      {open && (
        <div className="chat-window" style={{ height }} role="dialog" aria-label="Reform assistant">
          <div className="chat-resize-handle" onMouseDown={onDragStart} title="Drag to resize" />

          <div className="chat-header">
            <span className="chat-header-dot" aria-hidden="true" />
            Reform Assistant
            <button className="chat-header-close" onClick={() => setOpen(false)} aria-label="Close chat">×</button>
          </div>

          <div className="chat-messages" aria-live="polite">
            {messages.map((msg, i) => (
              <div key={i} className={`chat-msg chat-msg--${msg.role}`}>
                {msg.content}
              </div>
            ))}
            {streaming && messages[messages.length - 1]?.content === "" && (
              <div className="chat-typing" aria-label="Thinking">
                <span /><span /><span />
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="chat-input-row">
            <textarea
              ref={inputRef}
              className="chat-input"
              placeholder="Ask me anything…"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={1}
              disabled={streaming}
            />
            <button className="chat-send" onClick={send} disabled={!input.trim() || streaming} aria-label="Send">
              ↑
            </button>
          </div>
        </div>
      )}

      <button
        className="chat-fab"
        onClick={() => setOpen(o => !o)}
        aria-label={open ? "Close assistant" : "Open assistant"}
      >
        {open ? "×" : "💬"}
      </button>
    </>
  )
}
