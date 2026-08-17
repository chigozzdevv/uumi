import { providerName } from "../lib/format"

const colors: Record<string, string> = {
  sendgrid: "bg-[#dfeaf8] text-[#2776bd]",
  stripe: "bg-[#e9e7fb] text-[#635bce]",
  github: "bg-[#e8e8e7] text-[#25252a]",
  "internal-vendor": "bg-[#f5eadc] text-[#9a651c]",
  netsuite: "bg-[#dfeaf3] text-[#2b668a]",
  segment: "bg-[#e3f2e9] text-[#238458]",
  snowflake: "bg-[#e0f2f4] text-[#168197]",
  "cloud-run": "bg-[#e0e9fb] text-[#3f6eaf]",
  "google-secret-manager": "bg-[#f5eadc] text-[#8c631f]",
  "cloud-monitoring": "bg-[#e6e2f5] text-[#57468d]",
}

export function Provider({ value, label = true }: { value: string; label?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <span className={`grid size-7 shrink-0 place-items-center rounded-full text-[10px] font-bold uppercase ${colors[value] ?? "bg-[#e8e8e7] text-[var(--ink-soft)]"}`}>
        {providerName(value).slice(0, 2)}
      </span>
      {label && <span>{providerName(value)}</span>}
    </span>
  )
}
