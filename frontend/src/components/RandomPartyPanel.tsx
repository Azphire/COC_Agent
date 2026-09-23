import { useEffect, useRef, useState } from 'react'
import { api, hostToken, requestId } from '../api/session'
import { launchApi } from '../api/launch'
import type { PartyBatch, PartyMember } from '../api/launch'
import { charactersApi } from '../api/characters'
import PartyMemberEditor from './PartyMemberEditor'
import { personaFields } from '../api/agents'

type Props = { batchId: string; scopeId: string; count: number; preparationId: string; era: '1920s' | 'modern'; handoutIds?: (string | null)[]; onChange: (batch: PartyBatch) => void; own?: boolean; onAdopt?: (characterId: string) => void; value?: PartyBatch | null }
export default function RandomPartyPanel({ batchId, scopeId, count, preparationId, era, handoutIds, onChange, own, onAdopt, value }: Props) {
  const [localBatch, setBatch] = useState<PartyBatch | null>(null)
  const batch = value === undefined ? localBatch : value
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [elapsed, setElapsed] = useState<number | null>(null)
  const [workingIndex, setWorkingIndex] = useState(0)
  const active = useRef(true)
  const running = useRef(false)
  const callback = useRef(onChange)
  const adoptedCallback = useRef(onAdopt)
  useEffect(() => { callback.current = onChange }, [onChange])
  useEffect(() => { adoptedCallback.current = onAdopt }, [onAdopt])
  useEffect(() => { if (value) queueMicrotask(() => setLoadError('')) }, [value])
  useEffect(() => {
    if (own && batch?.status === 'adopted' && batch.character_ids[0]) adoptedCallback.current?.(batch.character_ids[0])
  }, [own, batch?.status, batch?.character_ids])
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  useEffect(() => {
    charactersApi.rulesets().then(rules => setLabels(Object.fromEntries(rules.flatMap(rule => [...rule.skills, ...rule.occupations].map(item => [item.key, item.display_name]))))).catch(() => {})
  }, [])
  useEffect(() => {
    if (!batchId) return
    let cancelled = false
    const refresh = () => launchApi.party(batchId).then(next => { if (!cancelled) { setBatch(next); setLoadError(''); callback.current(next) } }).catch(e => { if (!cancelled) setLoadError(e.message) })
    void refresh()
    const timer = window.setInterval(refresh, 4000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [batchId])
  function accept(next: PartyBatch) { if (active.current) { setBatch(next); callback.current(next) } }
  async function generate(mode: 'new' | 'resume' | 'reroll', memberIndex?: number) {
    if (running.current) return
    const started = Date.now()
    running.current = true; setBusy(true); setError(''); setElapsed(null); setWorkingIndex(memberIndex ?? batch?.members.find(member => member.status !== 'ready')?.index ?? 0)
    try {
      let next = batch
      if (mode === 'new') {
        const body = { count, preparation_id: preparationId, ruleset_id: 'coc7-character-creation', era, handout_ids: handoutIds }
        const key = `coc.party.create.${scopeId}.${own ? 'own' : 'team'}`
        const fingerprint = JSON.stringify(body)
        let operation: { fingerprint: string; id: string } | null = null
        try { operation = JSON.parse(localStorage.getItem(key) || 'null') } catch { /* A malformed browser cache does not discard a server batch. */ }
        if (operation?.fingerprint !== fingerprint) { operation = { fingerprint, id: requestId() }; localStorage.setItem(key, JSON.stringify(operation)) }
        next = await api<PartyBatch>('/party-batches', hostToken(), 'POST', { request_id: operation!.id, ...body })
      }
      else if (mode === 'reroll' && next) next = await api<PartyBatch>(`/party-batches/${next.id}/reroll`, hostToken(), 'POST', { request_id: requestId(), ...(memberIndex === undefined ? {} : { member_index: memberIndex }) })
      if (!next) return
      accept(next)
      while (active.current && !['ready', 'adopted'].includes(next.status)) {
        setWorkingIndex(next.members.find(member => member.status !== 'ready')?.index ?? 0)
        next = await api<PartyBatch>(`/party-batches/${next.id}/next`, hostToken(), 'POST', { request_id: requestId() })
        accept(next)
        if (next.status === 'failed') break
      }
      if (next.status === 'failed') setError(next.error || '人物文字未完成，数值卡已保留。可继续生成。')
    } catch (e) { setError(e instanceof Error ? e.message : '生成中断；已完成的成员会保留。') }
    finally { running.current = false; if (active.current) { setBusy(false); setElapsed(Math.round((Date.now() - started) / 1000)) } }
  }
  const generationIndex = batch?.members.find(member => member.status === 'generating')?.index ?? batch?.members.find(member => member.status !== 'ready')?.index ?? workingIndex
  return <div className="random-party" data-testid={own ? 'random-own-character' : 'random-party'}>
    <div className="action-row">{!batch ? <button type="button" disabled={busy || !count} onClick={() => void generate('new')}>{own ? '快速生成完整角色' : `随机生成 ${count} 名完整队友`}</button> : <>
      {!['ready', 'adopted'].includes(batch.status) && <button type="button" disabled={busy} onClick={() => void generate('resume')}>继续生成未完成成员</button>}
      {batch.status !== 'adopted' && <button type="button" disabled={busy || batch.status === 'generating'} onClick={() => void generate('reroll')}>全部重抽</button>}
    </>}</div>
    {(busy || batch) && <p role="status" aria-live="polite">{busy || batch?.status === 'generating' ? `正在生成第 ${generationIndex + 1} / ${count} 人的背景与性格…` : `已完成 ${batch?.completed || 0} / ${count} 人`}{elapsed !== null && !busy ? ` · 本次等待 ${elapsed} 秒` : ''}。{batch && `模型累计调用 ${batch.metrics?.model_calls ?? batch.members.reduce((total, member) => total + member.model_calls, 0)} 次。`}</p>}
    {error && <p role="alert">{error}</p>}
    {loadError && <p role="alert">{loadError}</p>}
    {batch && <div className="party-grid">{batch.members.map(member => <PartyPreview key={`${member.index}:${member.reroll_count}`} batchId={batch.id} member={member} labels={labels} disabled={busy || batch.status === 'generating' || batch.status === 'adopted'} onReroll={() => void generate('reroll', member.index)} onChange={accept} />)}</div>}
    {own && batch?.status === 'ready' && <button type="button" disabled={busy} onClick={async () => {
      setBusy(true); setError('')
      try { const adopted = await api<PartyBatch>(`/party-batches/${batch.id}/adopt`, hostToken(), 'POST', { request_id: requestId(), approve_handouts: true }); accept(adopted); if (adopted.character_ids[0]) onAdopt?.(adopted.character_ids[0]) }
      catch (e) { setError(e instanceof Error ? e.message : '采用失败') } finally { setBusy(false) }
    }}>采用为我的角色{handoutIds?.some(Boolean) ? '并确认本人 HO 建卡规则' : ''}</button>}
  </div>
}

function PartyPreview({ batchId, member, labels, disabled, onReroll, onChange }: { batchId: string; member: PartyMember; labels: Record<string, string>; disabled: boolean; onReroll: () => void; onChange: (batch: PartyBatch) => void }) {
  const [editing, setEditing] = useState(false)
  const card = member.character
  return <article className="party-member" data-testid={`party-member-${member.index}`}>
    <h3>{card.name || `调查员 ${member.index + 1}`}</h3><p>{card.age} 岁 · {labels[card.occupation || ''] || card.occupation} · {card.era === 'modern' ? '现代' : '1920 年代'}</p>
    <p>{Object.entries(card.effective_attributes || {}).map(([key, value]) => `${key.toUpperCase()} ${value}`).join(' · ')}</p>
    <p>主要技能：{Object.entries(card.skill_values || {}).filter(([key]) => !['credit_rating', 'own_language', 'dodge'].includes(key)).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([key, value]) => `${labels[key] || key} ${value}`).join('、')}</p>
    <p>信用评级 {card.skill_values?.credit_rating ?? '—'} · 装备：{card.equipment?.map(item => `${item.name} ×${item.quantity}`).join('、') || '无'}</p>
    {member.private_profile ? <p>已分配个人 HO；含秘密的人物背景与人格仅本人和 KP 可见。</p> : personaFields.map(([key, title]) => member.profile?.[key] ? <p key={key}><strong>{title}：</strong>{member.profile[key]}</p> : null)}
    {member.error && <p role="alert">{member.error}</p>}
    {!card.validation?.valid && <p role="alert">{card.validation?.issues.map(issue => issue.message).join('；')}</p>}
    <div className="action-row"><button type="button" disabled={disabled} onClick={onReroll}>重抽此人</button><button type="button" disabled={disabled} onClick={() => setEditing(!editing)}>编辑</button></div>
    {editing && <PartyMemberEditor batchId={batchId} member={member} disabled={disabled} onSaved={batch => { onChange(batch); setEditing(false) }} />}
    <details><summary>完整技能与点数</summary><dl>{Object.entries(card.skill_values || {}).map(([key, value]) => <div key={key}>{labels[key] || key}：{value}</div>)}</dl><p>剩余职业点 {card.remaining_points?.occupation ?? '—'} / 兴趣点 {card.remaining_points?.interest ?? '—'}</p></details>
  </article>
}
