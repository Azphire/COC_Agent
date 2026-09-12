import { useEffect, useState } from 'react'
import { api } from '../api/session'
import type { Result, Room } from '../api/rooms'
import { entityLabels } from '../api/preparation'
import type { Entity, EntityType, HostReview, Preparation } from '../api/preparation'

type Props = { room: Room; token: string; acceptRoom: (room: Room) => void }
export default function HostEntityPanel({ room, token, acceptRoom }: Props) {
  const [preparations, setPreparations] = useState<Preparation[]>([])
  const [selected, setSelected] = useState('')
  const [reviews, setReviews] = useState<HostReview[]>([])
  const [entities, setEntities] = useState<Entity[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [interaction, setInteraction] = useState('')
  const prefix = `/rooms/${room.id}`
  useEffect(() => {
    let active = true
    const timer = setTimeout(async () => {
      try {
        const [tasks, requests, nextEntities] = await Promise.all([
          api<Preparation[]>('/module-preparations', token), api<HostReview[]>(prefix + '/review-requests', token), api<Entity[]>(prefix + '/host-entities', token),
        ])
        if (active) { setPreparations(tasks.filter(p => p.status === 'approved')); setReviews(requests); setEntities(nextEntities) }
      } catch (e) { if (active) setError(e instanceof Error ? e.message : '读取失败') }
    }, 100)
    return () => { active = false; clearTimeout(timer) }
  }, [room.revision, prefix, token])
  async function command(path: string, body?: unknown, method = 'POST') {
    setBusy(true); setError('')
    try { const result = await api<Result>(prefix + path, token, method, body); acceptRoom(result.room) }
    catch (e) { setError(e instanceof Error ? e.message : '操作失败') }
    finally { setBusy(false) }
  }
  return <section data-testid="host-entity-panel"><h2>模组准备与主机审阅</h2><p><a href="#/preparations">打开模组准备工作台</a></p>{error && <p role="alert">{error}</p>}
    {room.status === 'lobby' && <form onSubmit={e => { e.preventDefault(); void command('/module-preparation', { preparation_id: selected }, 'PATCH') }}><label>批准版本<select id="room-preparation" value={selected} onChange={e => setSelected(e.target.value)}><option value="">选择已批准准备任务</option>{preparations.map(p => <option value={p.id} key={p.id}>{p.display_title} · v{p.version} · {p.source_hash.slice(0, 12)}</option>)}</select></label><button disabled={busy || !selected}>绑定准备版本</button></form>}
    {room.game?.preparation && <p>当前快照 v{room.game.preparation.version} · {room.game.preparation.source_hash.slice(0, 12)}</p>}
    {room.game?.preparation && <details><summary>确认原稿物品／事件交互</summary><p>引用实际玩家行动，核对原文情境后确认。服务器仍会检查位置、物品、事件条件和已结算的骰点。</p><form onSubmit={e => {
      e.preventDefault(); const fields = new FormData(e.currentTarget); const [entityId, interactionId] = interaction.split(':')
      void command('/module-action', { entity_id: entityId, interaction_id: interactionId, source_event_seq: Number(fields.get('seq')), evidence_quote: fields.get('quote'), reason: fields.get('reason'), mode: 'confirm' })
    }}><label>已发现对象的交互<select required value={interaction} onChange={e => setInteraction(e.target.value)}><option value="">选择原稿交互</option>{entities.filter(e => e.state !== 'hidden').flatMap(e => (e.interactions || []).map(r => <option key={`${e.id}:${String(r.id)}`} value={`${e.id}:${String(r.id)}`}>{e.title} · {String(r.instruction)}</option>))}</select></label><label>玩家行动事件序号<input name="seq" type="number" min={1} required /></label><label>引用玩家原话<textarea name="quote" maxLength={1000} required /></label><label>校对情境与依据<input name="reason" maxLength={500} required /></label><button disabled={busy || !interaction}>核对并确认交互</button></form></details>}
    {reviews.filter(r => r.status === 'pending').map(review => <ReviewEditor key={review.id} review={review} busy={busy} onResolve={(decision, body) => void command(`/review-requests/${review.id}/${decision}`, body)} />)}
    <details><summary>批准实体与隐藏状态 · 仅主机</summary>{entities.map(entity => <article className="entity-card" key={entity.id}><h4>{entityLabels[entity.type]} · {entity.title} · {entity.state}</h4><p>主机摘要：{entity.keeper_summary}</p><p>公开摘要：{entity.public_summary || '尚未填写，不能公开'}</p>{entity.state === 'hidden' ? <button disabled={busy || !entity.public_summary} onClick={() => void command(`/entities/${entity.id}/reveal`)}>主机公开实体</button> : <form onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); void command(`/entities/${entity.id}/correct`, { public_summary: data.get('summary'), reason: data.get('reason') }) }}><label>修正后的公开摘要<textarea name="summary" defaultValue={entity.public_summary} required maxLength={1000} /></label><label>公开修正说明<input name="reason" required maxLength={400} /></label><button disabled={busy}>追加公开修正</button></form>}</article>)}</details>
    {reviews.some(r => r.status !== 'pending') && <details><summary>已处理审阅</summary>{reviews.filter(r => r.status !== 'pending').map(r => <p key={r.id}>{r.proposed_title} · {r.status} · {r.host_response}</p>)}</details>}
  </section>
}

function ReviewEditor({ review, busy, onResolve }: { review: HostReview; busy: boolean; onResolve: (decision: string, body: unknown) => void }) {
  const [summary, setSummary] = useState(review.proposed_public_summary)
  const [type, setType] = useState<EntityType>(review.entity_type)
  const [response, setResponse] = useState('')
  const edited = summary !== review.proposed_public_summary || type !== review.entity_type
  return <article className="entity-card host-review" data-review-id={review.id}><h3>[主机审阅] {review.proposed_title}</h3><p>请求：{review.request_type} · cycle {review.cycle_id.slice(0, 8)}</p><p>主机理由：{review.keeper_reason}</p>
    <label>实体类型<select value={type} onChange={e => setType(e.target.value as EntityType)}>{Object.entries(entityLabels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label><label>拟公开摘要<textarea aria-label="审阅公开摘要" value={summary} maxLength={1000} onChange={e => setSummary(e.target.value)} /></label><label>主机处理说明<input value={response} maxLength={800} onChange={e => setResponse(e.target.value)} /></label>
    {review.evidence.map(e => <details key={e.evidence_id}><summary>证据：{e.source_title} {e.physical_page ? `p.${e.physical_page}` : '文本'}</summary><p className="preserve-lines">{e.excerpt}</p></details>)}
    <button disabled={busy || !summary.trim()} onClick={() => onResolve(edited ? 'edit-and-approve' : 'approve', { public_summary: summary, entity_type: type, host_response: response })}>{edited ? '编辑后批准并恢复' : '批准并恢复'}</button><button disabled={busy} onClick={() => onResolve('reject', { host_response: response })}>拒绝并恢复</button>
  </article>
}
