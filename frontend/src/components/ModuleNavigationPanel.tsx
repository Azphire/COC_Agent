import { useEffect, useState } from 'react'
import { api, requestId } from '../api/session'
import type { Result, Room } from '../api/rooms'

type Navigation = { last_cycle_audit: Record<string, unknown> | null; enabled: boolean; module_structure_missing: boolean; current_scene_node_id: string; previous_scene_node_id: string | null; visited_scene_node_ids: string[]; active_npc_entity_ids: string[]; available_transition_ids: string[]; navigation_revision: number; pending_review_id: string | null; scenes: { node_id: string; title: string }[]; context: { module: { current_scene: { title: string; heading_path: string[] }; outgoing_transitions: { transition_id: string; target_scene_node_id: string; available: boolean; condition_summary: string }[] }; module_context_audit: { selected_node_ids: string[]; selected_block_ids: string[]; context_mode: string; omitted_block_count: number } } }
export default function ModuleNavigationPanel({ room, token, acceptRoom }: { room: Room; token: string; acceptRoom: (room: Room) => void }) {
  const [nav, setNav] = useState<Navigation | null>(null)
  const [error, setError] = useState('')
  const prefix = `/rooms/${room.id}`
  useEffect(() => {
    let active = true
    api<Navigation>(prefix + '/module-navigation', token).then(r => { if (active) setNav(r) }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [prefix, token, room.revision])
  if (!nav?.enabled) return error ? <p role="alert">{error}</p> : null
  return <section data-testid="module-navigation"><h3>当前场景导航</h3>{error && <p role="alert">{error}</p>}
    {nav.module_structure_missing ? <div><p role="alert">module_structure_missing：历史与调查板保留；请重建匹配来源版本，新回合已暂停。</p><button onClick={async () => { try { const r = await api<Result>(prefix + '/module-navigation/reload', token, 'POST'); setError(''); acceptRoom(r.room); setNav(await api<Navigation>(prefix + '/module-navigation', token)) } catch (e) { setError(String(e)) } }}>校验并重新加载匹配版本</button></div> : <>
      <p>{nav.context.module.current_scene.title} · {nav.context.module.current_scene.heading_path.join(' / ')}</p>
      <p>前一场景：{nav.scenes.find(n => n.node_id === nav.previous_scene_node_id)?.title || '无'} · 已访问 {nav.visited_scene_node_ids.length} 个场景 · 活动 NPC {nav.active_npc_entity_ids.length}</p>
      <p>主机审阅：{nav.pending_review_id ? '等待中' : '无'} · 模组上下文 {nav.context.module_context_audit.context_mode}</p>
      {nav.context.module.outgoing_transitions.map(t => <p key={t.transition_id}>{nav.scenes.find(n => n.node_id === t.target_scene_node_id)?.title} · {t.available ? '可执行' : '条件未满足或仅主机'} {t.condition_summary}</p>)}
      <details><summary>节点与上下文审计</summary><p>当前 {nav.current_scene_node_id} · revision {nav.navigation_revision}</p><pre>{JSON.stringify(nav.last_cycle_audit || nav.context.module_context_audit, null, 2)}</pre></details>
      <form onSubmit={async e => { e.preventDefault(); const d = new FormData(e.currentTarget); try { const r = await api<Result>(prefix + '/scene-transition', token, 'POST', { target_scene_node_id: d.get('target'), expected_revision: nav.navigation_revision, request_id: requestId() }); acceptRoom(r.room) } catch (e) { setError(String(e)) } }}>
        <label>一次性主机转换<select name="target">{nav.scenes.map(n => <option key={n.node_id} value={n.node_id}>{n.title}</option>)}</select></label><button>主机批准并转场</button>
      </form>
    </>}
  </section>
}
