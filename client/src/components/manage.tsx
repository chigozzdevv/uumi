import { useEffect, useState, type ReactNode } from "react"
import { Button } from "./ui/button"
import { Modal } from "./ui/modal"

export interface ResourceDependency {
  label: string
  items: string[]
}

export function ManageResourceModal({
  isOpen,
  onClose,
  title,
  resourceLabel,
  children,
  onSave,
  onDelete,
  dependencies = [],
  saveDisabled = false,
  saving = false,
  deleting = false,
  error,
}: {
  isOpen: boolean
  onClose: () => void
  title: string
  resourceLabel: string
  children: ReactNode
  onSave: () => void
  onDelete?: () => void
  dependencies?: ResourceDependency[]
  saveDisabled?: boolean
  saving?: boolean
  deleting?: boolean
  error?: string
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const activeDependencies = dependencies.filter((dependency) => dependency.items.length > 0)

  useEffect(() => {
    if (!isOpen) setConfirmingDelete(false)
  }, [isOpen])

  const close = () => {
    if (confirmingDelete) setConfirmingDelete(false)
    else onClose()
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={close}
      title={confirmingDelete ? `Delete ${resourceLabel}?` : title}
      cancelLabel={confirmingDelete ? "Back" : "Cancel"}
      footerStart={!confirmingDelete && onDelete ? <Button variant="ghost" onClick={() => setConfirmingDelete(true)}>Delete</Button> : undefined}
      actions={confirmingDelete
        ? <Button variant="danger" onClick={onDelete} disabled={deleting}>{deleting ? "Deleting…" : "Delete"}</Button>
        : <Button onClick={onSave} disabled={saveDisabled || saving}>{saving ? "Saving…" : "Save changes"}</Button>}
    >
      {!confirmingDelete && children}
      {confirmingDelete && (
        activeDependencies.length > 0
          ? <div className="space-y-4">
              <div className="divide-y divide-[var(--border-soft)] border-y border-[var(--border-soft)]">
                {activeDependencies.map((dependency) => (
                  <div key={dependency.label} className="grid gap-2 py-3 sm:grid-cols-[8rem_1fr]">
                    <div className="text-[9px] font-semibold text-[var(--ink-muted)]">{dependency.label}</div>
                    <div className="text-[10px]">{dependency.items.join(", ")}</div>
                  </div>
                ))}
              </div>
            </div>
          : <p className="text-[10px] text-[var(--ink-soft)]">This removes the {resourceLabel} from FireKey.</p>
      )}
      {error && <div role="alert" className="mt-4 rounded-xl border border-[#ebcfd3] bg-[var(--red-soft)] p-3 text-[10px] text-[var(--red)]">{error}</div>}
    </Modal>
  )
}
