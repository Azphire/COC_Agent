import { useEffect, useState } from 'react'
import type { Room, Save } from '../api/rooms'
import { api, hostToken } from '../api/session'

export default function RoomGameMenu({ room, onManage, onLeave }: { room: Room; onManage?: () => void; onLeave: () => void }) {
  const [open, setOpen] = useState(false)
  const [saves, setSaves] = useState<Save[]>([])
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const host = !!onManage
  useEffect(() => { if (host && open) api<Save[]>(`/rooms/${room.id}/snapshots`, hostToken()).then(setSaves).catch(e => setError(e.message)) }, [host, open, room.id])
  async function command(command: 'pause' | 'resume' | 'save' | 'load' | 'cancel', values: { name?: string; snapshot_id?: string } = {}) {
    setBusy(true); setError('')
    try {
      await api(`/rooms/${room.id}/play-session/command`, hostToken(), 'POST', { member_id: room.self_member_id, command, ...values })
      if (command === 'save') setSaves(await api<Save[]>(`/rooms/${room.id}/snapshots`, hostToken()))
    } catch (e) { setError(e instanceof Error ? e.message : '操作失败') } finally { setBusy(false) }
  }
  return <details className="game-menu" open={open} onToggle={e => setOpen(e.currentTarget.open)}><summary>游戏菜单</summary>
    {open && <div className="game-menu-content">
      <div className="action-row">{host && room.status === 'running' && <button disabled={busy} onClick={() => void command('pause')}>暂停游戏</button>}{host && room.status === 'paused' && <button disabled={busy} onClick={() => void command('resume')}>恢复游戏</button>}
        {host && room.game?.cycle && ['running', 'waiting_for_roll', 'waiting_for_review', 'failed'].includes(room.game.cycle.status) && <button disabled={busy} onClick={() => void command('cancel')}>取消当前回合</button>}
        <a href="#/">回到首页</a><button onClick={onLeave}>{host ? '退出游戏桌面' : '离开房间'}</button>{onManage && <button onClick={onManage}>进入主机管理视图</button>}
      </div>
      {host && <><h3>存读档</h3>{['running', 'paused'].includes(room.status) && <form onSubmit={e => { e.preventDefault(); void command('save', { name }) }}><label>存档名称<input required maxLength={120} value={name} onChange={e => setName(e.target.value)} /></label><button disabled={busy}>保存游戏</button></form>}
        {saves.map(save => <p key={save.id}>{save.name} <button disabled={busy || room.status !== 'paused'} onClick={() => { if (window.confirm(`载入“${save.name}”会恢复角色与场景状态，后续事件保留。确认载入？`)) void command('load', { snapshot_id: save.id }) }}>载入</button></p>)}{!!saves.length && room.status !== 'paused' && <p>暂停后可载入存档。</p>}
      </>}{error && <p role="alert">{error}</p>}
    </div>}
  </details>
}
