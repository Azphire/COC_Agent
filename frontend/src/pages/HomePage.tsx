import { useEffect, useState } from 'react'
import { api, hostToken } from '../api/session'
import { roomStatus, storedRooms } from '../api/rooms'
import type { RoomSummary } from '../api/rooms'

export default function HomePage({ unlocked }: { unlocked: boolean }) {
  const [rooms, setRooms] = useState<RoomSummary[]>([])
  const [error, setError] = useState('')
  const lastRoom = localStorage.getItem('coc.last-room')
  useEffect(() => {
    if (unlocked) api<RoomSummary[]>('/rooms', hostToken()).then(setRooms).catch(e => setError(e.message))
  }, [unlocked])
  const knownIds = new Set(rooms.map(room => room.id))
  const available = [...rooms, ...storedRooms().filter(id => !knownIds.has(id)).map(id => ({ id, name: '已加入的游戏', status: 'running' as const, revision: 0 }))]
  const recent = available.find(room => room.id === lastRoom) || available.find(room => room.status !== 'ended')
  return <div className="home-page">
    <section className="home-intro"><p className="eyebrow">克苏鲁的呼唤 · 调查员游戏桌</p><h1>下一段调查，从这里开始。</h1><p>选择一个模组，带上队友，走进故事。</p>
      <div className="home-actions">
        {recent || lastRoom ? <a className="primary-link" href={`#/rooms/${recent?.id || lastRoom}`}>继续游戏{recent ? ` · ${recent.name}` : ''}</a> : <span className="hint">开始第一局后，可从这里继续。</span>}
        <a className="primary-link" href="#/new">开始新游戏</a><a className="secondary-link" href="#/join">加入朋友</a>
      </div>
    </section>
    {!!available.length && <section><h2>已有游戏</h2><ul className="character-list">{available.map(room => <li key={room.id}><a href={`#/rooms/${room.id}`}>{room.name}</a> · {roomStatus[room.status]}</li>)}</ul></section>}
    {error && <p role="alert">{error}</p>}
  </div>
}
