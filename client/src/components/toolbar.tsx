import { Search, SlidersHorizontal } from "lucide-react"
import type { SelectHTMLAttributes } from "react"

export function Toolbar({ value, onChange, placeholder, filters }: { value: string; onChange: (value: string) => void; placeholder: string; filters?: Array<SelectHTMLAttributes<HTMLSelectElement> & { label: string }> }) {
  return (
    <div className="mb-5 flex flex-col gap-2.5 sm:flex-row">
      <label className="relative min-w-0 flex-1">
        <Search className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-[var(--ink-muted)]" />
        <input
          className="focus-ring h-12 w-full rounded-[14px] border border-[var(--border)] bg-white pl-11 pr-4 text-[12px] text-[var(--ink)] placeholder:text-[var(--ink-muted)]"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
        />
      </label>
      {filters?.map(({ label, ...props }) => (
        <label key={label} className="relative">
          <SlidersHorizontal className="pointer-events-none absolute left-3.5 top-1/2 size-3.5 -translate-y-1/2 text-[var(--ink-muted)]" />
          <select className="focus-ring h-12 min-w-[150px] appearance-none rounded-[14px] border border-[var(--border)] bg-white pl-9 pr-8 text-[11px] font-medium text-[var(--ink-soft)]" {...props}>
            {props.children}
          </select>
        </label>
      ))}
    </div>
  )
}
