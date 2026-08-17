import { ChevronRight } from "lucide-react"
import type { ReactNode } from "react"

export function PageHeader({ section, title, description, actions }: { section: string; title: string; description: string; actions?: ReactNode }) {
  return (
    <header className="mb-9">
      <div className="mb-8 flex items-center gap-2 text-[11px] font-medium text-[var(--ink-muted)]">
        <span>Home</span>
        <ChevronRight className="size-3" />
        <span className="text-[var(--ink)]">{section}</span>
      </div>
      <div className="flex flex-col items-start justify-between gap-5 md:flex-row md:items-end">
        <div>
          <h1 className="page-title">{title}</h1>
          <p className="page-copy">{description}</p>
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
    </header>
  )
}
