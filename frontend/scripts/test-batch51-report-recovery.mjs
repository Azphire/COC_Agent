import assert from 'node:assert/strict'
import test from 'node:test'
import { acknowledgeSupplement, supplementRequestId } from '../src/api/reportRecovery.ts'

function storage() {
  const rows = new Map()
  return { getItem: key => rows.get(key) ?? null, setItem: (key, value) => rows.set(key, value), removeItem: key => rows.delete(key) }
}
const available = { report_seq: 76, available: true, status: 'available', supplement_id: null }

test('network failure and refresh reuse the unacknowledged request ID', () => {
  const local = storage()
  assert.equal(supplementRequestId(local, 'room', available, () => 'first-id'), 'first-id')
  assert.equal(supplementRequestId(local, 'room', { ...available }, () => 'duplicate-id'), 'first-id')
})

test('a server-confirmed failed supplement allows a fresh explicit attempt', () => {
  const local = storage()
  supplementRequestId(local, 'room', available, () => 'first-id')
  const failed = { ...available, status: 'failed', supplement_id: 'accepted-supplement' }
  assert.equal(supplementRequestId(local, 'room', failed, () => 'second-id'), 'second-id')
  assert.equal(supplementRequestId(local, 'room', failed, () => 'third-id'), 'second-id')
})

test('acknowledgment clears pending state and room/report IDs stay isolated', () => {
  const local = storage()
  supplementRequestId(local, 'room', available, () => 'first-id')
  assert.equal(supplementRequestId(local, 'other-room', available, () => 'other-id'), 'other-id')
  assert.equal(supplementRequestId(local, 'room', { ...available, report_seq: 77 }, () => 'next-id'), 'next-id')
  acknowledgeSupplement(local, 'room', 76)
  assert.equal(supplementRequestId(local, 'room', available, () => 'fresh-id'), 'fresh-id')
  assert.equal(supplementRequestId(local, 'other-room', available, () => 'unexpected'), 'other-id')
})
