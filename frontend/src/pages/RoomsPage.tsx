import { useEffect, useRef, useState } from 'react'
import { api, ApiError, authHeaders, hostToken, requestId, socketUrl } from '../api/session'
import { credentialKey, roomStatus, storedRooms } from '../api/rooms'
import type { Result, Room, RoomEvent, RoomSummary, Save, SessionState } from '../api/rooms'
import { charactersApi } from '../api/characters'
import type { Character } from '../api/characters'
import HostUnlock from '../components/HostUnlock'

function errorText(error: unknown) { return error instanceof Error ? error.message : '请求失败，请重试' }

export default function RoomsPage({ roomId, unlocked, onUnlock }: { roomId?: string; unlocked: boolean; onUnlock: () => void }) {
  const [rooms, setRooms] = useState<RoomSummary[]>([])
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const [invite, setInvite] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [busy, setBusy] = useState(false)
  const [invites, setInvites] = useState<Record<string, string>>({})
  useEffect(() => {
    if (unlocked && !roomId) api<RoomSummary[]>('/rooms', hostToken()).then(setRooms).catch(error => setError(errorText(error)))
  }, [unlocked, roomId])
  if (roomId) return <RoomSession key={`${roomId}:${unlocked}`} roomId={roomId} initialInvite={invites[roomId] || ''} />
  return <>
    <section><h2>多人房间</h2><p>主机发布调查员，真人玩家通过邀请码加入。Agent 席位尚未接入自动行动。</p></section>
    {error && <p role="alert">{error}</p>}
    <form onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError('')
      try {
        const result = await api<Result>('/rooms/join', '', 'POST', { invite_code: invite, display_name: displayName })
        localStorage.setItem(credentialKey(result.room.id), result.member_token!)
        setInvite(''); window.location.hash = `#/rooms/${result.room.id}`
      } catch (error) { setError(errorText(error)) } finally { setBusy(false) }
    }}><h2>加入房间</h2><div className="field-grid">
      <label>邀请码<input id="join-invite" autoComplete="off" value={invite} onChange={e => setInvite(e.target.value)} required /></label>
      <label>显示名<input id="join-name" maxLength={120} value={displayName} onChange={e => setDisplayName(e.target.value)} required /></label>
    </div><button disabled={busy}>加入房间</button></form>
    {storedRooms().length > 0 && <section><h2>重新连接</h2>{storedRooms().map(id => <p key={id}><a href={`#/rooms/${id}`}>继续房间 {id}</a></p>)}</section>}
    {!unlocked ? <HostUnlock onUnlock={onUnlock} /> : <>
      <form onSubmit={async event => {
        event.preventDefault(); setBusy(true); setError('')
        try {
          const result = await api<Result>('/rooms', hostToken(), 'POST', { name })
          setInvites(previous => ({ ...previous, [result.room.id]: result.invite_code! }))
          window.location.hash = `#/rooms/${result.room.id}`
        } catch (error) { setError(errorText(error)) } finally { setBusy(false) }
      }}><h2>创建房间</h2><label>房间名称<input id="room-name" maxLength={120} value={name} onChange={e => setName(e.target.value)} required /></label><button disabled={busy}>创建房间</button></form>
      <section><h2>主机房间列表</h2>{rooms.length === 0 && <p>尚无房间</p>}{rooms.map(room => <p key={room.id}><a href={`#/rooms/${room.id}`}>{room.name}</a> · {roomStatus[room.status]}</p>)}</section>
    </>}
  </>
}

function RoomSession({ roomId, initialInvite }: { roomId: string; initialInvite: string }) {
  const token = localStorage.getItem(credentialKey(roomId)) || hostToken()
  const credentialType = localStorage.getItem(credentialKey(roomId)) ? 'member' : 'host'
  const [room, setRoom] = useState<Room | null>(null)
  const [events, setEvents] = useState<RoomEvent[]>([])
  const [online, setOnline] = useState<string[]>([])
  const [connection, setConnection] = useState('连接中')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [invite, setInvite] = useState(initialInvite)
  const [characters, setCharacters] = useState<Character[]>([])
  const [publishId, setPublishId] = useState('')
  const [memberName, setMemberName] = useState('')
  const [controller, setController] = useState('human')
  const [assignments, setAssignments] = useState<Record<string, string>>({})
  const [text, setText] = useState('')
  const [expression, setExpression] = useState('1d100')
  const [reason, setReason] = useState('')
  const [visibility, setVisibility] = useState('public')
  const [actor, setActor] = useState('')
  const [saveName, setSaveName] = useState('')
  const [saves, setSaves] = useState<Save[]>([])
  const [editing, setEditing] = useState<{ state: SessionState; revision: number } | null>(null)
  const lastSeq = useRef(0)
  const pending = useRef<Record<string, { fingerprint: string; id: string }>>({})
  const prefix = `/rooms/${roomId}`

  function acceptRoom(next: Room) {
    setRoom(previous => !previous || next.revision >= previous.revision ? next : previous)
  }
  function acceptEvent(event: RoomEvent) {
    setEvents(previous => previous.some(item => item.seq === event.seq) ? previous : [...previous, event].sort((a, b) => a.seq - b.seq))
  }
  useEffect(() => {
    let active = true
    let socket: WebSocket | null = null
    let timer: number | undefined
    let heartbeat: number | undefined
    let attempts = 0
    let lastFrame = Date.now()
    lastSeq.current = 0
    if (!token) return
    function connect() {
      if (!active) return
      setConnection(attempts ? '正在重连' : '连接中')
      socket = new WebSocket(socketUrl(`/ws/rooms/${roomId}`))
      socket.onopen = () => {
        lastFrame = Date.now()
        socket?.send(JSON.stringify({ type: 'auth', credential_type: credentialType, token, after_seq: lastSeq.current }))
        heartbeat = window.setInterval(() => {
          if (Date.now() - lastFrame > 40000) { socket?.close(); return }
          if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ping' }))
        }, 15000)
      }
      socket.onmessage = event => {
        if (!active) return
        lastFrame = Date.now()
        const message = JSON.parse(event.data)
        if (message.type === 'auth.ok') { attempts = 0; setConnection('已连接'); setError('') }
        if (message.type === 'room.snapshot') acceptRoom(message.data)
        if (message.type === 'room.event') acceptEvent(message.data)
        if (message.type === 'room.synced') lastSeq.current = message.data.seq
        if (message.type === 'presence.changed') setOnline(message.data.online_member_ids)
        if (message.type === 'error') setError(message.data.message)
      }
      socket.onclose = event => {
        window.clearInterval(heartbeat)
        if (!active) return
        setOnline([])
        if (event.code === 4401) {
          setConnection('凭据失效'); setError('凭据无效或成员已离开，请返回房间入口重新加入或解锁。')
          setRoom(null); setEvents([])
          if (credentialType === 'member') localStorage.removeItem(credentialKey(roomId))
          return
        }
        setConnection('已断开，自动重连中')
        timer = window.setTimeout(connect, Math.min(1000 * 2 ** attempts++, 10000))
      }
      socket.onerror = () => socket?.close()
    }
    connect()
    return () => {
      active = false; window.clearTimeout(timer); window.clearInterval(heartbeat)
      if (socket?.readyState === WebSocket.CONNECTING) socket.addEventListener('open', () => socket?.close(), { once: true })
      else socket?.close()
    }
  }, [roomId, token, credentialType])

  const isHost = room?.is_host || false
  useEffect(() => {
    if (isHost) {
      charactersApi.list().then(items => setCharacters(items.filter(c => c.status === 'finalized'))).catch(error => setError(errorText(error)))
      api<Save[]>(`${prefix}/snapshots`, token).then(setSaves).catch(error => setError(errorText(error)))
    }
  }, [isHost, prefix, token])

  async function command(path: string, body?: unknown, method = 'POST') {
    setBusy(true); setError('')
    try {
      const result = await api<Result>(`${prefix}${path}`, token, method, body)
      acceptRoom(result.room)
      if (result.event) acceptEvent(result.event)
      if (result.invite_code) setInvite(result.invite_code)
      if (result.snapshot) setSaves(previous => [...previous, result.snapshot!])
      return result
    } catch (error) { setError(errorText(error)); return null } finally { setBusy(false) }
  }
  async function send(kind: 'messages' | 'rolls') {
    const body = { ...(kind === 'messages' ? { text } : { expression, reason }), visibility, ...(actor ? { actor_member_id: actor } : {}) }
    const fingerprint = JSON.stringify(body)
    if (pending.current[kind]?.fingerprint !== fingerprint) pending.current[kind] = { fingerprint, id: requestId() }
    const result = await command(`/${kind}`, { ...body, client_request_id: pending.current[kind].id })
    if (result) { delete pending.current[kind]; if (kind === 'messages') setText('') }
  }
  async function exportLog(format: string) {
    try {
      const response = await fetch(`/api${prefix}/logs?format=${format}`, { headers: authHeaders(token) })
      if (!response.ok) throw new Error('导出失败')
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a'); link.href = url; link.download = `room-${roomId}.${format === 'jsonl' ? 'jsonl' : 'md'}`
      link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) { setError(errorText(error)) }
  }
  async function leave() {
    if (!room) return
    if (room.status !== 'ended') {
      setBusy(true)
      try { await api(`${prefix}/leave`, token, 'POST') }
      catch (error) { if (!(error instanceof ApiError && error.status === 401)) { setError(errorText(error)); setBusy(false); return } }
    }
    localStorage.removeItem(credentialKey(roomId)); window.location.hash = '#/rooms'
  }

  const self = room?.members.find(member => member.id === room.self_member_id)
  const writable = room?.status !== 'ended'
  const lobby = room?.status === 'lobby' || room?.status === 'paused'
  return <div className="room-session">
    <p><a href="#/rooms">返回房间入口</a> · <span role="status" data-testid="connection">{token ? connection : '缺少凭据，请返回入口解锁或加入'}</span></p>
    {error && <p role="alert">{error}</p>}
    {!room ? <p>等待认证与房间同步…</p> : <>
      <section><h2>{room.name} · <span data-testid="room-status">{roomStatus[room.status]}</span></h2>
        <p>最新事件序号：<strong data-testid="latest-seq">{room.latest_seq}</strong> · {isHost ? '主机管理身份' : self?.display_name}</p>
        {isHost && writable && <><p>邀请码：<code data-testid="invite-code">{invite || '仅创建／重新生成时显示，请重新生成以分享'}</code></p><button disabled={busy} onClick={() => void command('/invite/rotate')}>重新生成邀请码</button></>}
        {!isHost && <button disabled={busy} onClick={() => void leave()}>离开房间并清除凭据</button>}
        {isHost && <div className="action-row">
          {room.status === 'lobby' && <button disabled={busy} onClick={() => void command('/start')}>开始游戏</button>}
          {room.status === 'running' && <button disabled={busy} onClick={() => void command('/pause')}>暂停游戏</button>}
          {room.status === 'paused' && <button disabled={busy} onClick={() => void command('/resume')}>恢复游戏</button>}
          {['running', 'paused'].includes(room.status) && <button disabled={busy} onClick={() => { if (window.confirm('结束后房间将永久只读。确认结束游戏？')) void command('/end') }}>结束游戏</button>}
        </div>}
      </section>
      <section><h2>玩家与席位</h2><ul className="character-list">{room.members.map(member => <li key={member.id} data-member-id={member.id}>
        <strong>{member.display_name}</strong> · {member.role === 'host' ? '主机' : member.controller_type === 'agent' ? 'Agent 占位 · 尚未接入自动行动' : member.access_type === 'remote' ? '远程真人' : '本地真人'}
        <p>{!member.active ? '已离开' : online.includes(member.id) ? '在线' : member.access_type === 'host_managed' && member.role === 'player' ? '主机管理' : '离线'} · {member.ready ? '已准备' : '未准备'}</p>
        {member.active && member.role === 'player' && writable && <div className="action-row">
          {lobby && (member.id === room.self_member_id || (isHost && member.access_type === 'host_managed')) && <button disabled={busy} onClick={() => void command('/ready', { member_id: member.id, ready: !member.ready })}>{member.ready ? '取消准备' : '准备'} · {member.display_name}</button>}
          {isHost && <button disabled={busy} onClick={() => void command(`/members/${member.id}`, { active: false }, 'PATCH')}>移出 · {member.display_name}</button>}
        </div>}
      </li>)}</ul>
      {isHost && lobby && <form onSubmit={async event => { event.preventDefault(); if (await command('/members', { display_name: memberName, controller_type: controller })) setMemberName('') }}>
        <div className="field-grid"><label>席位名称<input id="member-name" value={memberName} maxLength={120} onChange={e => setMemberName(e.target.value)} required /></label>
        <label>控制器<select id="member-controller" value={controller} onChange={e => setController(e.target.value)}><option value="human">本地真人</option><option value="agent">Agent 占位</option></select></label></div>
        <button disabled={busy}>添加席位</button>
      </form>}
      </section>
      <section><h2>房间调查员</h2>
        {isHost && room.status === 'lobby' && <form onSubmit={event => { event.preventDefault(); void command('/character-slots', { character_id: publishId }) }}>
          <label>已确认的本地角色<select id="publish-character" value={publishId} onChange={e => setPublishId(e.target.value)} required><option value="">选择角色</option>{characters.filter(c => !room.character_slots.some(s => s.character_snapshot?.id === c.id)).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label><button disabled={busy || !publishId}>发布角色</button>
        </form>}
        {room.character_slots.map(slot => <article className="room-character" key={slot.id} data-slot-id={slot.id}>
          <h3>{slot.public_summary.name}</h3><p>{slot.public_summary.age ?? '—'} 岁 · {slot.public_summary.occupation || '未记录职业'} · {slot.member_id ? `已分配：${room.members.find(m => m.id === slot.member_id)?.display_name}` : '可选择'}</p>
          {lobby && <div className="action-row">
            {!slot.member_id && (isHost ? <><select aria-label={`分配 ${slot.public_summary.name}`} value={assignments[slot.id] || ''} onChange={e => setAssignments({ ...assignments, [slot.id]: e.target.value })}><option value="">选择玩家席位</option>{room.members.filter(m => m.active && m.role === 'player' && !m.slot_id).map(m => <option key={m.id} value={m.id}>{m.display_name}</option>)}</select><button disabled={busy || !assignments[slot.id]} onClick={() => void command('/character-assignments', { slot_id: slot.id, member_id: assignments[slot.id] })}>分配 · {slot.public_summary.name}</button></> : <button disabled={busy || !!self?.slot_id} onClick={() => void command('/character-assignments', { slot_id: slot.id })}>选择 · {slot.public_summary.name}</button>)}
            {slot.member_id && (isHost || slot.member_id === room.self_member_id) && <button disabled={busy} onClick={() => void command(`/character-assignments/${slot.id}`, undefined, 'DELETE')}>取消分配 · {slot.public_summary.name}</button>}
            {isHost && !slot.member_id && room.status === 'lobby' && <button disabled={busy} onClick={() => void command(`/character-slots/${slot.id}`, undefined, 'DELETE')}>撤下 · {slot.public_summary.name}</button>}
          </div>}
          {slot.character_snapshot && <details><summary>完整角色卡 · {slot.public_summary.name}</summary><pre>{JSON.stringify(slot.character_snapshot, null, 2)}</pre></details>}
        </article>)}
      </section>
      <section><h2>当前场景</h2><h3 data-testid="scene-title">{room.session_state.scene_title || '尚未设置场景'}</h3><p className="preserve-lines">{room.session_state.scene_summary}</p>
        <p>回合：{room.session_state.round_number ?? '—'} · 当前行动：{room.character_slots.find(s => s.id === room.session_state.active_slot_id)?.public_summary.name || '—'}</p>
        {Object.entries(room.session_state.characters).map(([id, runtime]) => <p key={id} data-runtime-id={id}>{room.character_slots.find(s => s.id === id)?.public_summary.name} · HP {runtime.hp ?? '—'} / MP {runtime.mp ?? '—'} / SAN {runtime.san ?? '—'} / Luck {runtime.luck ?? '—'} · {runtime.conditions.join('、')}</p>)}
        {isHost && ['running', 'paused'].includes(room.status) && <button disabled={busy} onClick={() => setEditing({ state: structuredClone(room.session_state), revision: room.revision })}>编辑场景与资源</button>}
        {editing && writable && <form onSubmit={async event => { event.preventDefault(); if (await command('/session-state', { expected_revision: editing.revision, state: editing.state }, 'PATCH')) setEditing(null) }}>
          <label>场景标题<input id="scene-title" maxLength={200} value={editing.state.scene_title} onChange={e => setEditing({ ...editing, state: { ...editing.state, scene_title: e.target.value } })} /></label>
          <label>场景摘要<textarea id="scene-summary" maxLength={2000} value={editing.state.scene_summary} onChange={e => setEditing({ ...editing, state: { ...editing.state, scene_summary: e.target.value } })} /></label>
          <div className="field-grid"><label>回合编号<input id="round-number" type="number" min={0} max={1000000} value={editing.state.round_number ?? ''} onChange={e => setEditing({ ...editing, state: { ...editing.state, round_number: e.target.value === '' ? null : Number(e.target.value) } })} /></label>
          <label>当前行动<select value={editing.state.active_slot_id || ''} onChange={e => setEditing({ ...editing, state: { ...editing.state, active_slot_id: e.target.value || null } })}><option value="">无</option>{room.character_slots.map(s => <option key={s.id} value={s.id}>{s.public_summary.name}</option>)}</select></label></div>
          {Object.entries(editing.state.characters).map(([id, runtime]) => <fieldset key={id}><legend>{room.character_slots.find(s => s.id === id)?.public_summary.name}</legend><div className="field-grid">
            {(['hp', 'mp', 'san', 'luck'] as const).map(key => <label key={key}>{key.toUpperCase()}<input aria-label={`${id}-${key}`} type="number" min={0} max={100000} value={runtime[key] ?? ''} onChange={e => setEditing({ ...editing, state: { ...editing.state, characters: { ...editing.state.characters, [id]: { ...runtime, [key]: e.target.value === '' ? null : Number(e.target.value) } } } })} /></label>)}
            <label>状态（逗号分隔）<input value={runtime.conditions.join(',')} onChange={e => setEditing({ ...editing, state: { ...editing.state, characters: { ...editing.state.characters, [id]: { ...runtime, conditions: e.target.value ? e.target.value.split(',') : [] } } } })} /></label>
          </div></fieldset>)}<div className="action-row"><button disabled={busy}>保存场景与资源</button><button type="button" onClick={() => setEditing(null)}>取消编辑</button></div>
        </form>}
      </section>
      {isHost && <section><h2>存档</h2>{['running', 'paused'].includes(room.status) && <form onSubmit={event => { event.preventDefault(); void command('/snapshots', { name: saveName }) }}><label>存档名称<input id="save-name" maxLength={120} value={saveName} onChange={e => setSaveName(e.target.value)} required /></label><button disabled={busy}>创建存档</button></form>}
        {saves.map(save => <p key={save.id}>{save.name} · 事件 {save.event_seq} <button disabled={busy || room.status !== 'paused'} onClick={async () => {
          if (window.confirm(`载入“${save.name}”将恢复场景、角色资源和分配。之后的事件保留，房间保持暂停；凭据和在线状态不变。确认载入？`)) {
            if (await command(`/snapshots/${save.id}/load`)) setEditing(null)
          }
        }}>载入 · {save.name}</button></p>)}
      </section>}
      <section><h2>聊天与掷骰</h2>{writable && <>
        <div className="field-grid"><label>可见性<select id="event-visibility" value={visibility} onChange={e => setVisibility(e.target.value)}><option value="public">公开</option><option value="actor_and_host">仅自己与主机</option>{isHost && <option value="host_only">仅主机</option>}</select></label>
        {isHost && <label>操作身份<select value={actor} onChange={e => setActor(e.target.value)}><option value="">主机</option>{room.members.filter(m => m.active && m.role === 'player' && m.controller_type === 'human' && m.access_type === 'host_managed').map(m => <option key={m.id} value={m.id}>{m.display_name}</option>)}</select></label>}</div>
        <form onSubmit={e => { e.preventDefault(); void send('messages') }}><label>消息<textarea id="chat-message" value={text} onChange={e => setText(e.target.value)} maxLength={4000} required /></label><button disabled={busy || !text.trim()}>发送消息</button></form>
        <form onSubmit={e => { e.preventDefault(); void send('rolls') }}><div className="field-grid"><label>骰子表达式<input id="dice-expression" value={expression} maxLength={32} onChange={e => setExpression(e.target.value)} required /></label><label>原因<input id="dice-reason" value={reason} maxLength={2000} onChange={e => setReason(e.target.value)} /></label></div><button disabled={busy}>服务端掷骰</button></form>
      </>}
      <ol className="timeline" data-testid="timeline">{events.map(event => <li key={event.seq} data-event-seq={event.seq}>
        <small>#{event.seq} · {new Date(event.occurred_at).toLocaleTimeString()} · {room.members.find(m => m.id === event.actor_member_id)?.display_name || '系统'} · {event.visibility === 'public' ? '公开' : '私密'} · {event.type}</small>
        {event.type === 'chat.message' ? <p className="preserve-lines">{String(event.payload.text)}</p> : event.type === 'dice.rolled' ? <p>{String(event.payload.reason)} · {String(event.payload.expression)} → [{(event.payload.dice as number[]).join(', ')}] {Number(event.payload.modifier) >= 0 ? '+' : ''}{String(event.payload.modifier)} = <strong>{String(event.payload.total)}</strong></p> : <details><summary>{eventLabel(event.type)}</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>}
      </li>)}</ol>
      <div className="action-row"><button onClick={() => void exportLog('jsonl')}>导出 JSONL</button><button onClick={() => void exportLog('markdown')}>导出 Markdown</button></div>
      </section>
    </>}
  </div>
}

function eventLabel(type: string) {
  const names: Record<string, string> = { 'room.created': '房间创建', 'member.joined': '成员加入', 'member.left': '成员离开', 'member.deactivated': '成员失活', 'member.ready': '准备状态变化', 'character.published': '角色发布', 'character.assigned': '角色分配', 'character.unassigned': '角色取消分配', 'game.started': '游戏开始', 'game.paused': '游戏暂停', 'game.resumed': '游戏恢复', 'game.ended': '游戏结束', 'scene.updated': '场景更新', 'session.updated': '运行时状态更新', 'snapshot.created': '存档创建', 'snapshot.loaded': '存档载入', 'invite.rotated': '邀请码已更新' }
  return names[type] || type
}
