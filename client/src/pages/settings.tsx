import { BookOpenText, LifeBuoy, LockKeyhole, ShieldCheck } from "lucide-react"
import { Detail, DetailList, Section } from "../components/detail"
import { PageHeader } from "../components/header"
import { Badge } from "../components/ui/badge"

export function SettingsPage({ section }: { section: "settings" | "help" }) {
  if (section === "help") {
    return <div className="page"><PageHeader section="Help" /><div className="grid gap-4 md:grid-cols-2"><HelpCard icon={BookOpenText} title="Product guide" /><HelpCard icon={LifeBuoy} title="Rotation support" /><HelpCard icon={LockKeyhole} title="Credential safety" /><HelpCard icon={ShieldCheck} title="Browser sessions" /></div></div>
  }

  return (
    <div className="page">
      <PageHeader section="Settings" />
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="panel p-6"><Section title="Operator"><DetailList><Detail label="Email">chigozie@acme.com</Detail><Detail label="Role"><Badge variant="active">Security administrator</Badge></Detail><Detail label="Sign-in">Google</Detail><Detail label="MFA"><Badge variant="healthy">Required</Badge></Detail></DetailList></Section></div>
        <div className="panel p-6"><Section title="Organisation"><DetailList><Detail label="Region">United States (Central)</Detail><Detail label="Residency">Regional</Detail><Detail label="Encryption"><Badge variant="healthy">Enabled</Badge></Detail><Detail label="Audit retention">Locked</Detail></DetailList></Section></div>
      </div>
    </div>
  )
}

function HelpCard({ icon: Icon, title }: { icon: typeof BookOpenText; title: string }) {
  return <div className="panel flex items-center gap-4 p-5"><span className="grid size-9 place-items-center rounded-xl bg-[var(--accent-soft)] text-[var(--accent)]"><Icon className="size-4" /></span><h2 className="text-[13px] font-semibold">{title}</h2></div>
}
