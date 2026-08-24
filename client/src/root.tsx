import { lazy, Suspense, useEffect, useState } from "react"
import type { User } from "firebase/auth"
import fireKeyLogo from "./assets/firekey-logo.png"
import { observeIdentity } from "./lib/auth"
import { SignInPage } from "./pages/signin"

const Dashboard = lazy(() => import("./App.tsx"))

function LoadingScreen() {
  return <main className="grid min-h-screen place-items-center bg-[var(--workspace)]"><img className="h-auto w-[132px]" src={fireKeyLogo} alt="FireKey" /></main>
}

export function AuthenticationBoundary() {
  const [identity, setIdentity] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => observeIdentity((user) => {
    setIdentity(user)
    setLoading(false)
  }), [])

  useEffect(() => {
    if (identity && window.location.pathname === "/sign-in") {
      window.history.replaceState({}, "", "/")
    }
  }, [identity])

  if (loading) return <LoadingScreen />
  if (!identity) return <SignInPage />
  return <Suspense fallback={<LoadingScreen />}><Dashboard /></Suspense>
}
