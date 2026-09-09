import { useEffect, useState } from 'react'
import { api } from '../api/session'
import type { Result, Room } from '../api/rooms'
import type { KnowledgeSource } from '../api/knowledge'

export default function KnowledgeBindingPanel({ room, token, acceptRoom }: { room: Room; token: string; acceptRoom: (room: Room) => void }) {
  const [sources, setSources] = useState<KnowledgeSource[]>([])
  const [selectedRules, setRules] = useState<string[] | null>(null)
  const [selectedModule, setModule] = useState<string | null>(null)
  const [opening, setOpening] = useState('')
  const [error, setError] = useState('')
  const binding = room.game?.knowledge
  const rules = selectedRules ?? binding?.rules.map(s => s.source_id) ?? []
  const module = selectedModule ?? binding?.module?.source_id ?? ''
  const editable = ['lobby', 'paused'].includes(room.status) && !['running', 'waiting_for_roll', 'failed'].includes(room.game?.cycle?.status || '')
  useEffect(() => { api<KnowledgeSource[]>('/knowledge/sources', token).then(setSources).catch(e => setError(e.message)) }, [token])
  function ref(id: string) { const source = sources.find(s => s.source_id === id)!; return { source_id: source.source_id, source_hash: source.source_hash } }
  return <section><h2>房间知识来源</h2><p><a href="#/knowledge">管理本地知识库</a></p>
    {binding?.knowledge_missing && <p role="alert">knowledge_missing：索引版本缺失，Agent 已暂停；请重新索引相同版本或明确重新绑定。</p>}
    {binding?.rules.map(s => <p key={s.source_id}>规则绑定 · {sources.find(x => x.source_id === s.source_id)?.title || s.source_id} · {s.source_hash.slice(0, 12)}</p>)}
    {binding?.module && <p>模组绑定 · {sources.find(x => x.source_id === binding.module?.source_id)?.title || binding.module.source_id} · {binding.module.source_hash.slice(0, 12)}</p>}
    <form onSubmit={async e => { e.preventDefault(); setError(''); try { const result = await api<Result>(`/rooms/${room.id}/knowledge`, token, 'PATCH', { rules: rules.map(ref), module: module ? ref(module) : null, enabled: true, opening_scene: opening || null }); acceptRoom(result.room) } catch (e) { setError(e instanceof Error ? e.message : '绑定失败') } }}>
      <fieldset disabled={!editable}><legend>CoC 7 规则来源</legend>{sources.filter(s => s.kind !== 'module' && s.chunk_count > 0).map(s => <label key={s.source_id}><input type="checkbox" checked={rules.includes(s.source_id)} onChange={e => setRules(e.target.checked ? [...rules, s.source_id] : rules.filter(id => id !== s.source_id))} />{s.title}</label>)}</fieldset>
      <label>本地模组<select disabled={!editable} value={module} onChange={e => setModule(e.target.value)}><option value="">不绑定模组知识源</option>{sources.filter(s => s.kind === 'module' && s.chunk_count > 0).map(s => <option key={s.source_id} value={s.source_id}>{s.title}</option>)}</select></label>
      {!room.game?.module && module && <label>主机公开开场（玩家可见）<textarea disabled={!editable} maxLength={1000} value={opening} onChange={e => setOpening(e.target.value)} placeholder="只填写准备公开给玩家的简短开场。" /></label>}
      <button disabled={!editable || (!rules.length && !module)}>绑定所选版本</button>
    </form>{error && <p role="alert">{error}</p>}
  </section>
}
