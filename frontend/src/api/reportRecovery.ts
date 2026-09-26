import type { ReportRecovery } from './agents'

type RequestStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
const requestKey = (roomId: string, reportSeq: number) => `coc.report-supplement.${roomId}.${reportSeq}`

// Keep an unacknowledged submission stable across network errors and refreshes.
// A changed server supplement ID proves the prior submission was accepted.
export function supplementRequestId(storage: RequestStorage, roomId: string, recovery: ReportRecovery, createId: () => string): string {
  const key = requestKey(roomId, recovery.report_seq)
  const priorId = recovery.supplement_id || null
  try {
    const pending = JSON.parse(storage.getItem(key) || 'null')
    if (pending?.previous_supplement_id === priorId && typeof pending.client_request_id === 'string') return pending.client_request_id
  } catch { /* An invalid local entry does not authorize or settle a report. */ }
  const clientRequestId = createId()
  storage.setItem(key, JSON.stringify({ client_request_id: clientRequestId, previous_supplement_id: priorId }))
  return clientRequestId
}

export function acknowledgeSupplement(storage: RequestStorage, roomId: string, reportSeq: number) {
  storage.removeItem(requestKey(roomId, reportSeq))
}
