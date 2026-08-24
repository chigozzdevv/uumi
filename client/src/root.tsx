import { lazy, Suspense, useEffect, useState } from "react"
import type { User } from "firebase/auth"
import uumiLogo from "./assets/uumi-logo.png"
import { observeIdentity } from "./lib/auth"
import { activateOrganisation, storedOrganisation } from "./lib/organisationstate"
import { api } from "./lib/api"
import type { AccountSession, OrganisationMembership } from "./types"
import { Button } from "./components/ui/button"
import { OrganisationSetupPage } from "./pages/organisation"
import { SignInPage } from "./pages/signin"
import { LandingPage } from "./pages/landing"
import { connectionCallbackIntegration, dashboardLocation } from "./lib/callback"

const Dashboard = lazy(() => import("./App.tsx"))

function LoadingScreen() {
  return <main className="grid min-h-screen place-items-center bg-[var(--workspace)]"><img className="h-auto w-[132px]" src={uumiLogo} alt="Uumi" /></main>
}

export function AuthenticationBoundary() {
  const [identity, setIdentity] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [session, setSession] = useState<AccountSession | null>(null)
  const [active, setActive] = useState<OrganisationMembership | null>(null)
  const [sessionError, setSessionError] = useState("")

  useEffect(() => observeIdentity((user) => {
    setIdentity(user)
    setLoading(false)
  }), [])

  useEffect(() => {
    if (!identity || window.location.pathname === "/") {
      setSession(null)
      setActive(null)
      setSessionError("")
      return
    }
    let current = true
    setSessionError("")
    void api.getSession().then((value) => {
      if (!current) return
      const membership = storedOrganisation(value.organisations)
      if (membership) activateOrganisation(membership)
      setSession(value)
      setActive(membership)
    }).catch((reason: unknown) => {
      if (current) setSessionError(reason instanceof Error ? reason.message : "Account could not be loaded")
    })
    return () => { current = false }
  }, [identity])

  if (loading) return <LoadingScreen />
  if (window.location.pathname === "/" && connectionCallbackIntegration()) {
    window.location.replace(dashboardLocation())
    return <LoadingScreen />
  }
  if (window.location.pathname === "/") return <LandingPage authenticated={Boolean(identity)} />
  if (window.location.pathname === "/auth") {
    if (identity) {
      window.location.replace(dashboardLocation())
      return <LoadingScreen />
    }
    return <SignInPage />
  }
  if (!identity) {
    window.location.replace(`/auth${window.location.search}`)
    return <LoadingScreen />
  }
  if (sessionError) return <main className="grid min-h-screen place-items-center bg-[var(--workspace)] px-6"><div className="text-center"><p className="text-[12px] text-[var(--red)]">{sessionError}</p><Button className="mt-4" onClick={() => window.location.reload()}>Try again</Button></div></main>
  if (!session) return <LoadingScreen />
  if (!active) return <OrganisationSetupPage onCreated={(membership) => {
    activateOrganisation(membership)
    setSession({ organisations: [membership] })
    setActive(membership)
  }} />
  return <Suspense fallback={<LoadingScreen />}><Dashboard activeOrganisation={active} memberships={session.organisations} /></Suspense>
}
