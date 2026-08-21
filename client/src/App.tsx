import { useState } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { Shell, type NavItem } from "./components/sidebar"
import { OverviewPage } from "./pages/overview"
import { CredentialsPage } from "./pages/credentials"
import { ApplicationsPage } from "./pages/applications"
import { PlaybooksPage } from "./pages/playbooks"
import { IncidentsPage } from "./pages/incidents"
import { RotationsPage } from "./pages/rotations"
import { ApprovalsPage } from "./pages/approvals"
import { ConnectionsPage } from "./pages/connections"
import { AuditsPage } from "./pages/audits"
import { SettingsPage } from "./pages/settings"
import { BrowserSetupPage } from "./pages/browsersetup"

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
  const [currentNav, setCurrentNav] = useState<NavItem>("overview")
  const [activeRunId, setActiveRunId] = useState<string>("")

  const handleNavigateRotation = (runId: string) => {
    setActiveRunId(runId)
    setCurrentNav("rotations")
  }

  if (window.location.pathname === "/browser/setup") {
    return <QueryClientProvider client={queryClient}><BrowserSetupPage /></QueryClientProvider>
  }

  const renderContent = () => {
    switch (currentNav) {
      case "overview":
        return <OverviewPage onNavigate={setCurrentNav} />
      case "credentials":
        return <CredentialsPage onNavigate={setCurrentNav} onNavigateRotation={handleNavigateRotation} />
      case "applications":
        return <ApplicationsPage />
      case "playbooks":
        return <PlaybooksPage />
      case "incidents":
        return <IncidentsPage onNavigateRotation={handleNavigateRotation} />
      case "rotations":
        return <RotationsPage activeRunId={activeRunId} onNavigateApproval={() => setCurrentNav("approvals")} />
      case "approvals":
        return <ApprovalsPage />
      case "connections":
        return <ConnectionsPage />
      case "audits":
        return <AuditsPage />
      case "settings":
        return <SettingsPage section="settings" />
      case "help":
        return <SettingsPage section="help" />
      default:
        return <OverviewPage onNavigate={setCurrentNav} />
    }
  }

  return (
    <QueryClientProvider client={queryClient}>
      <Shell currentNav={currentNav} onNavigate={setCurrentNav}>
        {renderContent()}
      </Shell>
    </QueryClientProvider>
  )
}

export default App
