import type { Room, RoomEvent } from '../api/rooms'
import type { Citation } from '../api/knowledge'
import { nodeLabels, resultLabels } from '../api/agents'

export default function RoomTimelineEvent({ event, room }: { event: RoomEvent; room: Room }) {
  const p = event.payload
  const binding = room.game?.bindings.find(b => b.member_id === event.actor_member_id)
  const member = room.members.find(m => m.id === event.actor_member_id)
  const isKP = event.type === 'keeper.narration' || event.type === 'agent.needs_host_ruling'
  const teammate = ['agent.spoke', 'agent.action_proposed'].includes(event.type)
  const system = event.type.startsWith('check.') || event.type === 'agent.cycle_changed' || event.type === 'clue.revealed'
  const actor = String(p.actor_name || (isKP || teammate ? binding?.name : member?.display_name) || '系统')
  const role = event.type.startsWith('rules.') ? '规则问答' : event.type === 'npc.spoke' ? 'NPC' : event.type.startsWith('review.') ? '主机审阅' : event.type.startsWith('entity.') ? '线索' : event.type === 'scene.updated' ? '场景' : event.type.startsWith('check.') ? '检定' : event.type === 'agent.cycle_changed' ? 'Agent' : isKP ? 'KP' : teammate ? 'AI队友' : system ? '系统' : member?.controller_type === 'agent' ? 'AI队友' : member ? '真人' : '系统'
  const cycle = String(p.cycle_id || '').slice(0, 8)
  const textTypes = ['rules.question', 'rules.answered', 'chat.message', 'action.submitted', 'keeper.narration', 'npc.spoke', 'agent.spoke', 'agent.action_proposed', 'module.completed', 'agent.needs_host_ruling']
  const result = p.result as { total: number; level: string; passed: boolean } | undefined
  const labels: Record<string, string> = { running: '进行中', waiting_for_roll: '等待检定', completed: '完成', failed: '失败', cancelled: '已取消' }
  return <li data-event-seq={event.seq} data-actor-type={role} data-controller={isKP || teammate ? 'agent' : system ? 'system' : member?.controller_type || 'system'}>
    <small>#{event.seq} [{new Date(event.occurred_at).toLocaleTimeString()}] [{role}{!system && actor !== '系统' ? `：${actor}` : ''}] {cycle && `[${cycle}]`} {event.visibility !== 'public' && '私密'}</small>
    {textTypes.includes(event.type) ? <p className="preserve-lines">{event.type === 'agent.spoke' ? '发言：' : event.type === 'agent.action_proposed' ? '行动：' : ''}{String(p.text)}</p>
      : event.type === 'action.clarification_requested' ? <p>需要澄清：{String(p.question)}</p>
      : event.type === 'check.requested' ? <p>{(p.sanity as { origin?: string } | undefined)?.origin === 'automatic' ? '遭遇自动触发' : p.sanity ? '主机确认' : 'KP 请求'}“{String(p.display_name || p.name)}”检定 · {room.members.find(m => m.id === p.target_member_id)?.display_name}</p>
      : ['check.rolled', 'check.luck_spent', 'check.push_requested', 'check.push_reviewed', 'check.consequence_pending', 'check.consequence_applied'].includes(event.type) ? <p>{String(p.display_text)}</p>
      : event.type === 'check.resolved' ? <p>{p.display_text ? String(p.display_text) : <>1D100={result?.total}，结果：{resultLabels[result?.level || '']} · {result?.passed ? '通过' : '未通过'}</>}</p>
      : event.type === 'clue.revealed' ? <p>公开线索：{String(p.title)} · {String(p.content)}</p>
      : event.type === 'entity.revealed' || event.type === 'entity.corrected' ? <p>{event.type === 'entity.corrected' ? '公开修正' : '公开发现'}：{String(p.title)} · {String(p.public_summary)}</p>
      : event.type === 'review.waiting' || event.type === 'review.status' ? <p>{String(p.text)}</p>
      : event.type === 'agent.cycle_changed' ? <p>cycle {labels[String(p.status)] || String(p.status)} · {nodeLabels[String(p.current_node)] || String(p.current_node)}{p.status === 'completed' && `，模型调用 ${p.call_count ?? '—'} 次`}{p.safe_error ? ` · ${p.safe_error}` : ''}</p>
      : event.type === 'scene.updated' ? <p>场景：{String(p.scene_title)} · {String(p.scene_summary)}</p>
      : event.type === 'dice.rolled' ? <p>{String(p.reason)} · {String(p.expression)} → [{(p.dice as number[]).join(', ')}] {Number(p.modifier) >= 0 ? '+' : ''}{String(p.modifier)} = <strong>{String(p.total)}</strong></p>
      : <details><summary>{event.type}</summary><pre>{JSON.stringify(p, null, 2)}</pre></details>}
    {p.check_notice ? <p>{String(p.check_notice)}</p> : null}
    {(p.citations as Citation[] | undefined)?.map(c => <details className="rule-citation" key={c.evidence_id}><summary>《{c.source_title}》{c.page_kind === 'pdf' ? 'PDF' : c.page_kind === 'word' ? 'Word' : '文本'} {c.physical_page ? `p.${c.physical_page}` : ''}</summary><p>{c.edition} {c.source_version} {c.section} {c.page_label && `· 页标签 ${c.page_label}`}</p><p className="preserve-lines">{c.excerpt}</p></details>)}
  </li>
}
