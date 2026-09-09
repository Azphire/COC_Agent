import { useState } from 'react'
import { api } from '../api/session'

export default function HostUnlock({ onUnlock }: { onUnlock: () => void }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  return <form onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      await api('/host/unlock', key, 'POST')
      sessionStorage.setItem('coc.host', key); setKey(''); onUnlock()
    } catch (error) { setError(error instanceof Error ? error.message : '解锁失败') }
    finally { setBusy(false) }
  }}>
    <h2>主机解锁</h2>
    <p className="hint">主机在本机终端运行以下命令查看密钥。密钥只保存在当前标签页会话中。</p>
    <code>uv run --directory backend python scripts/host_key.py --show</code>
    <label htmlFor="host-key">主机管理密钥</label>
    <input id="host-key" type="password" autoComplete="off" value={key} onChange={event => setKey(event.target.value)} required />
    <button disabled={busy || !key}>解锁主机</button>
    {error && <p role="alert">{error}</p>}
    <p><a href="#/rooms">远程玩家通过邀请码加入多人房间</a></p>
  </form>
}
