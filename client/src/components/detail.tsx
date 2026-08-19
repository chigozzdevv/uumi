import type { ReactNode } from "react"

export function DetailList({ children }: { children: ReactNode }) {
  return <dl className="grid gap-x-8 gap-y-5 sm:grid-cols-2">{children}</dl>
}

export function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 text-[11px]">
      <dt className="text-[9px] font-medium text-[var(--ink-muted)]">{label}</dt>
      <dd className="mt-1.5 min-w-0 break-words font-medium text-[var(--ink)]">{children}</dd>
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
