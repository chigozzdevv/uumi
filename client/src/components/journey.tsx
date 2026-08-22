import { Check } from "lucide-react"

export function Journey({ steps, current }: { steps: string[]; current: number }) {
  const progress = steps.length > 1 ? (current / (steps.length - 1)) * 100 : 0

  return (
    <div className="mb-7" aria-label={`Step ${current + 1} of ${steps.length}`}>
      <ol className="relative h-[47px]">
        {steps.length > 1 && <>
          <span className="absolute left-3.5 right-3.5 top-[13px] h-px bg-[var(--border)]" />
          <span className="absolute left-3.5 top-[13px] h-px bg-[var(--accent)]" style={{ width: `calc((100% - 28px) * ${progress / 100})` }} />
        </>}
        {steps.map((label, index) => {
          const complete = index < current
          const active = index === current
          return (
            <li
              key={label}
              className={`absolute top-0 flex w-24 flex-col gap-2 ${index === 0 ? "left-0 items-start text-left" : index === steps.length - 1 ? "right-0 items-end text-right" : "-translate-x-1/2 items-center text-center"}`}
              style={index > 0 && index < steps.length - 1 ? { left: `${(index / (steps.length - 1)) * 100}%` } : undefined}
            >
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
