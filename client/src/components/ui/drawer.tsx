import { useEffect, type ReactNode } from "react"
import { X } from "lucide-react"

export function Drawer({ isOpen, onClose, title, subtitle, children }: { isOpen: boolean; onClose: () => void; title: string; subtitle?: string; children: ReactNode }) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => event.key === "Escape" && onClose()
    if (isOpen) window.addEventListener("keydown", close)
    return () => window.removeEventListener("keydown", close)
  }, [isOpen, onClose])

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button className="absolute inset-0 bg-[#17152f]/20 backdrop-blur-[2px]" onClick={onClose} aria-label="Close details" />
      <section className="relative z-10 flex h-full w-full max-w-[560px] flex-col border-l border-[var(--border)] bg-[#fafaf8] shadow-[-22px_0_70px_rgba(32,29,55,0.12)]">
        <header className="flex items-start justify-between border-b border-[var(--border-soft)] px-7 py-6">
          <div className="min-w-0 pr-4">
            <div className="text-lg font-semibold tracking-[-0.035em] text-[var(--ink)]">{title}</div>
            {subtitle && <div className="mono mt-1 truncate text-[10px] text-[var(--ink-muted)]">{subtitle}</div>}
          </div>
          <button className="focus-ring grid size-9 shrink-0 place-items-center rounded-xl border border-[var(--border)] bg-white hover:bg-[var(--surface-soft)]" onClick={onClose} aria-label="Close details">
            <X className="size-4" />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto px-7 py-6">{children}</div>
      </section>
    </div>
  )
}
