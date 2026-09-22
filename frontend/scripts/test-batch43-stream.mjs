import assert from 'node:assert/strict'
import { test } from 'node:test'
import { acceptNarrationEvent, emptyNarrationStream, reduceNarrationStream } from '../src/api/narrationStream.ts'

const frame = (kind, data = {}) => ({ type: `keeper.stream.${kind}`, data: { cycle_id: 'cycle', stream_id: 'stream', attempt: 1, index: 0, ...data } })

test('duplicate and out of order increments never duplicate visible text', () => {
  let state = reduceNarrationStream(emptyNarrationStream(), frame('start'))
  state = reduceNarrationStream(state, frame('delta', { index: 2, offset: 0, text: '公开😀。第二句。' }))
  state = reduceNarrationStream(state, frame('delta', { index: 2, offset: 0, text: '公开😀。第二句。' }))
  state = reduceNarrationStream(state, frame('delta', { index: 1, offset: 0, text: '公开😀。' }))
  assert.equal(state.draft.text, '公开😀。第二句。')
  state = reduceNarrationStream(state, frame('delta', { index: 3, offset: 8, text: '第三句。' }))
  assert.equal(state.draft.text, '公开😀。第二句。第三句。')
})

test('reconnection snapshot covers in-flight merge and later overlapping delta', () => {
  let state = reduceNarrationStream(emptyNarrationStream(), frame('snapshot', { index: 1, text: '第一句。' }))
  state = reduceNarrationStream(state, { type: 'connection.lost' })
  assert.equal(state.draft.status, 'waiting')
  state = reduceNarrationStream(state, frame('snapshot', { index: 1, text: '第一句。', status: 'responding' }))
  state = reduceNarrationStream(state, frame('delta', { index: 2, offset: 0, text: '第一句。第二句。' }))
  assert.equal(state.draft.text, '第一句。第二句。')
  assert.equal(state.needsSync, false)
})

test('gap requests reconnect and does not display unjoined text', () => {
  let state = reduceNarrationStream(emptyNarrationStream(), frame('start'))
  state = reduceNarrationStream(state, frame('delta', { index: 2, offset: 4, text: '后来内容。' }))
  assert.equal(state.draft.text, '')
  assert.equal(state.needsSync, true)
  state = reduceNarrationStream(state, frame('snapshot', { index: 2, text: '第一句。后来内容。' }))
  assert.equal(state.needsSync, false)
})

test('formal event wins over every stale draft and replacement', () => {
  let state = reduceNarrationStream(emptyNarrationStream(), frame('start'))
  state = acceptNarrationEvent(state, { type: 'keeper.narration', payload: { cycle_id: 'cycle' }, seq: 42 })
  for (const kind of ['delta', 'replace', 'start', 'snapshot', 'end', 'interrupt']) {
    state = reduceNarrationStream(state, frame(kind, { index: 100, text: '迟到内容。' }))
    assert.equal(state.draft, null)
  }
})

test('cancel drops draft, retry replaces the bubble, old attempts stay retired', () => {
  let state = reduceNarrationStream(emptyNarrationStream(), frame('start'))
  state = reduceNarrationStream(state, frame('delta', { index: 1, offset: 0, text: '旧正文。' }))
  state = reduceNarrationStream(state, frame('interrupt', { index: 2, reason: 'cancelled' }))
  assert.equal(state.draft.text, '')
  state = reduceNarrationStream(state, frame('start', { stream_id: 'retry', attempt: 2 }))
  state = reduceNarrationStream(state, frame('snapshot', { index: 3, text: '旧正文。' }))
  state = reduceNarrationStream(state, frame('delta', { index: 99, offset: 0, text: '迟到内容。' }))
  assert.equal(state.draft.stream_id, 'retry')
  assert.equal(state.draft.text, '')
  state = reduceNarrationStream(state, { type: 'keeper.stream.snapshot', data: null })
  assert.equal(state.draft.status, 'interrupted')
  assert.equal(state.draft.reason, 'buffer_unavailable')
})

test('NPC-only successful reply removes the temporary waiting bubble', () => {
  let state = reduceNarrationStream(emptyNarrationStream(), frame('start'))
  state = reduceNarrationStream(state, frame('interrupt', { index: 1, reason: 'complete_without_narration' }))
  assert.equal(state.draft, null)
  state = reduceNarrationStream(state, frame('snapshot', { index: 1, reason: 'complete_without_narration', status: 'interrupted' }))
  assert.equal(state.draft, null)
})

for (const kind of ['start', 'snapshot']) {
  test(`same-cycle resumed ${kind} can restart the attempt budget and retires old frames`, () => {
    let state = reduceNarrationStream(emptyNarrationStream(), frame('start', { attempt: 2 }))
    state = reduceNarrationStream(state, frame('interrupt', { attempt: 2, index: 1, reason: 'paused' }))
    state = reduceNarrationStream(state, frame(kind, { stream_id: 'resumed', attempt: 1 }))
    assert.equal(state.draft.stream_id, 'resumed')
    assert.equal(state.draft.status, 'responding')
    for (const oldKind of ['start', 'snapshot', 'delta', 'interrupt']) {
      state = reduceNarrationStream(state, frame(oldKind, { attempt: 2, index: 99, offset: 0, text: '旧请求迟到正文。' }))
      assert.equal(state.draft.stream_id, 'resumed')
      assert.equal(state.draft.text, '')
    }
    state = reduceNarrationStream(state, frame('delta', { stream_id: 'resumed', attempt: 1, index: 1, offset: 0, text: '恢复后的正文。' }))
    assert.equal(state.draft.text, '恢复后的正文。')
    assert.equal(state.needsSync, false)
  })
}

test('missing reconnect buffer has an explicit state without reviving a formal event', () => {
  const unavailable = { type: 'keeper.stream.snapshot', data: null }
  let state = reduceNarrationStream(emptyNarrationStream(), unavailable)
  assert.equal(state.draft, null)
  state = reduceNarrationStream(state, frame('start'))
  state = reduceNarrationStream(state, frame('delta', { index: 1, offset: 0, text: '连接前的正文。' }))
  state = reduceNarrationStream(state, { type: 'connection.lost' })
  state = reduceNarrationStream(state, unavailable)
  assert.equal(state.draft.text, '')
  assert.equal(state.draft.status, 'interrupted')
  assert.equal(state.draft.reason, 'buffer_unavailable')
  state = reduceNarrationStream(state, frame('snapshot', { index: 2, text: '过期的正文。' }))
  assert.equal(state.draft.text, '')
  state = acceptNarrationEvent(state, { type: 'keeper.narration', payload: { cycle_id: 'cycle' }, seq: 42 })
  state = reduceNarrationStream(state, unavailable)
  assert.equal(state.draft, null)
  state = reduceNarrationStream(emptyNarrationStream(), frame('start'))
  state = reduceNarrationStream(state, frame('interrupt', { index: 1, reason: 'cancelled' }))
  state = reduceNarrationStream(state, unavailable)
  assert.equal(state.draft.reason, 'cancelled')
})
