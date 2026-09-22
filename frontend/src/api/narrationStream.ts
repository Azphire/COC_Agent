import type { RoomEvent } from './rooms'

export type NarrationDraft = {
  cycle_id: string; stream_id: string; attempt: number; index: number; text: string
  status: 'responding' | 'waiting' | 'ended' | 'interrupted'; event_seq?: number | null
  reason?: string | null
}
export type StreamMessage = { type: string; data?: Partial<NarrationDraft> & { offset?: number } | null }
export type NarrationStreamState = {
  draft: NarrationDraft | null; closedCycles: string[]; retiredStreams: string[]; needsSync: boolean
}
export const emptyNarrationStream = (): NarrationStreamState => ({ draft: null, closedCycles: [], retiredStreams: [], needsSync: false })
const remember = (items: string[], value: string) => [...items.filter(item => item !== value), value].slice(-128)

export function acceptNarrationEvent(state: NarrationStreamState, event: RoomEvent): NarrationStreamState {
  if (event.type !== 'keeper.narration' || typeof event.payload.cycle_id !== 'string') return state
  const cycle = event.payload.cycle_id
  return { ...state, closedCycles: remember(state.closedCycles, cycle),
    draft: state.draft?.cycle_id === cycle ? null : state.draft, needsSync: false }
}

export function reduceNarrationStream(state: NarrationStreamState, message: StreamMessage): NarrationStreamState {
  if (message.type === 'connection.lost') return { ...state, needsSync: false,
    draft: state.draft && !['ended', 'interrupted'].includes(state.draft.status)
      ? { ...state.draft, status: 'waiting', reason: 'disconnected' } : state.draft }
  if (!message.type.startsWith('keeper.stream.')) return state
  const kind = message.type.slice('keeper.stream.'.length)
  if (kind === 'snapshot' && !message.data) return { ...state, needsSync: false,
    retiredStreams: state.draft ? remember(state.retiredStreams, state.draft.stream_id) : state.retiredStreams,
    draft: !state.draft ? null : state.draft.status === 'interrupted' ? state.draft
      : { ...state.draft, text: '', status: 'interrupted', reason: 'buffer_unavailable' } }
  const data = message.data
  if (!data || typeof data.cycle_id !== 'string' || typeof data.stream_id !== 'string'
    || !Number.isSafeInteger(data.attempt) || !Number.isSafeInteger(data.index)
    || data.attempt! < 1 || data.index! < 0) return state
  if (state.closedCycles.includes(data.cycle_id) || state.retiredStreams.includes(data.stream_id)) return state
  if (['interrupt', 'snapshot'].includes(kind) && data.reason === 'complete_without_narration') {
    return { ...state, draft: state.draft?.cycle_id === data.cycle_id ? null : state.draft,
      closedCycles: remember(state.closedCycles, data.cycle_id), needsSync: false }
  }
  let draft = state.draft
  // A resumed model request has a new stream ID and starts its retry budget at 1.
  // Attempt ordering applies only within that stream, not across the whole cycle.
  if (draft && data.cycle_id === draft.cycle_id && data.stream_id === draft.stream_id
    && data.attempt! < draft.attempt) return state
  const same = draft?.stream_id === data.stream_id && draft.cycle_id === data.cycle_id && draft.attempt === data.attempt
  if (same && data.index! < draft!.index) return state
  if (same && data.index === draft!.index && kind !== 'snapshot') return state
  if (same && ['ended', 'interrupted'].includes(draft!.status) && kind !== 'snapshot') return state
  if (!same && !['start', 'snapshot'].includes(kind)) return { ...state, needsSync: true }
  const retiredStreams = !same && draft && draft.stream_id !== data.stream_id
    ? remember(state.retiredStreams, draft.stream_id) : state.retiredStreams
  if (!same) draft = { cycle_id: data.cycle_id, stream_id: data.stream_id, attempt: data.attempt!, index: 0, text: '', status: 'responding' }
  if (kind === 'delta') {
    const incoming = typeof data.text === 'string' ? Array.from(data.text) : []
    // Server offsets count Unicode code points (including astral characters).
    const accepted = Array.from(draft!.text)
    if (!Number.isSafeInteger(data.offset) || data.offset! < 0 || data.offset! > accepted.length) {
      return { ...state, draft: { ...draft!, status: 'waiting', reason: 'gap' }, needsSync: true }
    }
    const overlap = Math.min(accepted.length - data.offset!, incoming.length)
    if (accepted.slice(data.offset!, data.offset! + overlap).join('') !== incoming.slice(0, overlap).join('')) {
      return { ...state, draft: { ...draft!, status: 'waiting', reason: 'gap' }, needsSync: true }
    }
    draft = { ...draft!, index: data.index!, text: draft!.text + incoming.slice(overlap).join(''), status: 'responding', reason: null }
  } else if (['start', 'replace', 'snapshot'].includes(kind)) {
    draft = { ...draft!, index: data.index!, text: typeof data.text === 'string' ? data.text : '',
      status: data.status || 'responding', event_seq: data.event_seq, reason: data.reason }
  } else if (kind === 'end' || kind === 'interrupt') {
    draft = { ...draft!, index: data.index!, status: kind === 'end' ? 'ended' : 'interrupted',
      text: kind === 'interrupt' ? '' : draft!.text, event_seq: data.event_seq, reason: data.reason }
  } else return state
  return { ...state, draft, retiredStreams, needsSync: false }
}

export function narrationDraftLabel(draft: NarrationDraft): string {
  if (draft.reason === 'buffer_unavailable') return 'KP 回应缓存已失效，等待正式结果…'
  if (draft.status === 'interrupted') return 'KP 回应已中断'
  if (draft.status === 'ended') return 'KP 回应已完成，正在同步…'
  if (draft.reason === 'disconnected' || draft.reason === 'gap') return '连接中断，等待恢复 KP 回应…'
  if (draft.status === 'waiting') return 'KP 正在回应，等待完整结果…'
  return 'KP正在回应…'
}
