export function titleCase(value: string): string {
  return value.replaceAll("-", " ").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function formatDate(value: string, withTime = false): string {
  return new Intl.DateTimeFormat("en", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(new Date(value))
}

export function shortId(value: string | null, size = 14): string {
  if (!value) return "—"
  return value.length > size ? `${value.slice(0, size)}…` : value
}

export function providerName(value: string): string {
  const names: Record<string, string> = {
    "cloud-run": "Cloud Run",
    "cloud-monitoring": "Cloud Monitoring",
    "google-secret-manager": "Secret Manager",
    "internal-vendor": "Internal Vendor",
    github: "GitHub",
    netsuite: "NetSuite",
    segment: "Segment",
    sendgrid: "SendGrid",
    snowflake: "Snowflake",
    stripe: "Stripe",
  }
  return names[value] ?? titleCase(value)
}
