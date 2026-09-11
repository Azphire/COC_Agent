import { useEffect, useState } from 'react'
import { api, hostToken } from '../api/session'
import type { KnowledgeSource } from '../api/knowledge'
import { entityLabels } from '../api/preparation'
import type { Entity, EntityType, Preparation, Relation } from '../api/preparation'
import ModuleStructurePanel from '../components/ModuleStructurePanel'

export default function ModulePreparationPage() {
  const token = hostToken()
  const [sources, setSources] = useState<KnowledgeSource[]>([])
  const [preparations, setPreparations] = useState<Preparation[]>([])
  const [selected, setSelected] = useState('')
  const [sourceId, setSourceId] = useState('')
  const [title, setTitle] = useState('')
  const [pageStart, setPageStart] = useState('')
  const [pageEnd, setPageEnd] = useState('')
  const [section, setSection] = useState('')
  const [entities, setEntities] = useState<Entity[]>([])
  const [relations, setRelations] = useState<Relation[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const current = preparations.find(p => p.id === selected)
  async function refresh(id = selected) {
    setPreparations(await api<Preparation[]>('/module-preparations', token))
    if (id) {
      const [nextEntities, nextRelations] = await Promise.all([
        api<Entity[]>(`/module-preparations/${id}/entities`, token),
        api<Relation[]>(`/module-preparations/${id}/relations`, token),
      ])
      setEntities(nextEntities); setRelations(nextRelations)
    }
  }
  useEffect(() => {
    api<KnowledgeSource[]>('/knowledge/sources', token).then(s => setSources(s.filter(v => v.kind === 'module' && v.chunk_count))).catch(e => setError(e.message))
    api<Preparation[]>('/module-preparations', token).then(setPreparations).catch(e => setError(e.message))
  }, [token])
  useEffect(() => {
    if (!selected) return
    let active = true
    async function load() {
      try {
        const [next, nextEntities, nextRelations] = await Promise.all([
          api<Preparation>(`/module-preparations/${selected}`, token),
          api<Entity[]>(`/module-preparations/${selected}/entities`, token),
          api<Relation[]>(`/module-preparations/${selected}/relations`, token),
        ])
        if (active) { setPreparations(old => old.map(p => p.id === selected ? next : p)); setEntities(nextEntities); setRelations(nextRelations) }
      } catch (e) { if (active) setError(e instanceof Error ? e.message : '加载失败') }
    }
    void load()
    const timer = setInterval(() => void load(), 1800)
    return () => { active = false; clearInterval(timer) }
  }, [selected, token])
  async function command(path: string, body?: unknown, method = 'POST') {
    setError(''); setBusy(true)
    try { const result = await api<Preparation>(path, token, method, body); await refresh(); return result }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败') }
    finally { setBusy(false) }
  }
  return <>
    <section><h2>模组准备工作台</h2><p>选择一段已索引文本，生成草稿，逐项核对证据与公开摘要。批准后可在未开始的房间绑定。</p><p>当前仅支持文字信息；每次最多三批，范围较大时请分段准备。</p></section>
    {error && <p role="alert">{error}</p>}
    <form onSubmit={async e => {
      e.preventDefault()
      const source = sources.find(s => s.source_id === sourceId)
      if (!source) return
      const result = await command('/module-preparations', { source_id: source.source_id, source_hash: source.source_hash, display_title: title || source.title, scope: { page_start: pageStart ? Number(pageStart) : null, page_end: pageEnd ? Number(pageEnd) : null, section: section || null } })
      if (result) { setSelected(result.id); await refresh(result.id) }
    }}><h3>新建准备任务</h3>
      <label>已索引模组<select id="preparation-source" value={sourceId} onChange={e => setSourceId(e.target.value)} required><option value="">选择模组</option>{sources.map(s => <option key={s.source_id} value={s.source_id}>{s.title} · {s.source_hash.slice(0, 12)} · {s.page_count} 页 / {s.chunk_count} 块</option>)}</select></label>
      <label>任务名称<input id="preparation-title" maxLength={120} value={title} onChange={e => setTitle(e.target.value)} placeholder="例如：开场准备" /></label>
      <div className="field-grid"><label>起始页<input id="preparation-page-start" type="number" min={1} value={pageStart} onChange={e => setPageStart(e.target.value)} /></label><label>结束页<input id="preparation-page-end" type="number" min={1} value={pageEnd} onChange={e => setPageEnd(e.target.value)} /></label><label>章节标题包含<input value={section} onChange={e => setSection(e.target.value)} /></label></div>
      <p>页码和章节留空表示整个模组；页码使用来源文件的物理页，文本文件可按章节选择。</p><button disabled={busy || !sourceId}>创建准备任务</button>
    </form>
    <section><label>准备任务<select id="preparation-select" value={selected} onChange={e => setSelected(e.target.value)}><option value="">选择任务</option>{preparations.map(p => <option key={p.id} value={p.id}>{p.display_title} · {p.status} · v{p.version}</option>)}</select></label></section>
    {current && <>
      <ModuleStructurePanel key={current.id} preparationId={current.id} token={token} entities={entities} />
      <section data-testid="preparation-status"><h3>{current.display_title}</h3><p>{current.source_hash.slice(0, 12)} · {current.source?.file_types?.join(' / ') || current.source?.mime_type} · {current.source?.page_count} 页 / {current.source?.chunk_count} 块</p><p>范围：{current.scope.page_start || '起始'}–{current.scope.page_end || '末尾'} {current.scope.section || ''} · 状态：{current.status}</p><p>生成进度 {current.completed_batches}/{current.total_batches} 批 · 模型调用 {current.model_call_count} 次 · 生成 {current.generated_entity_count} · 批准 {current.approved_entity_count} · 拒绝 {current.rejected_entity_count}</p>
        {current.safe_error && <p role="alert">{current.safe_error}</p>}{current.status === 'stale' && <p>来源已变化。已有房间保持原快照；请按当前来源创建新的准备任务。</p>}
        <p>人物准备：{entities.filter(e => e.type === 'npc' && e.status === 'approved').length} 个已批准。请核对当前场景的人物绑定；若范围内没有人物，可继续无人物场景，测试人物请勾选专用标记。</p><div className="action-row"><button disabled={busy || ['extracting', 'approved', 'stale'].includes(current.status)} onClick={() => void command(`/module-preparations/${current.id}/generate`)}>生成实体草稿</button><button disabled={busy || !current.initial_scene_entity_id || current.status === 'extracting' || current.status === 'stale'} onClick={() => void command(`/module-preparations/${current.id}/approve`)}>批准准备版本</button><button onClick={() => void refresh()}>刷新状态</button></div>
        <p>初始场景：{entities.find(e => e.id === current.initial_scene_entity_id)?.title || '尚未设置'}</p><details><summary>最近准备与审阅记录</summary>{current.activity?.map((entry, i) => <p key={i}>{new Date(entry.time).toLocaleString()} · {entry.action} {entry.title}</p>)}</details>
      </section>
      <section><h3>实体草稿审阅</h3>{entities.length === 0 && <p>尚无实体，生成草稿或手动新增。</p>}{entities.map(entity => <EntityEditor key={`${entity.id}:${entity.version}`} entity={entity} token={token} busy={busy} command={command} initial={entity.id === current.initial_scene_entity_id} onInitial={() => void command(`/module-preparations/${current.id}`, { initial_scene_entity_id: entity.id, required_entity_ids: current.required_entity_ids }, 'PATCH')} required={current.required_entity_ids.includes(entity.id)} onRequired={(checked) => {
        if (!current.initial_scene_entity_id) { setError('请先设置初始场景'); return }
        void command(`/module-preparations/${current.id}`, { initial_scene_entity_id: current.initial_scene_entity_id, required_entity_ids: checked ? [...current.required_entity_ids, entity.id] : current.required_entity_ids.filter(id => id !== entity.id) }, 'PATCH')
      }} />)}</section>
      <form onSubmit={async e => {
        e.preventDefault(); const form = e.currentTarget; const data = new FormData(form)
        if (await command('/module-entities', { preparation_id: current.id, type: data.get('type'), title: data.get('title'), public_summary: data.get('public_summary'), keeper_summary: data.get('keeper_summary'), tags: data.get('host_authored_test') ? ['host_authored_test'] : [] })) form.reset()
      }}><h3>主机手动新增</h3><label><input type="checkbox" name="host_authored_test" />仅用于测试的合成人物／实体</label><label>类型<select name="type">{Object.entries(entityLabels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label><label>标题<input name="title" required maxLength={120} /></label><label>公开摘要<textarea name="public_summary" maxLength={1000} /></label><label>主机摘要<textarea name="keeper_summary" maxLength={1600} /></label><button disabled={busy || ['stale', 'extracting'].includes(current.status)}>新增主机草稿</button></form>
      <section><h3>实体关系</h3>{relations.length === 0 && <p>暂无关系。</p>}{relations.map(r => <article key={r.id}><p>{entities.find(e => e.id === r.source_entity_id)?.title} → {r.relation_type} → {entities.find(e => e.id === r.target_entity_id)?.title} · {r.status}</p><p>{r.keeper_note}</p><p>{r.validation_errors.join('、')}</p><button disabled={busy} onClick={() => void command(`/module-relations/${r.id}/approve`)}>批准关系</button><button disabled={busy} onClick={() => void command(`/module-relations/${r.id}/reject`)}>拒绝关系</button></article>)}</section>
    </>}
  </>
}

type EditorProps = { entity: Entity; token: string; busy: boolean; command: (path: string, body?: unknown, method?: string) => Promise<unknown>; initial: boolean; onInitial: () => void; required: boolean; onRequired: (checked: boolean) => void }
function EntityEditor({ entity, token, busy, command, initial, onInitial, required, onRequired }: EditorProps) {
  const [title, setTitle] = useState(entity.title)
  const [type, setType] = useState<EntityType>(entity.type)
  const [keeper, setKeeper] = useState(entity.keeper_summary)
  const [summary, setSummary] = useState(entity.public_summary)
  const [visibility, setVisibility] = useState(entity.initial_visibility)
  const [sanity, setSanity] = useState(JSON.stringify(entity.sanity_effects || [], null, 2))
  const [stats, setStats] = useState(JSON.stringify(entity.check_stats || null, null, 2))
  const [checks, setChecks] = useState(JSON.stringify(entity.suggested_checks, null, 2))
  const [accessPolicy, setAccessPolicy] = useState(entity.reveal_conditions.access_policy || (entity.reveal_conditions.successful_check ? 'requires_check' : entity.reveal_conditions.required_entity_ids.length ? 'requires_condition' : 'automatic'))
  const [conditions, setConditions] = useState(JSON.stringify(entity.reveal_conditions, null, 2))
  const [evidence, setEvidence] = useState<{ evidence_id: string; excerpt: string; source_title: string; physical_page: number | null }[]>([])
  const [error, setError] = useState('')
  const editable = entity.status === 'draft'
  const dirty = stats !== JSON.stringify(entity.check_stats || null, null, 2) || sanity !== JSON.stringify(entity.sanity_effects || [], null, 2) || accessPolicy !== (entity.reveal_conditions.access_policy || (entity.reveal_conditions.successful_check ? 'requires_check' : entity.reveal_conditions.required_entity_ids.length ? 'requires_condition' : 'automatic')) || title !== entity.title || type !== entity.type || keeper !== entity.keeper_summary || summary !== entity.public_summary || visibility !== entity.initial_visibility || checks !== JSON.stringify(entity.suggested_checks, null, 2) || conditions !== JSON.stringify(entity.reveal_conditions, null, 2)
  return <article className="entity-card" data-entity-id={entity.id}>
    <h4>{entityLabels[entity.type]} · {entity.title} {initial && '· 初始场景'}</h4><p>{entity.status} · v{entity.version} · {entity.generated_by === 'model' ? '模型草稿' : '主机创建'} {entity.host_edited && '· 主机已编辑'} · 置信度 {entity.confidence ?? '未提供'}</p>
    <form onSubmit={e => {
      e.preventDefault(); setError('')
      try { void command(`/module-entities/${entity.id}`, { title, type, keeper_summary: keeper, public_summary: summary, initial_visibility: visibility, check_stats: type === 'npc' ? JSON.parse(stats) : null, sanity_effects: JSON.parse(sanity), suggested_checks: JSON.parse(checks), reveal_conditions: { ...JSON.parse(conditions), access_policy: accessPolicy } }, 'PATCH') }
      catch { setError('检定和公开条件必须填写有效 JSON；字段规则由服务器校验。') }
    }}>
      <label>类型<select value={type} disabled={!editable} onChange={e => setType(e.target.value as EntityType)}>{Object.entries(entityLabels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label><label>标题<input value={title} maxLength={120} disabled={!editable} onChange={e => setTitle(e.target.value)} /></label>
      <label>主机摘要<textarea value={keeper} maxLength={1600} disabled={!editable} onChange={e => setKeeper(e.target.value)} /></label><label>公开摘要<textarea aria-label={`公开摘要 ${entity.title}`} value={summary} maxLength={1000} disabled={!editable} onChange={e => setSummary(e.target.value)} /></label>
      <label>访问策略<select value={accessPolicy} disabled={!editable} onChange={e => setAccessPolicy(e.target.value as typeof accessPolicy)}><option value="automatic">直接获取，无需检定</option><option value="requires_check">需要已配置的检定</option><option value="requires_condition">需要满足公开条件</option><option value="host_review">需要主机审阅</option></select></label>
      <label>初始可见性<select value={visibility} disabled={!editable} onChange={e => setVisibility(e.target.value)}><option value="hidden">隐藏，等待游戏中揭示</option><option value="revealed">绑定时公开</option></select></label>
      {type === 'npc' && <details><summary>NPC 对抗数值</summary><p>填写已核对的属性／技能数值与来源，保存并批准后随房间快照冻结。未准备的数值不能用于对抗。</p><label>数值与来源（JSON，null 表示未准备）<textarea value={stats} disabled={!editable} onChange={e => setStats(e.target.value)} /></label><p>示例：{'{"attributes":{"str":60},"skills":{"listen":45},"source":"模组人物表","page":12}'}</p></details>}
      <details><summary>编辑检定与公开条件</summary><p>实体引用填写本任务的实体 ID；留空表示没有额外限制。检定名称使用角色卡技能键。</p><label>SAN 效果（JSON，保存后重新批准）<textarea value={sanity} disabled={!editable} onChange={e => setSanity(e.target.value)} /></label><p>每项填写 id、encounter、trigger（action_target / entity_revealed）、success_loss、failure_loss、source、page、basis、visibility。同时填写 automation（automatic / host_review）、repeat（first_only / host_confirmed）、action_types 与 condition。默认须主机确认、仅首次触发。存在文字条件时等待主机确认；新行动不会自动视为重复遭遇。常数与骰式只接受非负结果；请按来源明确配置。</p><label>建议检定（JSON）<textarea value={checks} disabled={!editable} onChange={e => setChecks(e.target.value)} /></label><label>公开条件（JSON）<textarea value={conditions} disabled={!editable} onChange={e => setConditions(e.target.value)} /></label></details>
      <button disabled={busy || !editable}>保存编辑</button>{dirty && <p>有未保存的修改，请保存后再批准。</p>}
    </form>
    <p>建议检定：{entity.suggested_checks.map(c => `${c.name} (${c.difficulty})`).join('、') || '无'}</p><details><summary>公开条件</summary><pre>{JSON.stringify(entity.reveal_conditions, null, 2)}</pre></details>
    <p>来源：{entity.source_references.map((r, i) => <span key={i}>{r.source_title} {r.physical_page ? `p.${r.physical_page}` : '文本'}；</span>)}</p>
    {entity.validation_errors.length > 0 && <p role="alert">验证失败：{entity.validation_errors.join('、')}</p>}
    <button onClick={async () => { try { setEvidence(await Promise.all(entity.evidence_ids.map(id => api<{ evidence_id: string; excerpt: string; source_title: string; physical_page: number | null }>(`/module-preparations/${entity.preparation_id}/evidence/${id}`, token)))) } catch (e) { setError(e instanceof Error ? e.message : '读取证据失败') } }}>查看有限证据摘录</button>
    {error && <p role="alert">{error}</p>}{evidence.map(e => <blockquote key={e.evidence_id}><p>{e.source_title} · {e.physical_page ? `p.${e.physical_page}` : '文本'}</p><p className="preserve-lines">{e.excerpt}</p></blockquote>)}
    <div className="action-row"><button disabled={busy || dirty || entity.status === 'approved' || !!entity.validation_errors.length} onClick={() => void command(`/module-entities/${entity.id}/approve`)}>批准实体</button><button disabled={busy || entity.status === 'rejected'} onClick={() => void command(`/module-entities/${entity.id}/reject`)}>拒绝实体</button><button disabled={busy || editable} onClick={() => void command(`/module-entities/${entity.id}/draft`)}>返回草稿</button>{entity.type === 'scene' && <button disabled={busy} onClick={onInitial}>设为初始场景</button>}</div>
    {entity.type !== 'scene' && <label><input type="checkbox" checked={required} onChange={e => onRequired(e.target.checked)} />开场必要实体</label>}
  </article>
}
