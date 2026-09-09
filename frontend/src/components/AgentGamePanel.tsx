import { useEffect, useRef, useState } from 'react'
import { api, requestId } from '../api/session'
import type { Result, Room } from '../api/rooms'
import type { AgentProfile } from '../api/agents'
import { difficultyLabels, nodeLabels, resultLabels } from '../api/agents'
import KnowledgeBindingPanel from './KnowledgeBindingPanel'

type Props = { room: Room; token: string; acceptRoom: (room: Room) => void }

export default function AgentGamePanel({ room, token, acceptRoom }: Props) {
  const [profiles, setProfiles] = useState<AgentProfile[]>([])
  const [modules, setModules] = useState<{ id: string; title: string }[]>([])
  const [moduleId, setModuleId] = useState('stopped-clock')
  const [selection, setSelection] = useState<Record<string, string>>({})
  const [action, setAction] = useState('')
  const [actor, setActor] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [runs, setRuns] = useState<Record<string, unknown>[]>([])
  const [memory, setMemory] = useState<unknown[]>([])
  const [retrievals, setRetrievals] = useState<Record<string, unknown[]>>({})
  const [debugOpen, setDebugOpen] = useState(false)
  const pending = useRef<{ text: string; actor: string; id: string } | null>(null)
  const prefix = `/rooms/${room.id}`
  const game = room.game
  const cycle = game?.cycle
  const active = !!cycle && ['running', 'waiting_for_roll', 'failed'].includes(cycle.status)
  const editable = room.is_host && ['lobby', 'paused'].includes(room.status) && !active
  useEffect(() => {
    if (room.is_host) {
      api<AgentProfile[]>('/agent-profiles', token).then(setProfiles).catch(e => setError(e.message))
      api<{ id: string; title: string }[]>('/modules', token).then(setModules).catch(e => setError(e.message))
    }
  }, [room.is_host, token])
  useEffect(() => {
    if (!room.is_host || !debugOpen) return
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        const nextRuns = await api<Record<string, unknown>[]>(prefix + '/agent-runs', token)
        const nextMemory = await api<unknown[]>(prefix + '/memories', token)
        const audit = await Promise.all(nextRuns.map(async run => [String(run.id), await api<unknown[]>(`${prefix}/agent-runs/${run.id}/retrievals`, token)] as const))
        if (!cancelled) { setRuns(nextRuns); setMemory(nextMemory); setRetrievals(Object.fromEntries(audit)) }
      } catch (e) { if (!cancelled) setError(e instanceof Error ? e.message : '加载失败') }
    }, 200)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [room.is_host, room.revision, debugOpen, prefix, token])
  async function command(path: string, body?: unknown, method = 'POST') {
    setBusy(true); setError('')
    try { const result = await api<Result>(prefix + path, token, method, body); acceptRoom(result.room); return true }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败'); return false }
    finally { setBusy(false) }
  }
  const seats = room.members.filter(m => m.active && (m.id === room.host_member_id || m.controller_type === 'agent'))
  return <>
    {room.is_host && <KnowledgeBindingPanel room={room} token={token} acceptRoom={acceptRoom} />}
    {room.is_host && <section><h2>AI 与模组设置</h2><p><a href="#/agents">创建或编辑 Agent 档案</a></p>
      {!game?.module && <form onSubmit={e => { e.preventDefault(); void command('/module', { module_id: moduleId }) }}>
        <label>原创测试模组<select id="agent-module" value={moduleId} onChange={e => setModuleId(e.target.value)}>{modules.map(m => <option key={m.id} value={m.id}>{m.title}</option>)}</select></label>
        <button disabled={busy || !editable}>绑定测试模组</button>
      </form>}
      {game?.module && <p>{game.module.title} · {game.enabled ? 'Agent 已启用' : 'Agent 已停用'} {editable && <button disabled={busy} onClick={() => void command('/agent-config', { enabled: !game.enabled }, 'PATCH')}>{game.enabled ? '停用 Agent' : '启用 Agent'}</button>}</p>}
      {seats.map(member => {
        const role = member.id === room.host_member_id ? 'keeper' : 'investigator'
        const binding = game?.bindings.find(b => b.member_id === member.id)
        return <div className="agent-binding" key={member.id}>
          <strong>{role === 'keeper' ? 'AI KP' : member.display_name}</strong>
          {binding ? <p>{binding.name} · {binding.status} <button disabled={busy || !editable} onClick={() => void command(`/agent-bindings/${binding.id}`, undefined, 'DELETE')}>解除绑定 · {binding.name}</button></p> : <>
            <label>档案<select aria-label={`绑定 ${role === 'keeper' ? 'AI KP' : member.display_name}`} value={selection[member.id] || ''} onChange={e => setSelection({ ...selection, [member.id]: e.target.value })}><option value="">选择{role === 'keeper' ? 'KP' : '调查员'}档案</option>{profiles.filter(p => p.role === role).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
            <button disabled={busy || !editable || !selection[member.id]} onClick={() => void command('/agent-bindings', { member_id: member.id, profile_id: selection[member.id] })}>绑定 · {role === 'keeper' ? 'AI KP' : member.display_name}</button>
          </>}
        </div>
      })}
      {(!game?.module || seats.some(m => !game.bindings.some(b => b.member_id === m.id))) && <p>开始 Agent 回合前，请选择模组、绑定 AI KP，并给每个 AI 队友分配角色和档案。</p>}
    </section>}
    {error && <p role="alert">{error}</p>}
    {game?.module && <section className="agent-game"><h2>{game.module.title}</h2><p>{game.module.public_introduction}</p>
      <p role="status" data-testid="agent-cycle-status">{game.module.completed ? '调查已结束' : !cycle || cycle.status === 'completed' ? '等待真人行动' : cycle.status === 'cancelled' ? '回合已取消，可提交新行动' : cycle.status === 'failed' ? 'Agent 回合失败' : nodeLabels[cycle.current_node] || cycle.status}</p>
      {cycle?.safe_error && <p role="alert">{cycle.safe_error}</p>}
      {room.is_host && active && <div className="action-row">
        <button disabled={busy} onClick={() => void command('/agent-cycle/cancel')}>取消 Agent 回合</button>
        {cycle?.status === 'failed' && <button disabled={busy || room.status !== 'running'} onClick={() => void command('/agent-cycle/retry')}>重试 Agent 回合</button>}
      </div>}
      {room.status !== 'ended' && !game.module.completed && <form onSubmit={async e => {
        e.preventDefault()
        if (!pending.current || pending.current.text !== action || pending.current.actor !== actor) pending.current = { text: action, actor, id: requestId() }
        if (await command('/actions', { text: action, client_request_id: pending.current.id, ...(room.is_host ? { actor_member_id: actor } : {}) })) { setAction(''); pending.current = null }
      }}>
        {room.is_host && <label>真人行动席位<select id="agent-action-actor" value={actor} onChange={e => setActor(e.target.value)}><option value="">选择本地真人调查员</option>{room.members.filter(m => m.active && m.role === 'player' && m.controller_type === 'human' && m.access_type === 'host_managed').map(m => <option key={m.id} value={m.id}>{m.display_name}</option>)}</select></label>}
        <label>调查行动<textarea id="agent-action" value={action} maxLength={2000} onChange={e => setAction(e.target.value)} placeholder="描述调查员想做什么，例如走进维修间检查工作台。" required /></label>
        <button disabled={busy || active || room.status !== 'running' || !game.enabled || !action.trim() || (room.is_host && !actor)}>提交行动</button>
      </form>}
      <div className="check-list">{game.checks.map(check => <article key={check.id} className="check-card" data-check-id={check.id}>
        <h3>{check.kind === 'skill' ? '技能' : '属性'}检定 · {check.name}</h3>
        <p>{room.members.find(m => m.id === check.target_member_id)?.display_name} · 数值 {check.value} · {difficultyLabels[check.difficulty]} · 奖励骰 {check.bonus_dice} / 惩罚骰 {check.penalty_dice}</p><p>{check.reason}</p>
        {check.status === 'pending' ? <button disabled={busy || room.status !== 'running' || (!room.is_host && room.self_member_id !== check.target_member_id)} onClick={() => void command(`/checks/${check.id}/roll`, {})}>掷骰完成检定</button> : check.status === 'cancelled' ? <p>检定已取消</p> : <>
          <p>个位 {check.dice?.units} · 十位 [{check.dice?.tens.join(', ')}] · 候选 [{check.dice?.candidates.join(', ')}]</p>
          <p><strong>{check.result?.total} · {resultLabels[check.result?.level || '']}</strong> · 本次目标 {check.result?.threshold} · {check.result?.passed ? '通过' : '未通过'}</p>
        </>}
      </article>)}</div>
      <h3>已公开线索</h3>{game.module.clues.length === 0 ? <p>尚未发现线索。</p> : game.module.clues.map(clue => <article key={clue.id}><h4>{clue.title}</h4><p>{clue.content}</p></article>)}
    </section>}
    {room.is_host && game?.module && <section data-testid="host-agent-debug"><details onToggle={e => setDebugOpen(e.currentTarget.open)}><summary>主机 Agent 调试面板 · HOST_DEBUG</summary>
      <button disabled={busy} onClick={async () => {
        try {
          setRuns(await api<Record<string, unknown>[]>(prefix + '/agent-runs', token))
          setMemory(await api<unknown[]>(prefix + '/memories', token))
        } catch (e) { setError(e instanceof Error ? e.message : '加载失败') }
      }}>刷新运行与记忆</button>
      <pre>{JSON.stringify(cycle, null, 2)}</pre>
      {runs.map(run => <details key={String(run.id)}><summary>{String(run.graph_node)} · {String(run.status)} · {String(run.model)} · {String(run.latency_ms)} ms</summary><pre>{JSON.stringify({ run_id: run.id, cycle_id: run.cycle_id, output: run.structured_output, tools: run.tool_results, error: run.safe_error }, null, 2)}</pre><details><summary>检索证据与实际注入</summary><pre>{JSON.stringify(retrievals[String(run.id)] || [], null, 2)}</pre></details></details>)}
      <details><summary>当前记忆</summary><pre>{JSON.stringify(memory, null, 2)}</pre></details>
    </details></section>}
  </>
}
