import { ChevronDown, Search, SlidersHorizontal } from "lucide-react"
import { useEffect, useRef, useState, type SelectHTMLAttributes } from "react"

type ToolbarFilter = SelectHTMLAttributes<HTMLSelectElement> & { label: string }

export function Toolbar({ value, onChange, placeholder, filters }: { value: string; onChange: (value: string) => void; placeholder: string; filters?: ToolbarFilter[] }) {
  const [open, setOpen] = useState(false)
  const filterMenu = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!filterMenu.current?.contains(event.target as Node)) setOpen(false)
    }
    window.addEventListener("pointerdown", close)
    return () => window.removeEventListener("pointerdown", close)
  }, [])

  return (
    <div className="resource-toolbar flex items-center gap-3">
      <label className="relative min-w-0 flex-1">
        <Search className="pointer-events-none absolute left-4 top-1/2 size-[18px] -translate-y-1/2 text-[var(--ink)]" strokeWidth={2} />
        <input
          className="focus-ring h-[52px] w-full rounded-[14px] border border-[var(--border)] bg-white pl-12 pr-4 text-[13px] text-[var(--ink)] placeholder:text-[var(--ink-soft)]"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
        />
      </label>

      {filters?.length ? (
        <div ref={filterMenu} className="relative shrink-0">
          <button
            className="focus-ring grid size-[52px] place-items-center rounded-[14px] bg-[var(--accent-soft)] text-[var(--accent)] transition hover:bg-[#ddd8ef]"
            aria-label="Filter results"
            aria-expanded={open}
            onClick={() => setOpen((current) => !current)}
          >
            <SlidersHorizontal className="size-[18px]" strokeWidth={2} />
          </button>

          {open && (
            <div className="absolute right-0 top-[60px] z-30 w-60 rounded-xl border border-[var(--border)] bg-white p-3 shadow-[0_16px_45px_rgba(23,21,47,0.12)]">
              {filters.map(({ label, children, ...props }) => (
                <label key={label} className="block [&+&]:mt-3">
                  <span className="mb-1.5 block px-1 text-[10px] font-medium text-[var(--ink-soft)]">{label}</span>
                  <span className="relative block">
                    <select
                      className="focus-ring h-10 w-full appearance-none rounded-lg border border-[var(--border)] bg-[var(--surface-soft)] px-3 pr-8 text-[11px] font-medium text-[var(--ink)]"
                      {...props}
                    >
                      {children}
                    </select>
                    <ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-3.5 -translate-y-1/2 text-[var(--ink-muted)]" />
                  </span>
                </label>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  )
}
