import type { IntegrationKind } from "../components/integration"

export function connectionCallbackIntegration(): IntegrationKind | null {
  const parameters = new URLSearchParams(window.location.search)
  if (parameters.has("google_cloud")) return "google-cloud"
  if (parameters.has("github") || parameters.has("installation_id")) return "github"
  if (parameters.has("code") && sessionStorage.getItem("uumi.github")) return "github"
  return null
}

export function dashboardLocation(): string {
  return connectionCallbackIntegration() ? `/dashboard${window.location.search}` : "/dashboard"
}
