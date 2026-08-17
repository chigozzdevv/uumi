import type { HTMLAttributes } from "react"
import { cn } from "../../lib/utils"

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: "healthy" | "warning" | "danger" | "neutral" | "active"
  dot?: boolean
}

export function Badge({ className, variant = "neutral", dot = true, children, ...props }: BadgeProps) {
  const variants = {
    healthy: "bg-[var(--green-soft)] text-[var(--green)]",
    warning: "bg-[var(--amber-soft)] text-[var(--amber)]",
    danger: "bg-[var(--red-soft)] text-[var(--red)]",
    neutral: "bg-[#ececea] text-[var(--ink-soft)]",
    active: "bg-[var(--accent-soft)] text-[var(--accent)]",
  }
  const dots = {
    healthy: "bg-[var(--green)]",
    warning: "bg-[var(--amber)]",
    danger: "bg-[var(--red)]",
    neutral: "bg-[var(--ink-muted)]",
    active: "bg-[var(--accent)]",
  }

  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-semibold capitalize", variants[variant], className)} {...props}>
      {dot && <span className={cn("size-1.5 rounded-full", dots[variant])} />}
      {children}
    </span>
  )
}
