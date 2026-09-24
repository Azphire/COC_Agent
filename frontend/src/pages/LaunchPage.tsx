import { useEffect, useRef, useState } from 'react'
import HostUnlock from '../components/HostUnlock'
import ModelSettingsPanel from '../components/ModelSettingsPanel'
import RandomPartyPanel from '../components/RandomPartyPanel'
import CharacterCreationPage from './CharacterCreationPage'
import { launchApi } from '../api/launch'
import type { LaunchDraft, LaunchOptions, PartyBatch } from '../api/launch'
import { api, hostToken, requestId } from '../api/session'

type Selection = { draftId: string; moduleId: string; characterId: string; count: number; partyId: string; ownBatchId: string; era: '1920s' | 'modern'; rulesId: string; ownHandout: string; multiplayer: boolean; reservedHumans: number }
const storageKey = 'coc.launch.selection'
const empty: Selection = { draftId: '', moduleId: '', characterId: '', count: 2, partyId: '', ownBatchId: '', era: '1920s', rulesId: '', ownHandout: '', multiplayer: false, reservedHumans: 0 }
function storedSelection(): Selection { try { return { ...empty, ...JSON.parse(localStorage.getItem(storageKey) || '{}') } } catch { return empty } }

export default function LaunchPage({ unlocked, onUnlock }: { unlocked: boolean; onUnlock: () => void }) {
  if (!unlocked) return <><section><h1>开始新游戏</h1><p>首次开团先验证本机主机身份，随后在这里选择模组和队伍。</p></section><HostUnlock onUnlock={onUnlock} /></>
  return <LaunchWizard />
}

function LaunchWizard() {
  const [selection, setSelection] = useState(storedSelection)
  const [options, setOptions] = useState<LaunchOptions | null>(null)
  const [draft, setDraft] = useState<LaunchDraft | null>(null)
  const [party, setParty] = useState<PartyBatch | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [modelOpen, setModelOpen] = useState(false)
  const [readingRules, setReadingRules] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [unspentConfirmed, setUnspentConfirmed] = useState(false)
  const [step, setStep] = useState<1 | 2 | 3>(selection.draftId ? 2 : 1)
  const createRequest = useRef(localStorage.getItem('coc.launch.request') || requestId())
  useEffect(() => { localStorage.setItem(storageKey, JSON.stringify(selection)); localStorage.setItem('coc.launch.request', createRequest.current) }, [selection])
  useEffect(() => {
    launchApi.options().then(async result => { setOptions(result); if (['unconfigured', 'failed'].includes(result.model.state || '')) setModelOpen(true); if (selection.draftId) await restore(selection.draftId) }).catch(e => setError(e.message))
    // Browser state only selects a draft; its choices and progress come from the server.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const module = options?.preparations.find(item => item.id === selection.moduleId)
  const chosen = options?.characters.find(item => item.id === selection.characterId)
  const hasUnspent = !!chosen?.remaining_points && Object.values(chosen.remaining_points).some(value => value !== null && value > 0)
  const teamReady = !selection.partyId && selection.count === 0 || !!party && selection.count === party.count && ['ready', 'adopted'].includes(party.status)
  const maxAI = module?.requirements?.players_verified ? Math.max(0, (module.requirements.maximum_players || 1) - 1 - selection.reservedHumans) : 6
  const countError = module?.requirements?.players_verified && selection.count + 1 + selection.reservedHumans > (module.requirements.maximum_players || 1) ? '本人、AI 与预留真人席位超过模组已核准人数。请先调整人数。' : ''
  const configuredRules = options?.default_rules?.length ? options.default_rules : options?.rules.filter(item => item.source_id === selection.rulesId).map(({ source_id, source_hash }) => ({ source_id, source_hash })) || []
  function update(patch: Partial<Selection>) { setSelection(current => ({ ...current, ...patch })); setNotice('') }
  async function run(action: () => Promise<void>) { setBusy(true); setError(''); try { await action() } catch (e) { setError(e instanceof Error ? e.message : '操作失败；开团草稿已保留。'); if (selection.draftId) await launchApi.draft(selection.draftId).then(setDraft).catch(() => {}) } finally { setBusy(false) } }
  async function persist() {
    if (!draft) throw new Error('请先选择模组')
    const current = await launchApi.draft(draft.id)
    const next = await launchApi.update(draft.id, { version: current.version, character_id: selection.characterId || null, party_batch_id: selection.partyId || current.document.party_batch_id || null, ai_count: selection.count, reserved_humans: selection.reservedHumans, era: selection.era, own_handout: selection.ownHandout || null, rules: configuredRules, handout_acknowledged: module?.handouts.length ? acknowledged || !!draft.document.handout_acknowledged : true, acknowledge_unspent: unspentConfirmed || !!draft.document.acknowledge_unspent })
    setDraft(next); return next
  }
  async function restore(id: string) {
    const next = await launchApi.draft(id)
    const [team, own] = await Promise.all([next.document.party_batch_id ? launchApi.party(next.document.party_batch_id) : null, next.document.own_batch_id ? launchApi.party(next.document.own_batch_id) : null])
    const doc = next.document
    const ownHandout = own && (!doc.character_id || own.character_ids.includes(doc.character_id)) ? own.members[0]?.character.module_handout?.handout_id || '' : doc.own_handout || ''
    setDraft(next); setParty(team)
    setSelection({ ...empty, draftId: id, moduleId: doc.preparation_id, characterId: doc.character_id || own?.character_ids[0] || '', partyId: doc.party_batch_id || '', ownBatchId: doc.own_batch_id || '', count: team?.count ?? doc.ai_count ?? 0, era: team?.era || own?.era || doc.era || '1920s', ownHandout, reservedHumans: doc.reserved_humans || 0, multiplayer: !!doc.reserved_humans, rulesId: doc.rules?.[0]?.source_id || '' })
    setAcknowledged(!!doc.handout_acknowledged); setUnspentConfirmed(!!doc.acknowledge_unspent); setStep(next.room_id ? 3 : 2)
  }
  async function beforeCreate(role: 'party' | 'self') {
    if (countError) throw new Error(countError)
    const saved = await persist()
    return { launch_draft_id: saved.id, launch_draft_version: saved.version, launch_role: role }
  }
  async function checkDraft(id: string) {
    const next = await launchApi.step(id, 'preflight')
    setDraft(next)
    if (next.document.party_batch_id) setParty(await launchApi.party(next.document.party_batch_id))
    return next
  }
  const [acknowledged, setAcknowledged] = useState(false)
  return <div className="launch-wizard">
    <section className="launch-heading"><h1>开始新游戏</h1><ol className="wizard-steps" aria-label="开团步骤">{[[1, '选模组'], [2, '配队伍'], [3, '开始游戏']].map(([number, label]) => <li key={number} aria-current={step === number ? 'step' : undefined}>{number} · {label}</li>)}</ol><p>默认单机；已保存的设置和生成进度会继续使用。</p></section>
    {error && <p role="alert" className="preserve-lines">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!options ? <p role="status">正在读取可玩的模组与当前设置…</p> : <>
      <details open={modelOpen} onToggle={e => setModelOpen(e.currentTarget.open)}><summary>当前模型：{options.model.model || '待配置'} · 设置与修复</summary>{modelOpen && <><ModelSettingsPanel /><button type="button" onClick={() => void run(async () => { setOptions(await launchApi.options()); setNotice('已刷新设置，可继续当前开团草稿。') })}>设置完成，刷新检查</button></>}</details>
      {step === 1 && <section><h2>选择模组</h2>
        {!!options.drafts?.length && !selection.draftId && <details><summary>恢复未完成的开团</summary>{options.drafts.map(saved => <p key={saved.id}><button type="button" onClick={() => void run(async () => {
          await restore(saved.id)
        })}>继续 · {options.preparations.find(item => item.id === saved.document.preparation_id)?.title || '已保存开团'}</button></p>)}</details>}
        <label>已批准可玩的模组<select id="launch-module" value={selection.moduleId} onChange={e => {
        const next = options.preparations.find(item => item.id === e.target.value)
        const maximum = next?.requirements?.players_verified ? Math.max(0, (next.requirements.maximum_players || 1) - 1) : 6
        const minimum = next?.requirements?.players_verified ? Math.max(0, (next.requirements.minimum_players || 1) - 1) : 0
        update({ ...empty, moduleId: e.target.value, characterId: selection.characterId, count: Math.min(6, maximum, Math.max(minimum, 2)), era: next?.requirements?.era === 'modern' ? 'modern' : '1920s', ownHandout: next?.handouts[0]?.id || '' })
        setParty(null); setDraft(null); createRequest.current = requestId()
      }}><option value="">选择模组</option>{options.preparations.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
        {!options.preparations.length && <p>尚无已批准的可玩模组，请在<a href="#/preparations">模组准备</a>中准备或导入一次。</p>}
        {module && <><p className="preserve-lines">{module.public_introduction}</p><p>准备版本 {module.version} · {module.requirements?.source || '旧准备包尚未标注人数与年代，下面的默认值可调整。'}</p>{module.requirements?.note && <p>{module.requirements.note}</p>}
          {(module.requirements?.minimum_players || module.requirements?.maximum_players) && <p>调查员人数：{module.requirements.minimum_players || '—'} 至 {module.requirements.maximum_players || '—'} 人</p>}
        </>}
        {!options.default_rules.length && <label>首次选择规则来源<select id="launch-rules" value={selection.rulesId} onChange={e => update({ rulesId: e.target.value })}><option value="">选择规则来源</option>{options.rules.map(rule => <option key={rule.source_id} value={rule.source_id}>{rule.title}</option>)}</select></label>}
        {!options.rules.length && <div><p>尚未读取本机的规则来源。可以在这里读取已有规则文件后继续。</p><button type="button" disabled={busy || readingRules} onClick={() => void run(async () => { setReadingRules(true); try { const next = await api<LaunchOptions>('/launch/rules/refresh', hostToken(), 'POST', {}); setOptions(next); setNotice(next.rules.length ? '已读取本地规则，选择来源后继续。' : '本地尚无可读取的规则文件，请将规则放入 data/rules 后再次读取。') } finally { setReadingRules(false) } })}>读取本地规则来源</button>{readingRules && <p role="status">正在读取并索引本地规则文件，完成后即可选择；开团选择已保留。</p>}</div>}
        <button disabled={busy || !module || !configuredRules.length} onClick={() => void run(async () => {
          const next = await launchApi.create({ client_request_id: createRequest.current, preparation_id: selection.moduleId, character_id: selection.characterId || null, ai_count: selection.count, reserved_humans: selection.reservedHumans, era: selection.era, own_handout: selection.ownHandout || null, rules: configuredRules })
          setDraft(next); update({ draftId: next.id }); setStep(2)
        })}>下一步：配队伍</button>
      </section>}
      {step >= 2 && <>
        <section><h2>{module?.title || '当前模组'} · 配队伍</h2>
          {draft?.status !== 'started' && <button type="button" disabled={busy || !!draft?.room_id} onClick={() => setStep(1)}>重新选择模组</button>}
          <div className="field-grid"><label>我的角色<select id="launch-character" disabled={busy || !!draft?.room_id} value={selection.characterId} onChange={e => { const card = options.characters.find(item => item.id === e.target.value); update({ characterId: e.target.value, ownHandout: card?.module_handout?.handout_id || '' }); setUnspentConfirmed(false) }}><option value="">选择已确认的角色</option>{options.characters.map(character => <option key={character.id} value={character.id}>{character.name} · {character.age} 岁</option>)}</select></label>
            <label>AI 队友人数<input id="launch-party-count" type="number" min={0} max={maxAI} disabled={busy || !!draft?.room_id || party?.status === 'adopted' || party?.status === 'generating'} value={selection.count} onChange={e => update({ count: Math.max(0, Math.min(6, Number(e.target.value))) })} /></label>
            <label>年代<select id="launch-era" disabled={!!module?.requirements?.era_verified || !!selection.partyId || !!selection.ownBatchId || !!draft?.room_id} value={selection.era} onChange={e => update({ era: e.target.value as Selection['era'] })}><option value="1920s">1920 年代</option><option value="modern">现代</option></select></label>
          </div>
          {chosen && <p>你扮演 {chosen.name}。AI KP 独立负责主持，模组人物不占队友席位。</p>}
          {hasUnspent && <div className="rules-notice"><p>此角色还有未分配的建卡点数：职业 {chosen?.remaining_points?.occupation}、兴趣 {chosen?.remaining_points?.interest}、经历 {chosen?.remaining_points?.experience}。</p><label><input type="checkbox" checked={unspentConfirmed || !!draft?.document.acknowledge_unspent} onChange={e => setUnspentConfirmed(e.target.checked)} />我已核对，确认保留当前卡并放弃这些未分配点数。</label><button type="button" onClick={() => setCreateOpen(!createOpen)}>查看或编辑角色</button>{createOpen && <CharacterCreationPage characterId={selection.characterId} onFinalized={() => void launchApi.options().then(setOptions)} />}</div>}
          {!selection.characterId && <>
            {!!module?.handouts.length && <label>我的 HO<select disabled={!!selection.ownBatchId} value={selection.ownHandout} onChange={e => update({ ownHandout: e.target.value })}><option value="">暂不选择</option>{module.handouts.map(handout => <option key={handout.id} value={handout.id}>{handout.title}</option>)}</select></label>}
            <RandomPartyPanel key={`own-${selection.moduleId}`} beforeCreate={() => beforeCreate('self')} disabled={busy || !!countError} own scopeId={selection.draftId} batchId={selection.ownBatchId} count={1} preparationId={selection.moduleId} era={selection.era} handoutIds={[selection.ownHandout || null]} onChange={batch => { if (batch.id !== selection.ownBatchId) update({ ownBatchId: batch.id }) }} onAdopt={id => { update({ characterId: id }); void launchApi.options().then(setOptions) }} />
            <button type="button" onClick={() => setCreateOpen(!createOpen)}>{createOpen ? '收起建卡' : '手动创建我的角色'}</button>
            {createOpen && <CharacterCreationPage onFinalized={character => { update({ characterId: character.id }); setCreateOpen(false); void launchApi.options().then(setOptions) }} />}
          </>}
          {(selection.count > 0 || !!selection.partyId) && <RandomPartyPanel key={`party-${selection.moduleId}`} beforeCreate={() => beforeCreate('party')} disabled={busy || !!countError} value={party} scopeId={selection.draftId} batchId={selection.partyId} count={selection.count} preparationId={selection.moduleId} era={selection.era} handoutIds={Array.from({ length: selection.count }, (_, index) => module?.handouts.filter(handout => handout.id !== selection.ownHandout)[index]?.id || null)} onChange={batch => { setParty(batch); if (batch.id !== selection.partyId) update({ partyId: batch.id }); if (draft) void launchApi.draft(draft.id).then(setDraft) }} />}
          {!!module?.handouts.length && <label className="acknowledgement"><input type="checkbox" checked={acknowledged || !!draft?.document.handout_acknowledged} onChange={e => setAcknowledged(e.target.checked)} />采用队伍时，一并确认已分配 HO 的建卡要求与规则调整。秘密资料仅对应调查员和 KP 可见。</label>}
          <label className="acknowledgement"><input type="checkbox" disabled={!!draft?.room_id} checked={selection.multiplayer} onChange={e => { const reservedHumans = e.target.checked ? Math.max(1, selection.reservedHumans) : 0; update({ multiplayer: e.target.checked, reservedHumans, ...(!selection.partyId && module?.requirements?.players_verified ? { count: Math.max(0, Math.min(selection.count, (module.requirements.maximum_players || 1) - 1 - reservedHumans)) } : {}) }) }} />邀请朋友加入（朋友自行选卡或提交卡并准备）</label>
          {selection.multiplayer && <label>预留真人席位（本人之外）<input id="launch-reserved-humans" type="number" min={1} max={11} disabled={!!draft?.room_id} value={selection.reservedHumans} onChange={e => update({ reservedHumans: Math.max(1, Math.min(11, Number(e.target.value))) })} /></label>}
          {countError && <p role="alert">{countError}</p>}
          {!draft?.room_id && <button disabled={busy || !!countError || !selection.characterId || !teamReady} onClick={() => void run(async () => { const saved = await persist(); await checkDraft(saved.id); setStep(3) })}>检查队伍并继续</button>}
        </section>
        {step === 3 && <section><h2>采用队伍并开始</h2><p>将采用你的角色和 {selection.count} 名 AI 队友，自动完成本地席位、角色、人格和 KP 的绑定。</p>
          {!!draft?.issues?.length && <ul className="launch-issues">{draft.issues.map((issue, index) => <li key={`${issue.code}-${index}`}><p>{issue.message}</p>{issue.repair === 'model' ? <button type="button" onClick={() => { setModelOpen(true); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>在这里修复模型设置</button> : ['team', 'character', 'agents'].includes(issue.repair || '') || issue.code === 'agent_config' ? draft.room_id ? <button type="button" disabled={busy} onClick={() => void run(async () => { const repaired = await launchApi.step(draft.id, 'repair'); await checkDraft(repaired.id) })}>恢复已采用队伍的绑定并检查</button> : <button type="button" onClick={() => setStep(2)}>返回配队伍修复</button> : issue.repair === 'module' ? draft.room_id ? <p>请恢复已采用版本的来源文件；<a href="#/preparations">查看模组准备</a>。</p> : <button type="button" onClick={() => setStep(1)}>重新选择批准模组</button> : issue.repair === 'invite' ? <p>请朋友在加入的游戏页面自行准备，然后重新检查。</p> : issue.repair === 'rules' ? <label>规则来源<select value={selection.rulesId} onChange={e => update({ rulesId: e.target.value })}><option value="">选择有效来源</option>{options.rules.map(rule => <option key={rule.source_id} value={rule.source_id}>{rule.title}</option>)}</select><button type="button" disabled={!selection.rulesId || busy} onClick={() => void run(async () => { if (!draft) return; const rules = options.rules.filter(rule => rule.source_id === selection.rulesId).map(({ source_id, source_hash }) => ({ source_id, source_hash })); const saved = await launchApi.update(draft.id, { version: draft.version, rules }); await checkDraft(saved.id); setOptions(await launchApi.options()) })}>保存来源并重新检查</button></label> : null}</li>)}</ul>}
          {draft?.invite_code && <p>分享给朋友的邀请码：<code>{draft.invite_code}</code></p>}
          {selection.multiplayer && !draft?.room_id && <button disabled={busy} onClick={() => void run(async () => { const saved = await persist(); setDraft(await launchApi.step(saved.id, 'assemble', { expected_version: saved.version })); setNotice('邀请大厅已保存，朋友加入并准备后可继续开始。') })}>创建邀请大厅</button>}
          {draft?.room_id && draft.status !== 'started' && <p><a href={`#/rooms/${draft.room_id}`}>查看朋友的加入与准备状态</a></p>}
          <div className="action-row"><button disabled={busy || !draft} onClick={() => void run(async () => { const saved = draft?.room_id ? draft : await persist(); if (saved) await checkDraft(saved.id) })}>重新检查</button>
            {draft?.status === 'started' && draft.room_id ? <a className="primary-link" href={`#/rooms/${draft.room_id}`}>继续进入游戏</a> : <button id="launch-start" disabled={busy || !!countError || !draft || !selection.characterId || !teamReady || !!draft.issues?.length || (!!module?.handouts.length && !acknowledged && !draft.document.handout_acknowledged)} onClick={() => void run(async () => {
              if (!draft) return
              const saved = draft.room_id ? draft : await persist()
              const next = await launchApi.step(saved.id, 'start', { expected_version: saved.version, ...(saved.room_revision === undefined ? {} : { expected_room_revision: saved.room_revision }) })
              setDraft(next)
              if (next.status === 'started' && next.room_id) { localStorage.setItem('coc.last-room', next.room_id); localStorage.removeItem(storageKey); localStorage.removeItem('coc.launch.request'); window.location.hash = `#/rooms/${next.room_id}` }
            })}>{busy ? '正在续办开团步骤…' : '一次采用队伍并开始游戏'}</button>}
          </div>
        </section>}
      </>}
    </>}
  </div>
}
