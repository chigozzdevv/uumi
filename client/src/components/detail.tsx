import type { ReactNode } from "react"

export function DetailList({ children }: { children: ReactNode }) {
  return <dl className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">{children}</dl>
}

export function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[135px_1fr] gap-4 py-3.5 text-[11px]">
      <dt className="text-[var(--ink-muted)]">{label}</dt>
      <dd className="min-w-0 break-words font-medium text-[var(--ink)]">{children}</dd>
    </div>
  )
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-7 last:mb-0">
      <h3 className="eyebrow mb-3">{title}</h3>
      {children}
    </section>
  )
}
