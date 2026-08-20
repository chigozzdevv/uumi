import { useEffect, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Check, LockKeyhole, ShieldCheck } from "lucide-react"
import { api } from "../lib/api"
import { Button } from "../components/ui/button"

export function BrowserSetupPage() {
  const [capability] = useState(() => {
    const fragment = new URLSearchParams(window.location.hash.slice(1))
    return { setupId: fragment.get("setup_id") ?? "", token: fragment.get("token") ?? "" }
  })
  const complete = useMutation({ mutationFn: () => api.completeBrowserSetup(capability.setupId, 0, capability.token) })

  useEffect(() => {
    window.history.replaceState({}, "", window.location.pathname)
  }, [])

  return <main className="min-h-screen bg-[var(--workspace)] p-5 text-[var(--ink)] sm:p-10">
    <div className="mx-auto max-w-5xl overflow-hidden rounded-[22px] border border-[var(--border)] bg-white shadow-[0_30px_90px_rgba(23,21,47,0.12)]">
      <header className="flex items-center justify-between border-b border-[var(--border)] px-6 py-4"><div className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-xl bg-[var(--accent-soft)] text-[var(--accent)]"><LockKeyhole className="size-4" /></span><div><div className="text-[12px] font-semibold">Provider authentication</div><div className="mt-0.5 text-[9px] text-[var(--ink-muted)]">Isolated FireKey browser</div></div></div><span className="flex items-center gap-1.5 text-[9px] font-semibold text-[var(--green)]"><ShieldCheck className="size-3.5" /> Recording paused for authentication</span></header>
      <div className="grid min-h-[520px] place-items-center bg-[#f7f7f5] p-8">
        <section className="w-full max-w-md rounded-2xl border border-[var(--border)] bg-white p-7 text-center">
          {complete.isSuccess ? <><span className="mx-auto grid size-12 place-items-center rounded-full bg-[var(--green-soft)] text-[var(--green)]"><Check className="size-5" /></span><h1 className="mt-5 text-[17px] font-semibold tracking-[-0.03em]">Connection ready</h1><p className="mt-2 text-[10px] leading-5 text-[var(--ink-soft)]">The browser session was stored by the isolated worker. FireKey retained only its reference and metadata.</p><Button className="mt-6" onClick={() => window.close()}>Close browser</Button></> : <><h1 className="text-[17px] font-semibold tracking-[-0.03em]">Sign in to the provider</h1><p className="mt-3 text-[10px] leading-5 text-[var(--ink-soft)]">In production, the provider page is streamed here from an isolated worker. Passwords, cookies, and MFA never enter the dashboard or agent context.</p><div className="mt-6 rounded-xl bg-[var(--surface-soft)] p-4 text-left text-[9px] leading-5 text-[var(--ink-muted)]">Mock session: complete the provider login and any MFA in this window, then continue.</div><Button className="mt-6" onClick={() => complete.mutate()} disabled={!capability.setupId || !capability.token || complete.isPending}>{complete.isPending ? "Finishing…" : "Finish connection"}</Button>{complete.error && <div className="mt-4 text-[9px] text-[var(--red)]">{complete.error.message}</div>}</>}
        </section>
      </div>
    </div>
  </main>
}
