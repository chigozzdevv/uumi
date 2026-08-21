import { useEffect, useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  ChevronDown,
  House,
  KeyRound,
  Menu,
  ScrollText,
  ShieldCheck,
  SlidersHorizontal,
  Workflow,
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
  | "playbooks"
  | "connections"
  | "audits"
  | "settings"
  | "help"

interface NavEntry {
  id: NavItem
  label: string
  count?: number
}

interface NavGroup {
  id: "inventory" | "operations" | "management"
  label: string
  icon: LucideIcon
  items: NavEntry[]
}

const groups: NavGroup[] = [
  {
    id: "inventory",
    label: "Inventory",
    icon: KeyRound,
    items: [
      { id: "credentials", label: "Credentials" },
      { id: "applications", label: "Applications" },
    ],
  },
  {
    id: "operations",
    label: "Operations",
    icon: Workflow,
    items: [
      { id: "incidents", label: "Incidents" },
      { id: "rotations", label: "Rotations" },
      { id: "approvals", label: "Approvals", count: 2 },
    ],
  },
  {
    id: "management",
    label: "Management",
    icon: ShieldCheck,
    items: [
      { id: "connections", label: "Connections" },
      { id: "playbooks", label: "Playbooks" },
    ],
  },
]

function Brand() {
  return (
    <div className="flex items-center gap-3 px-3 text-[1.35rem] font-semibold tracking-[-0.045em] text-[var(--ink)]">
      <span className="grid size-10 place-items-center rounded-xl bg-[var(--accent)] text-white shadow-[0_7px_18px_rgba(25,27,30,0.12)]">
        <KeyRound className="size-5" strokeWidth={2} />
      </span>
      FireKey
    </div>
  )
}

function SidebarContent({ currentNav, onNavigate }: { currentNav: NavItem; onNavigate: (item: NavItem) => void }) {
  const summary = useQuery({ queryKey: ["overview"], queryFn: () => api.getOverview() })
  const [expanded, setExpanded] = useState<Record<NavGroup["id"], boolean>>({
    inventory: true,
    operations: true,
    management: true,
  })

  return (
    <>
      <div className="px-4 pt-8">
        <Brand />
      </div>

      <nav className="mt-9 flex-1 overflow-y-auto px-4 pb-5">
        <PrimaryItem icon={House} label="Overview" active={currentNav === "overview"} onClick={() => onNavigate("overview")} />

        <div className="mt-4 space-y-3">
          {groups.map((group) => {
            const open = expanded[group.id]
            const groupActive = group.items.some((item) => item.id === currentNav)

            return (
              <div key={group.id}>
                <button
                  className={`focus-ring flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-[13px] font-medium transition hover:bg-white/35 ${
                    groupActive ? "text-[var(--ink)]" : "text-[var(--ink-soft)]"
                  }`}
                  aria-expanded={open}
                  onClick={() => setExpanded((current) => ({ ...current, [group.id]: !current[group.id] }))}
                  onPointerUp={(event) => event.currentTarget.blur()}
                >
                  <group.icon className="size-[17px] text-[var(--accent)]" strokeWidth={1.8} />
                  <span className="flex-1">{group.label}</span>
                  <ChevronDown className={`size-3.5 text-[var(--accent)] transition-transform ${open ? "" : "-rotate-90"}`} strokeWidth={2} />
                </button>

                {open && (
                  <div className="nav-children mt-0.5 space-y-0.5">
                    {group.items.map((item) => {
                      const active = item.id === currentNav
                      const count = item.id === "approvals" ? summary.data?.pending_approvals : item.count

                      return (
                        <button
                          key={item.id}
                          className={`focus-ring flex w-full items-center rounded-lg px-3 py-2 text-left text-[13px] font-medium transition ${
                            active
                              ? "bg-[var(--surface-active)] text-[var(--ink)]"
                              : "text-[var(--ink-soft)] hover:bg-white/45 hover:text-[var(--ink)]"
                          }`}
                          onClick={() => onNavigate(item.id)}
                          onPointerUp={(event) => event.currentTarget.blur()}
                        >
                          <span className="flex-1">{item.label}</span>
                          {count ? (
                            <span className="grid min-w-5 place-items-center rounded-lg bg-white px-1.5 py-0.5 text-[10px] font-semibold text-[var(--ink-soft)]">
                              {count}
                            </span>
                          ) : null}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        <div className="mt-4">
          <PrimaryItem icon={ScrollText} label="Audits" active={currentNav === "audits"} onClick={() => onNavigate("audits")} />
        </div>
      </nav>

      <div className="px-4 pb-5">
        <PrimaryItem icon={SlidersHorizontal} label="Settings" active={currentNav === "settings"} onClick={() => onNavigate("settings")} />
        <button className="focus-ring mt-3 flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition hover:bg-white/35">
          <span className="grid size-8 place-items-center rounded-lg bg-[var(--surface-active)] text-[10px] font-semibold text-[var(--accent)]">CO</span>
          <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-[var(--ink)]">Chigozie Okafor</span>
          <ChevronDown className="size-3.5 text-[var(--accent)]" />
        </button>
      </div>
    </>
  )
}

function PrimaryItem({ icon: Icon, label, active, onClick }: { icon: LucideIcon; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      className={`focus-ring flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-[13px] font-medium transition ${
        active ? "bg-[var(--surface-active)] text-[var(--ink)]" : "text-[var(--ink-soft)] hover:bg-white/45 hover:text-[var(--ink)]"
      }`}
      onClick={onClick}
      onPointerUp={(event) => event.currentTarget.blur()}
    >
      <Icon className="size-[17px] text-[var(--accent)]" strokeWidth={1.8} />
      <span className="flex-1">{label}</span>
    </button>
  )
}

export function Shell({ currentNav, onNavigate, children }: { currentNav: NavItem; onNavigate: (item: NavItem) => void; children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" })
  }, [currentNav])

  return (
    <div className="app-shell">
      <aside className="sticky top-6 hidden h-[calc(100vh-48px)] w-[240px] shrink-0 flex-col overflow-hidden rounded-2xl bg-[var(--sidebar)] lg:flex">
        <SidebarContent currentNav={currentNav} onNavigate={onNavigate} />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col overflow-x-hidden">
        <header className="flex h-14 items-center justify-between bg-[var(--workspace)] px-6 lg:hidden">
          <Brand />
          <button aria-label="Open navigation" className="focus-ring rounded-lg p-2 text-[var(--ink-soft)] hover:bg-white/50" onClick={() => setMobileOpen(true)}>
            <Menu className="size-5" />
          </button>
        </header>

        <main className="flex-1">
          {children}
        </main>
      </div>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          <div className="fixed inset-0 bg-black/20 backdrop-blur-sm" onClick={() => setMobileOpen(false)} />
          <div className="relative z-10 flex w-[280px] flex-col bg-[var(--sidebar)]">
            <div className="flex h-14 items-center justify-end px-4">
              <button aria-label="Close navigation" className="focus-ring rounded-lg p-2 text-[var(--ink-soft)] hover:bg-white/50" onClick={() => setMobileOpen(false)}>
                <X className="size-5" />
              </button>
            </div>
            <SidebarContent currentNav={currentNav} onNavigate={(item) => { onNavigate(item); setMobileOpen(false) }} />
          </div>
        </div>
      )}
    </div>
  )
}
