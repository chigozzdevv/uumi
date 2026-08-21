import { controlTriggers, type ControlValues } from "../lib/controls"
import { titleCase } from "../lib/format"
import { Detail, DetailList } from "./detail"
import { Field, SelectControl } from "./workspace"

export function ControlsFields({ value, onChange }: { value: ControlValues; onChange: (value: ControlValues) => void }) {
  return <div className="space-y-6">
    <fieldset>
      <legend className="mb-2 text-[10px] font-semibold text-[var(--ink-soft)]">Automatic rotation</legend>
      <div className="grid gap-2 sm:grid-cols-3">
        {controlTriggers.map(([trigger, label]) => <label key={trigger} className="flex items-center gap-3 rounded-xl border border-[var(--border)] bg-white px-4 py-3 text-[10px] font-medium">
          <input className="accent-[var(--ink)]" type="checkbox" checked={value.automaticTriggers.includes(trigger)} onChange={(event) => onChange({ ...value, automaticTriggers: event.target.checked ? [...value.automaticTriggers, trigger] : value.automaticTriggers.filter((item) => item !== trigger) })} />
          {label}
        </label>)}
      </div>
    </fieldset>
    <div className="grid gap-4 sm:grid-cols-2">
      <Field label="Rotate before expiry"><SelectControl value={String(value.expiryDays)} onChange={(event) => onChange({ ...value, expiryDays: Number(event.target.value) })}><option value="3">3 days</option><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option></SelectControl></Field>
      <Field label="Verify before revoking"><SelectControl value={String(value.observationMinutes)} onChange={(event) => onChange({ ...value, observationMinutes: Number(event.target.value) })}><option value="15">15 minutes</option><option value="30">30 minutes</option><option value="60">1 hour</option><option value="240">4 hours</option></SelectControl></Field>
    </div>
  </div>
}

export function ControlsSummary({ value }: { value: ControlValues }) {
  return <DetailList>
    <Detail label="Automatic rotation">{value.automaticTriggers.map(titleCase).join(", ")}</Detail>
    <Detail label="Expiry lead">{value.expiryDays} days</Detail>
    <Detail label="Verification period">{value.observationMinutes} minutes</Detail>
    <Detail label="Approval">Required before revocation</Detail>
  </DetailList>
}
