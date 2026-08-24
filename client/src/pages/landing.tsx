import uumiLogo from "../assets/uumi-logo.png"
import { Button } from "../components/ui/button"

export function LandingPage({ authenticated }: { authenticated: boolean }) {
  return (
    <main className="min-h-screen bg-[var(--workspace)]">
      <header className="mx-auto flex w-full max-w-[1440px] items-center justify-between px-8 py-7 lg:px-12">
        <img className="h-auto w-[112px]" src={uumiLogo} alt="Uumi" />
        <Button onClick={() => window.location.assign(authenticated ? "/dashboard" : "/auth")}>
          {authenticated ? "Open dashboard" : "Sign in"}
        </Button>
      </header>
    </main>
  )
}
