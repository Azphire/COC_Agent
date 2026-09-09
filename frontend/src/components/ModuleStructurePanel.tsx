import { useEffect, useState } from 'react'
import { api } from '../api/session'
import { nodeTypes } from '../api/moduleStructure'
import type { ModuleStructure, SceneTransition, StructureNode } from '../api/moduleStructure'
import type { Entity } from '../api/preparation'

type Props = { preparationId: string; token: string; entities: Entity[] }
export default function ModuleStructurePanel({ preparationId, token, entities }: Props) {
  const prefix = `/module-preparations/${preparationId}/structure`
  const [structure, setStructure] = useState<ModuleStructure | null>(null)
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<{ blocks: { block_id: string; text: string; truncated: boolean }[] } | null>(null)
  const [suggestions, setSuggestions] = useState<{ entity_id: string; node_id: string; source_hash: string; reasons: string[] }[]>([])
  async function refresh() { setStructure(await api<ModuleStructure>(prefix, token)) }
  useEffect(() => {
    let active = true
    api<ModuleStructure>(prefix, token).then(s => { if (active) setStructure(s) }).catch(() => { if (active) setStructure(null) })
    return () => { active = false }
  }, [prefix, token])
  async function command(path: string, body?: unknown, method = 'POST') {
    setBusy(true); setError('')
    try { await api(prefix + path, token, method, body); await refresh(); return true }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败'); return false }
    finally { setBusy(false) }
  }
  const node = structure?.nodes.find(n => n.node_id === selected)
  const scenes = structure?.nodes.filter(n => n.included && n.approved_type === 'scene') || []
  const approved = entities.filter(e => e.status === 'approved')
  return <section data-testid="module-structure"><h3>文档结构</h3>
    <p>按原始顺序查看目录，核对场景和公开摘要后批准。原文预览最多 1200 字。</p>
    {error && <p role="alert">{error}</p>}
    <button disabled={busy} onClick={() => void command('/build')}>构建 / 校验 ModuleIR</button>
    {structure && <>
      <p>{structure.extraction_method} · {structure.node_count} 节点 / {structure.block_count} blocks · {structure.approved_snapshot_id ? '结构已批准' : '结构待批准'} {structure.stale && '· 来源已变化'}</p>
      <details><summary>结构版本与警告（{structure.warnings.length}）</summary><p>{structure.structure_version} · {structure.source_hash.slice(0, 12)}</p>{structure.warnings.map((w, i) => <p key={i}>{w}</p>)}</details>
      <div className="structure-workspace"><nav aria-label="文档目录树"><Tree nodeId={structure.root_node_id} structure={structure} select={id => { setSelected(id); setPreview(null) }} /></nav>
      <div>{node && <form key={`${node.node_id}:${node.title}:${node.parent_node_id}:${node.approved_type}:${node.included}`} onSubmit={async e => {
        e.preventDefault(); const data = new FormData(e.currentTarget)
        await command(`/nodes/${node.node_id}`, { title: data.get('title'), approved_type: data.get('type'), included: data.get('included') === 'on', public_title: data.get('public_title'), public_summary: data.get('public_summary'), keeper_summary: data.get('keeper_summary'), linked_node_ids: data.getAll('linked_nodes'), ...(node.parent_node_id ? { parent_node_id: data.get('parent') } : {}) }, 'PATCH')
      }}><h4>校正节点</h4><p>{node.heading_path.join(' / ')}</p><p>原层级 {node.original_depth} · 当前层级 {node.depth} · {node.detection_source} · 置信度 {node.confidence ?? '未知'} · 页 {node.source_position.physical_page ?? '无可靠物理页码'} · 段落 {node.source_position.paragraph_start}</p>
        {node.confidence !== null && node.confidence < 0.8 && <p role="status">低置信度，请核对标题与上级节点。</p>}
        <label>显示标题<input name="title" defaultValue={node.title} required maxLength={240} /></label>
        <label>批准类型<select name="type" defaultValue={node.approved_type || node.detected_type}>{Object.entries(nodeTypes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        {node.parent_node_id && <label>父节点<select name="parent" defaultValue={node.parent_node_id}>{structure.nodes.filter(n => n.node_id !== node.node_id).map(n => <option key={n.node_id} value={n.node_id}>{n.heading_path.join(' / ')}</option>)}</select></label>}
        <label><input type="checkbox" name="included" defaultChecked={node.included} />纳入运行结构</label>
        <label>场景公开标题<input name="public_title" defaultValue={node.public_title} maxLength={120} /></label>
        <label>场景公开摘要<textarea name="public_summary" defaultValue={node.public_summary} maxLength={1000} /></label>
        <label>主机短摘要<textarea name="keeper_summary" defaultValue={node.keeper_summary} maxLength={1000} /></label>
        <label>显式关联节点<select name="linked_nodes" multiple defaultValue={node.linked_node_ids}>{structure.nodes.filter(n => n.node_id !== node.node_id && n.included).map(n => <option key={n.node_id} value={n.node_id}>{n.title}</option>)}</select></label>
        <button disabled={busy}>保存节点校正</button>
        <button type="button" disabled={busy || !node.included} onClick={() => void command(`/nodes/${node.node_id}`, { initial_scene: true }, 'PATCH')}>{structure.initial_scene_node_id === node.node_id ? '当前 initial scene' : '标记为初始场景'}</button>
        <button type="button" onClick={async () => { try { setPreview(await api(prefix + `/nodes/${node.node_id}`, token)) } catch (e) { setError(String(e)) } }}>受限原文预览</button>
        {preview?.blocks.map(b => <blockquote key={b.block_id} className="preserve-lines">{b.text}{b.truncated && '…'}</blockquote>)}
      </form>}</div></div>
      <form onSubmit={e => { e.preventDefault(); const d = new FormData(e.currentTarget); void command('/entity-bindings', { entity_id: d.get('entity'), node_id: d.get('node'), source_hash: structure.source_hash }) }}><h4>绑定已批准实体</h4>
        <label>实体<select name="entity">{approved.map(e => <option key={e.id} value={e.id}>{e.type} · {e.title}</option>)}</select></label>
        <label>场景<select name="node">{scenes.map(n => <option key={n.node_id} value={n.node_id}>{n.title}</option>)}</select></label>
        <button disabled={busy || !approved.length || !scenes.length}>确认绑定</button>
        <button type="button" onClick={async () => { try { const r = await api<{ suggestions: typeof suggestions }>(prefix + '/entity-bindings', token); setSuggestions(r.suggestions) } catch (e) { setError(String(e)) } }}>查看来源匹配建议</button>
      </form>
      {suggestions.map((s, i) => <p key={i}>{entities.find(e => e.id === s.entity_id)?.title} → {structure.nodes.find(n => n.node_id === s.node_id)?.title} · {s.reasons.join(' / ')} <button disabled={busy} onClick={() => void command('/entity-bindings', { entity_id: s.entity_id, node_id: s.node_id, source_hash: s.source_hash })}>主机确认</button></p>)}
      {structure.entity_bindings.map(b => <p key={b.binding_id}>{entities.find(e => e.id === b.entity_id)?.title} → {structure.nodes.find(n => n.node_id === b.node_id)?.title} <button disabled={busy} onClick={() => void command(`/entity-bindings/${b.binding_id}`, undefined, 'DELETE')}>解除绑定</button></p>)}
      <h4>批准的场景转换</h4>
      {structure.transitions.map(t => <TransitionEditor key={t.transition_id} transition={t} scenes={scenes} entities={approved} busy={busy} save={body => command(`/transitions/${t.transition_id}`, body, 'PATCH')} remove={() => command(`/transitions/${t.transition_id}`, undefined, 'DELETE')} />)}
      <TransitionEditor scenes={scenes} entities={approved} busy={busy} save={body => command('/transitions', body)} />
      <form onSubmit={e => { e.preventDefault(); const d = new FormData(e.currentTarget); void command('/approve', { incomplete: d.get('incomplete') === 'on' }) }}>
        <p>初始场景：{structure.nodes.find(n => n.node_id === structure.initial_scene_node_id)?.title || '未设置'}。批准将接受纳入节点的当前类型和层级，固定实体绑定与转换。</p>
        <label><input name="incomplete" type="checkbox" defaultChecked={structure.incomplete} />结构内容不完整，允许 KP 主动请求全模组检索补充</label>
        <button disabled={busy || structure.stale || !structure.initial_scene_node_id}>批准结构快照</button>
      </form>
    </>}
  </section>
}

function Tree({ nodeId, structure, select }: { nodeId: string; structure: ModuleStructure; select: (id: string) => void }) {
  const node = structure.nodes.find(n => n.node_id === nodeId)!
  return <details open={node.depth < 2}><summary><button onClick={() => select(nodeId)}>{node.title}</button> · {nodeTypes[node.approved_type || node.detected_type]} · {node.block_count} 块 / {node.entity_count} 实体 {node.included ? '' : '· 排除'} {node.confidence !== null && node.confidence < 0.8 && '· 待核对'}</summary>
    <div className="structure-children">{node.child_ids.map(id => <Tree key={id} nodeId={id} structure={structure} select={select} />)}</div>
  </details>
}

function TransitionEditor({ transition, scenes, entities, busy, save, remove }: { transition?: SceneTransition; scenes: StructureNode[]; entities: Entity[]; busy: boolean; save: (body: unknown) => Promise<boolean>; remove?: () => Promise<boolean> }) {
  return <details><summary>{transition ? '编辑转换' : '添加转换'} {transition && `${scenes.find(n => n.node_id === transition.source_scene_node_id)?.title} → ${scenes.find(n => n.node_id === transition.target_scene_node_id)?.title}`}</summary><form onSubmit={e => {
    e.preventDefault(); const d = new FormData(e.currentTarget)
    void save({ source_scene_node_id: d.get('source'), target_scene_node_id: d.get('target'), transition_type: d.get('type'), condition_summary: d.get('condition'), required_revealed_entity_ids: d.getAll('required'), required_event_types: String(d.get('events')).split(',').map(v => v.trim()).filter(Boolean), approved: d.get('approved') === 'on' })
  }}>
    <label>起点<select name="source" defaultValue={transition?.source_scene_node_id}>{scenes.map(n => <option key={n.node_id} value={n.node_id}>{n.title}</option>)}</select></label>
    <label>终点<select name="target" defaultValue={transition?.target_scene_node_id}>{scenes.map(n => <option key={n.node_id} value={n.node_id}>{n.title}</option>)}</select></label>
    <label>类型<select name="type" defaultValue={transition?.transition_type || 'normal'}><option value="normal">普通</option><option value="conditional">有条件</option><option value="host_only">仅主机</option></select></label>
    <label>条件摘要<input name="condition" defaultValue={transition?.condition_summary} maxLength={600} /></label>
    <label>必须已公开的实体<select name="required" multiple defaultValue={transition?.required_revealed_entity_ids || []}>{entities.map(e => <option key={e.id} value={e.id}>{e.title}</option>)}</select></label>
    <label>需要的事件类型（逗号分隔）<input name="events" defaultValue={transition?.required_event_types.join(',')} /></label>
    <label><input name="approved" type="checkbox" defaultChecked={transition?.approved} />主机批准此转换</label>
    <button disabled={busy || scenes.length < 2}>保存转换</button>{remove && <button type="button" disabled={busy} onClick={() => void remove()}>移除转换</button>}
  </form></details>
}
