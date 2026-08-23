import { useEffect, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Check } from "lucide-react"
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

  return <main className="grid min-h-screen place-items-center bg-[var(--workspace)] p-5 text-[var(--ink)] sm:p-10">
    <section className="w-full max-w-sm rounded-2xl border border-[var(--border)] bg-white p-7 text-center">
      {complete.isSuccess ? <><span className="mx-auto grid size-12 place-items-center rounded-full bg-[var(--green-soft)] text-[var(--green)]"><Check className="size-5" /></span><h1 className="mt-5 text-[17px] font-semibold tracking-[-0.03em]">Connection ready</h1><Button className="mt-6" onClick={() => window.close()}>Close browser</Button></> : <><h1 className="text-[17px] font-semibold tracking-[-0.03em]">Sign in to provider</h1><Button className="mt-6" onClick={() => complete.mutate()} disabled={!capability.setupId || !capability.token || complete.isPending}>{complete.isPending ? "Continuing…" : "Continue"}</Button>{complete.error && <div className="mt-4 text-[9px] text-[var(--red)]">{complete.error.message}</div>}</>}
    </section>
  </main>
}
