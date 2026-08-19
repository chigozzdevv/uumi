import { useEffect, useId, useRef, type ReactNode } from "react"
import { Button } from "./button"

const focusable = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"

type ModalProps = {
  isOpen: boolean
  onClose: () => void
  title: string
  subtitle?: string
  children: ReactNode
  actions?: ReactNode
  footerStart?: ReactNode
  size?: "compact" | "wide"
}

export function Modal({ isOpen, onClose, title, subtitle, children, actions, footerStart, size = "compact" }: ModalProps) {
  const titleId = useId()
  const panel = useRef<HTMLElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!isOpen) return
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"
    panel.current?.focus()

    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault()
        onCloseRef.current()
        return
      }
      if (event.key !== "Tab" || !panel.current) return
      const elements = [...panel.current.querySelectorAll<HTMLElement>(focusable)]
      if (!elements.length) return
      const first = elements[0]
      const last = elements.at(-1)!
      if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    window.addEventListener("keydown", handleKey)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener("keydown", handleKey)
      previousFocus?.focus()
    }
  }, [isOpen])

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4 sm:p-6">
      <div className="absolute inset-0 bg-[#17152f]/25 backdrop-blur-[2px]" onMouseDown={onClose} aria-hidden="true" />
      <section ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId} className={`relative z-10 flex max-h-[calc(100vh-48px)] w-full flex-col overflow-hidden rounded-2xl border border-[var(--border)] bg-[#fafaf8] shadow-[0_28px_90px_rgba(32,29,55,0.2)] outline-none ${size === "wide" ? "max-w-[640px]" : "max-w-[560px]"}`}>
        <header className="px-6 pb-3 pt-6">
          <div className="min-w-0">
            <h2 id={titleId} className="m-0 text-lg font-semibold tracking-[-0.035em] text-[var(--ink)]">{title}</h2>
            {subtitle && <div className="mono mt-1 truncate text-[10px] text-[var(--ink-muted)]">{subtitle}</div>}
          </div>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6 pt-3">{children}</div>
        <footer className="flex shrink-0 items-center gap-3 bg-[#f1f1ee] px-6 py-4">
          <div className="min-w-0 flex-1">{footerStart}</div>
          <Button variant="secondary" onClick={onClose}>Close</Button>
          {actions}
        </footer>
      </section>
    </div>
  )
}
