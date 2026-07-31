export interface RegionDraftState {
  bbox: number[]
  regionType: string
  confirmed: boolean
  note: string
}

export type TaskRecoveryAction = 'keep' | 'clear_job' | 'clear_document' | 'reauthenticate'

export function regionDraftSignature(draft: RegionDraftState): string {
  const bbox: number[] = draft.bbox.map((value: number) => Math.round(value))
  return JSON.stringify({
    bbox: bbox,
    regionType: draft.regionType.trim(),
    confirmed: draft.confirmed,
    note: draft.note.trim()
  })
}

export function isActiveTaskStatus(status: string): boolean {
  return status === 'restoring' || status === 'queued' || status === 'running' ||
    status === 'pausing' || status === 'paused' || status === 'network_wait' ||
    status === 'auth_required' || status === 'restore_wait'
}

export function taskRecoveryAction(statusCode: number, documentExists: boolean): TaskRecoveryAction {
  if (statusCode === 401) return 'reauthenticate'
  if (statusCode === 404) return documentExists ? 'clear_job' : 'clear_document'
  return 'keep'
}
