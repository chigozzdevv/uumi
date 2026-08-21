import type { ControlDefinition, ControlPreferences } from "./api"

export interface ControlValues {
  automaticTriggers: string[]
  expiryDays: number
  observationMinutes: number
}

export const defaultControls: ControlValues = {
  automaticTriggers: ["expiry", "drift", "verified-exposure"],
  expiryDays: 7,
  observationMinutes: 30,
}

export const controlTriggers = [
  ["expiry", "Expiry"],
  ["drift", "Configuration drift"],
  ["verified-exposure", "Verified exposure"],
] as const

export function controlsFromDefinition(definition: ControlDefinition | undefined): ControlValues {
  if (!definition) return defaultControls
  return {
    automaticTriggers: definition.automatic_triggers.filter((trigger) => controlTriggers.some(([value]) => value === trigger)),
    expiryDays: definition.rotate_before_expiry_seconds / 86400,
    observationMinutes: definition.maximum_observation_seconds / 60,
  }
}

export function controlsAreValid(values: ControlValues) {
  return values.automaticTriggers.length > 0 && values.expiryDays >= 1 && values.observationMinutes >= 1
}

export function buildControlPreferences(values: ControlValues): ControlPreferences {
  return {
    automatic_triggers: values.automaticTriggers,
    rotate_before_expiry_seconds: values.expiryDays * 86400,
    maximum_observation_seconds: values.observationMinutes * 60,
  }
}
