import { Check } from "lucide-react"

export function Journey({ steps, current }: { steps: string[]; current: number }) {
  return (
    <div className="mb-7" aria-label={`Step ${current + 1} of ${steps.length}`}>
      <ol className="grid grid-cols-5">
        {steps.map((label, index) => {
          const complete = index < current
          const active = index === current
          return (
            <li key={label} className="relative flex flex-col items-center gap-2 text-center">
              {index > 0 && <span className={`absolute right-1/2 top-[13px] h-px w-full ${complete || active ? "bg-[var(--accent)]" : "bg-[var(--border)]"}`} />}
              <span className={`relative z-10 grid size-7 place-items-center rounded-full border text-[9px] font-semibold ${complete ? "border-[var(--accent)] bg-[var(--accent)] text-white" : active ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]" : "border-[var(--border)] bg-[#fafaf8] text-[var(--ink-muted)]"}`}>
                {complete ? <Check className="size-3.5" /> : index + 1}
              </span>
              <span className={`text-[9px] font-medium ${active ? "text-[var(--ink)]" : "text-[var(--ink-muted)]"}`}>{label}</span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
