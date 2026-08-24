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
import { signOutIdentity } from "./lib/auth"
import { OrganisationProvider } from "./lib/organisation"
import { clearOrganisation } from "./lib/organisationstate"
import type { OrganisationMembership } from "./types"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

export function App({
  activeOrganisation,
  memberships,
}: {
  activeOrganisation: OrganisationMembership
  memberships: OrganisationMembership[]
}) {
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
      await signOutIdentity()
    } finally {
      clearOrganisation()
      queryClient.clear()
      window.location.assign("/auth")
    }
  }

  if (window.location.pathname === "/browser/setup") {
    return <OrganisationProvider active={activeOrganisation} memberships={memberships}><QueryClientProvider client={queryClient}><BrowserSetupPage /></QueryClientProvider></OrganisationProvider>
  }
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
    <OrganisationProvider active={activeOrganisation} memberships={memberships}>
      <QueryClientProvider client={queryClient}>
        <Shell currentNav={currentNav} onNavigate={handleNavigate} onLogout={() => { void handleLogout() }}>
          {renderContent()}
        </Shell>
      </QueryClientProvider>
    </OrganisationProvider>
  )
}

export default App
