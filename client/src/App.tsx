import { useState } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Shell, type NavItem } from "./components/sidebar"
import { OverviewPage } from "./pages/overview"
import { CredentialsPage } from "./pages/credentials"
import { PlaybooksPage } from "./pages/playbooks"
import { IncidentsPage } from "./pages/incidents"
import { RotationsPage } from "./pages/rotations"
import { ApprovalsPage } from "./pages/approvals"
import { ConnectionsPage } from "./pages/connections"
import { connectionCallbackIntegration } from "./lib/callback"
import { AuditsPage } from "./pages/audits"
import { SettingsPage } from "./pages/settings"
import { BrowserSetupPage } from "./pages/browsersetup"
import { Button } from "./components/ui/button"
import { api } from "./lib/api"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

export function App() {
  const [currentNav, setCurrentNav] = useState<NavItem>(() => connectionCallbackIntegration() ? "connections" : "overview")
  const [activeRunId, setActiveRunId] = useState<string>("")
  const [activeIncidentId, setActiveIncidentId] = useState("")
  const [activeApprovalId, setActiveApprovalId] = useState("")
  const [activeCredential, setActiveCredential] = useState<{ id: string; controlVersionId: string } | null>(null)
  const [activeConnectionId, setActiveConnectionId] = useState("")

  const handleNavigate = (target: NavItem) => {
    setActiveRunId("")
    setActiveIncidentId("")
    setActiveApprovalId("")
    setActiveCredential(null)
    setActiveConnectionId("")
    setCurrentNav(target)
  }

  const handleNavigateRotation = (runId: string) => {
    setActiveRunId(runId)
    setActiveIncidentId("")
    setActiveApprovalId("")
    setCurrentNav("rotations")
  }

  const handleNavigateIncident = (incidentId: string) => {
    setActiveRunId("")
    setActiveIncidentId(incidentId)
    setActiveApprovalId("")
    setCurrentNav("incidents")
  }

  const handleNavigateApproval = (approvalId: string) => {
    setActiveRunId("")
    setActiveIncidentId("")
    setActiveApprovalId(approvalId)
    setCurrentNav("approvals")
  }

  const handleNavigateControls = (credentialId: string, controlVersionId: string) => {
    setActiveRunId("")
    setActiveCredential({ id: credentialId, controlVersionId })
    setCurrentNav("credentials")
  }

  const handleNavigateConnection = (connectionId: string) => {
    setActiveRunId("")
    setActiveCredential(null)
    setActiveConnectionId(connectionId)
    setCurrentNav("connections")
  }

  const handleLogout = async () => {
    try {
      await api.logout()
    } finally {
      queryClient.clear()
      window.location.assign("/signed-out")
    }
  }

  if (window.location.pathname === "/browser/setup") {
    return <QueryClientProvider client={queryClient}><BrowserSetupPage /></QueryClientProvider>
  }
  if (window.location.pathname === "/signed-out") return <SignedOutPage />

  const renderContent = () => {
    switch (currentNav) {
      case "overview":
        return <OverviewPage onNavigate={handleNavigate} onNavigateRotation={handleNavigateRotation} onNavigateIncident={handleNavigateIncident} onNavigateApproval={handleNavigateApproval} />
      case "credentials":
        return <CredentialsPage initialCredentialId={activeCredential?.id} initialControlVersionId={activeCredential?.controlVersionId} onNavigate={handleNavigate} onNavigateRotation={handleNavigateRotation} />
      case "playbooks":
        return <PlaybooksPage />
      case "incidents":
        return <IncidentsPage initialIncidentId={activeIncidentId} onNavigateRotation={handleNavigateRotation} />
      case "rotations":
        return <RotationsPage activeRunId={activeRunId} onNavigateApproval={() => setCurrentNav("approvals")} onNavigateControls={handleNavigateControls} onNavigateConnection={handleNavigateConnection} />
      case "approvals":
        return <ApprovalsPage initialApprovalId={activeApprovalId} onNavigateRotation={handleNavigateRotation} />
      case "connections":
        return <ConnectionsPage initialConnectionId={activeConnectionId} />
      case "audits":
        return <AuditsPage />
      case "settings":
        return <SettingsPage />
      default:
        return <OverviewPage onNavigate={handleNavigate} onNavigateRotation={handleNavigateRotation} onNavigateIncident={handleNavigateIncident} onNavigateApproval={handleNavigateApproval} />
    }
  }

  return (
    <QueryClientProvider client={queryClient}>
      <Shell currentNav={currentNav} onNavigate={handleNavigate} onLogout={() => { void handleLogout() }}>
        {renderContent()}
      </Shell>
    </QueryClientProvider>
  )
}

function SignedOutPage() {
  const signIn = import.meta.env.VITE_SIGN_IN_URL ?? "/"
  return <main className="grid min-h-screen place-items-center bg-[var(--workspace)] px-6"><div className="text-center"><h1 className="text-2xl font-semibold tracking-[-0.04em] text-[var(--ink)]">Signed out</h1><Button className="mt-6" onClick={() => window.location.assign(signIn)}>Sign in</Button></div></main>
}

export default App
