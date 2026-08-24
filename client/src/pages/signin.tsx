import { useState } from "react"
import googleIcon from "@iconify-icons/logos/google-icon.js"
import { Icon } from "@iconify/react"
import fireKeyLogo from "../assets/firekey-logo.png"
import { Button } from "../components/ui/button"
import { authenticationConfigured, signInWithGoogle } from "../lib/auth"

export function SignInPage() {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  const handleSignIn = async () => {
    setError("")
    setSubmitting(true)
    try {
      await signInWithGoogle()
      window.history.replaceState({}, "", "/")
    } catch (reason) {
      const code = typeof reason === "object" && reason !== null && "code" in reason ? String(reason.code) : ""
      if (code !== "auth/popup-closed-by-user" && code !== "auth/cancelled-popup-request") {
        setError("Sign in could not be completed. Try again.")
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-[var(--workspace)] px-6 py-12">
      <section className="w-full max-w-[420px] rounded-[22px] border border-[var(--border)] bg-white px-8 py-10 shadow-[0_14px_40px_rgba(25,27,30,0.05)] sm:px-10">
        <img className="mx-auto h-auto w-[144px]" src={fireKeyLogo} alt="FireKey" />
        <h1 className="mt-10 text-center text-[28px] font-semibold tracking-[-0.045em] text-[var(--ink)]">Sign in to FireKey</h1>
        <Button
          className="mt-8 h-12 w-full text-[14px]"
          variant="secondary"
          disabled={submitting || !authenticationConfigured}
          onClick={() => { void handleSignIn() }}
        >
          <Icon icon={googleIcon} className="size-5" />
          {submitting ? "Signing in…" : "Continue with Google"}
        </Button>
        {!authenticationConfigured ? <p className="mt-4 text-center text-[12px] text-[var(--red)]">Sign-in is not configured.</p> : null}
        {error ? <p className="mt-4 text-center text-[12px] text-[var(--red)]">{error}</p> : null}
      </section>
    </main>
  )
}
