import type { ReactNode } from "react"
import { Journey } from "./journey"
import { PageHeader } from "./header"
import { Button } from "./ui/button"

export function SetupPage({
  eyebrow,
  title,
  description,
  steps,
  current,
  onBack,
  onCancel,
  primary,
  children,
  error,
}: {
  eyebrow: string
  title: string
  description: string
  steps: string[]
  current: number
  onBack: () => void
  onCancel: () => void
  primary: ReactNode
  children: ReactNode
  error?: string
}) {
  return (
    <div className="page max-w-[1080px]">
      <PageHeader eyebrow={eyebrow} title={title} description={description} onBack={onCancel} />
      <section className="overflow-hidden rounded-2xl border border-[var(--border)] bg-white">
        <div className="border-b border-[var(--border-soft)] px-6 pt-6"><Journey steps={steps} current={current} /></div>
        <div className="min-h-[360px] px-6 py-7 sm:px-8">{children}</div>
        {error && <div role="alert" className="mx-6 mb-5 rounded-xl border border-[#ebcfd3] bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)] sm:mx-8">{error}</div>}
        <footer className="flex items-center gap-3 border-t border-[var(--border-soft)] bg-[var(--surface-soft)] px-6 py-4 sm:px-8">
          <div className="flex-1">{current > 0 && <Button variant="ghost" onClick={onBack}>Back</Button>}</div>
          <Button variant="secondary" onClick={onCancel}>Cancel</Button>
          {primary}
        </footer>
      </section>
    </div>
  )
}

export function SuccessPage({ eyebrow, title, description, onBack, actions, children }: { eyebrow: string; title: string; description: string; onBack: () => void; actions: ReactNode; children?: ReactNode }) {
  return <div className="page max-w-[920px]"><PageHeader eyebrow={eyebrow} title={title} description={description} onBack={onBack} actions={actions} />{children && <div className="rounded-2xl border border-[var(--border)] bg-white p-7">{children}</div>}</div>
}

export function FormGrid({ children }: { children: ReactNode }) {
  return <div className="grid gap-5 sm:grid-cols-2">{children}</div>
}

export function Field({ label, hint, children, wide = false }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  return <label className={wide ? "block sm:col-span-2" : "block"}><span className="mb-1.5 block text-[10px] font-semibold text-[var(--ink-soft)]">{label}</span>{children}{hint && <span className="mt-1.5 block text-[9px] leading-4 text-[var(--ink-muted)]">{hint}</span>}</label>
}

export function Fieldset({ label, hint, children, wide = false }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  return <fieldset className={wide ? "sm:col-span-2" : undefined}><legend className="mb-1.5 text-[10px] font-semibold text-[var(--ink-soft)]">{label}</legend>{children}{hint && <span className="mt-1.5 block text-[9px] leading-4 text-[var(--ink-muted)]">{hint}</span>}</fieldset>
}

export const formControl = "focus-ring h-11 w-full rounded-xl border border-[var(--border)] bg-white px-3.5 text-[11px] text-[var(--ink)] outline-none placeholder:text-[var(--ink-muted)]"
