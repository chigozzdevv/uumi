import { Check, ChevronRight } from "lucide-react"
import { useEffect, useRef, useState, type ReactNode } from "react"

export function PageHeader({ section, actions }: { section: string; actions?: ReactNode }) {
  const [organisationsOpen, setOrganisationsOpen] = useState(false)
  const organisationMenu = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!organisationMenu.current?.contains(event.target as Node)) setOrganisationsOpen(false)
    }
    window.addEventListener("pointerdown", close)
    return () => window.removeEventListener("pointerdown", close)
  }, [])

  return (
    <header className="mb-8 flex min-h-8 items-center justify-between gap-4">
      <div className="flex items-center gap-2 text-[11px] font-medium text-[var(--ink-muted)]">
        <div ref={organisationMenu} className="relative">
          <button
            className="focus-ring -ml-2 flex items-center gap-1 rounded-lg px-2 py-1.5 transition hover:bg-white/60 hover:text-[var(--ink)]"
            aria-expanded={organisationsOpen}
            aria-haspopup="menu"
            onClick={() => setOrganisationsOpen((open) => !open)}
          >
            <span>Acme Corporation</span>
            <ChevronRight className={`size-3 transition-transform ${organisationsOpen ? "rotate-90" : ""}`} />
          </button>
          {organisationsOpen && (
            <div className="absolute left-0 top-9 z-40 w-56 rounded-xl border border-[var(--border)] bg-white p-2 shadow-[0_16px_45px_rgba(23,21,47,0.12)]" role="menu">
              <div className="px-3 pb-2 pt-1 text-[9px] font-semibold uppercase tracking-[0.08em] text-[var(--ink-muted)]">Organization</div>
              <button className="focus-ring flex w-full items-center justify-between rounded-lg bg-[var(--surface-soft)] px-3 py-2.5 text-left text-[11px] font-semibold text-[var(--ink)]" role="menuitem" onClick={() => setOrganisationsOpen(false)}>
                Acme Corporation
                <Check className="size-3.5 text-[var(--green)]" />
              </button>
            </div>
          )}
        </div>
        <span className="text-[var(--ink)]">{section}</span>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  )
}
