import { BookOpenText, Database, LifeBuoy, LockKeyhole, MapPin, ShieldCheck, UserRound } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Badge } from "../components/ui/badge"

export function SettingsPage({ section }: { section: "settings" | "help" }) {
  if (section === "help") {
    return <div className="page"><PageHeader section="Help" title="Help & architecture" description="Short operational guidance for the people who review incidents, runs, and protected actions." /><div className="grid gap-4 md:grid-cols-2"><HelpCard icon={BookOpenText} title="Product contract" copy="firekey.md is the source of truth for navigation, lifecycle, safety boundaries, and acceptance criteria." /><HelpCard icon={LifeBuoy} title="Run recovery" copy="Failed stages move through approved recovery branches. A compensated run never presents itself as a successful rotation." /><HelpCard icon={LockKeyhole} title="Credential handling" copy="Do not paste workload credentials into inventory, incident notes, approvals, or support requests." /><HelpCard icon={ShieldCheck} title="Computer Use" copy="Human takeover and Secure Capture stay behind deterministic policy, selector, domain, and replay controls." /></div></div>
  }

  return (
    <div className="page">
      <PageHeader section="Settings" title="Organisation settings" description="Identity, regional controls, and the operating boundary for this FireKey organisation." />
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="panel p-6"><Section title="Active operator"><DetailList><Detail label="Identity">chigozie@acme.com</Detail><Detail label="Role"><Badge variant="active">Security administrator</Badge></Detail><Detail label="Provider">Google Identity Platform</Detail><Detail label="MFA"><Badge variant="healthy">Required</Badge></Detail></DetailList></Section><div className="mt-5 flex items-center gap-2 text-[10px] text-[var(--ink-muted)]"><UserRound className="size-3.5" /> Sessions are enforced by IAP and organisation grants.</div></div>
        <div className="panel p-6"><Section title="Data boundary"><DetailList><Detail label="Primary region">us-central1</Detail><Detail label="Operational state">Regional Firestore</Detail><Detail label="Secret store">Google Secret Manager</Detail><Detail label="Encryption"><Badge variant="healthy">CMEK enforced</Badge></Detail><Detail label="Audit retention">Locked regional bucket</Detail></DetailList></Section><div className="mt-5 flex items-center gap-2 text-[10px] text-[var(--ink-muted)]"><MapPin className="size-3.5" /> Runtime, evidence, and session metadata stay within policy.</div></div>
        <div className="panel p-6 lg:col-span-2"><div className="mb-4 flex items-center gap-2"><Database className="size-4 text-[var(--accent)]" /><div className="text-[12px] font-semibold">Authority model</div></div><p className="max-w-3xl text-[10px] leading-5 text-[var(--ink-soft)]">Cloud Workflows owns lifecycle transitions, retries, locks, idempotency, approval pauses, and completion. Agents propose grounded decisions through typed tools; they do not own authoritative workflow state or receive plaintext credential material.</p></div>
      </div>
    </div>
  )
}

function HelpCard({ icon: Icon, title, copy }: { icon: typeof BookOpenText; title: string; copy: string }) {
  return <div className="panel p-6"><span className="grid size-9 place-items-center rounded-xl bg-[var(--accent-soft)] text-[var(--accent)]"><Icon className="size-4" /></span><h2 className="mt-5 text-[13px] font-semibold">{title}</h2><p className="mt-2 text-[10px] leading-5 text-[var(--ink-soft)]">{copy}</p></div>
}
