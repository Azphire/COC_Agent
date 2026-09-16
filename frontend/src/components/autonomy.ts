import type { Runtime } from '../api/rooms'

export function autonomyReason(runtime?: Runtime): string | null {
  if (!runtime) return null
  if (runtime.san === 0) return 'SAN 为 0：永久疯狂，不能自主行动'
  if (runtime.sanity.kind === 'permanent') return '永久疯狂，不能自主行动；更正 SAN 不会解除永久疯狂'
  if (runtime.sanity.phase === 'awaiting_symptom') return '等待主机确认疯狂症状，不能自主行动'
  if (runtime.sanity.phase === 'bout') return '疯狂发作中，不能自主行动'
  return null
}
