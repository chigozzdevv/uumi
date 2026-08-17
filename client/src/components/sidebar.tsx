import { useEffect, useRef, useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Activity,
  AppWindow,
  BellRing,
  BookOpenText,
  Bot,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  FileClock,
  Gauge,
  KeyRound,
  Menu,
  Network,
  RotateCw,
  Settings2,
  ShieldCheck,
  X,
  type LucideIcon,
} from "lucide-react"
import { api } from "../lib/api"

export type NavItem =
  | "overview"
  | "credentials"
  | "applications"
  | "incidents"
  | "rotations"
  | "approvals"
  | "policies"
  | "playbooks"
  | "agents"
  | "connections"
  | "audits"
  | "settings"
  | "help"

interface NavEntry {
  id: NavItem
  label: string
  icon: LucideIcon
  count?: number
}

const sections: Array<{ label?: string; items: NavEntry[] }> = [
  { items: [{ id: "overview", label: "Overview", icon: Gauge }] },
  {
    label: "Inventory",
    items: [
      { id: "credentials", label: "Credentials", icon: KeyRound },
      { id: "applications", label: "Applications", icon: AppWindow },
    ],
  },
  {
    label: "Operations",
    items: [
      { id: "incidents", label: "Incidents", icon: Activity, count: 2 },
      { id: "rotations", label: "Rotations", icon: RotateCw },
      { id: "approvals", label: "Approvals", icon: ShieldCheck, count: 2 },
    ],
  },
  {
    label: "Automation",
    items: [
      { id: "policies", label: "Policies", icon: BellRing },
      { id: "playbooks", label: "Playbooks", icon: BookOpenText },
      { id: "agents", label: "Agent Fleet", icon: Bot },
    ],
  },
  {
    label: "Governance",
    items: [
      { id: "connections", label: "Connections", icon: Network },
      { id: "audits", label: "Audit Log", icon: FileClock },
    ],
  },
]

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-3 text-[1.25rem] font-semibold tracking-[-0.045em] text-[var(--ink)]">
      <span className="grid size-8 place-items-center rounded-[10px] bg-[var(--accent)] text-white shadow-[0_5px_14px_rgba(61,50,111,0.2)]">
        <KeyRound className="size-[17px]" strokeWidth={2.1} />
      </span>
      FireKey
    </div>
  )
}

function SidebarContent({ currentNav, onNavigate }: { currentNav: NavItem; onNavigate: (item: NavItem) => void }) {
  const summary = useQuery({ queryKey: ["overview"], queryFn: () => api.getOverview() })

  return (
    <>
      <div className="px-3 pt-7">
        <Brand />
        <button className="focus-ring mt-7 flex w-full items-center gap-2 rounded-xl border border-white/70 bg-white/45 px-3 py-2.5 text-left transition hover:bg-white/70">
          <span className="grid size-7 place-items-center rounded-lg bg-[#d8d4ec] text-[10px] font-bold text-[var(--accent)]">AC</span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[12px] font-semibold text-[var(--ink)]">Acme Corporation</span>
            <span className="block truncate text-[10px] text-[var(--ink-muted)]">Production workspace</span>
          </span>
          <ChevronDown className="size-3.5 text-[var(--ink-muted)]" />
        </button>
      </div>

      <nav className="mt-6 flex-1 overflow-y-auto px-3 pb-5">
        {sections.map((section, sectionIndex) => (
          <div key={section.label ?? "primary"} className={sectionIndex ? "mt-5" : ""}>
            {section.label && <div className="mb-1.5 px-3 text-[9px] font-semibold uppercase tracking-[0.12em] text-[#a2a0ae]">{section.label}</div>}
            <div className="space-y-0.5">
              {section.items.map((item) => {
                const Icon = item.icon
                const active = currentNav === item.id
                const count = item.id === "approvals" ? summary.data?.pending_approvals : item.id === "incidents" ? summary.data?.open_incidents : item.count
                return (
                  <button
                    key={item.id}
                    className={`focus-ring flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left text-[12.5px] font-medium transition ${
                      active
                        ? "bg-[var(--surface-active)] text-[var(--ink)] shadow-[0_1px_0_rgba(255,255,255,0.8)]"
                        : "text-[#4e4a6a] hover:bg-white/45 hover:text-[var(--ink)]"
                    }`}
                    onClick={() => onNavigate(item.id)}
                  >
                    <Icon className={`size-[15px] ${active ? "text-[var(--accent)]" : "text-[#645c8b]"}`} strokeWidth={1.85} />
                    <span className="flex-1">{item.label}</span>
                    {count ? (
                      <span className="grid min-w-5 place-items-center rounded-full bg-[#ded9f0] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--accent)]">
                        {count}
                      </span>
                    ) : active ? (
                      <ChevronRight className="size-3 text-[var(--ink-muted)]" />
                    ) : null}
                  </button>
                )
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-[#d8d8e0] px-3 py-4">
        <div className="space-y-0.5">
          <FooterItem icon={Settings2} label="Settings" active={currentNav === "settings"} onClick={() => onNavigate("settings")} />
          <FooterItem icon={CircleHelp} label="Help" active={currentNav === "help"} onClick={() => onNavigate("help")} />
        </div>
        <button className="focus-ring mt-3 flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-white/45">
          <span className="grid size-8 place-items-center rounded-[10px] bg-[var(--accent)] text-[10px] font-semibold text-white">CO</span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[12px] font-semibold">Chigozie Okafor</span>
            <span className="block truncate text-[10px] text-[var(--ink-muted)]">Security administrator</span>
          </span>
          <ChevronDown className="size-3.5 text-[var(--ink-muted)]" />
        </button>
      </div>
    </>
  )
}

function FooterItem({ icon: Icon, label, active, onClick }: { icon: LucideIcon; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      className={`focus-ring flex w-full items-center gap-3 rounded-xl px-3 py-2 text-[12px] font-medium transition ${
        active ? "bg-white/75 text-[var(--ink)]" : "text-[#4e4a6a] hover:bg-white/45"
      }`}
      onClick={onClick}
    >
      <Icon className="size-[15px] text-[#645c8b]" strokeWidth={1.85} />
      {label}
    </button>
  )
}

export function Shell({ currentNav, onNavigate, children }: { currentNav: NavItem; onNavigate: (item: NavItem) => void; children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const contentRef = useRef<HTMLElement>(null)

  useEffect(() => {
    contentRef.current?.scrollTo({ top: 0, behavior: "instant" })
  }, [currentNav])

  return (
    <div className="app-shell">
      <aside className="hidden w-[240px] shrink-0 flex-col bg-[var(--sidebar)] lg:flex">
        <SidebarContent currentNav={currentNav} onNavigate={onNavigate} />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex h-14 items-center justify-between border-b border-[var(--border-soft)] bg-[var(--workspace)] px-6 lg:hidden">
          <Brand />
          <button className="focus-ring rounded-xl p-2 text-[var(--ink-soft)] hover:bg-white/50" onClick={() => setMobileOpen(true)}>
            <Menu className="size-5" />
          </button>
        </header>

        <main ref={contentRef} className="flex-1 overflow-y-auto">
          {children}
        </main>
      </div>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          <div className="fixed inset-0 bg-[#17152f]/25 backdrop-blur-sm" onClick={() => setMobileOpen(false)} />
          <div className="relative z-10 flex w-[280px] flex-col bg-[var(--sidebar)]">
            <div className="flex h-14 items-center justify-end px-4">
              <button className="focus-ring rounded-xl p-2 text-[var(--ink-soft)] hover:bg-white/50" onClick={() => setMobileOpen(false)}>
                <X className="size-5" />
              </button>
            </div>
            <SidebarContent currentNav={currentNav} onNavigate={(item) => { onNavigate(item); setMobileOpen(false); }} />
          </div>
        </div>
      )}
    </div>
  )
}
