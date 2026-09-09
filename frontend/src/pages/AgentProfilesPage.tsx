import { useEffect, useState } from 'react'
import { api, hostToken } from '../api/session'
import type { AgentProfile, AgentProfileInput } from '../api/agents'

const blank: AgentProfileInput = { role: 'keeper', name: '', background: '', personality: '', goals: '', speaking_style: '', action_tendency: '', model_preset: 'default' }
const fields: { key: 'background' | 'personality' | 'goals' | 'speaking_style' | 'action_tendency'; label: string; max: number }[] = [
  { key: 'background', label: '背景', max: 1000 }, { key: 'personality', label: '性格', max: 1000 },
  { key: 'goals', label: '目标', max: 1000 }, { key: 'speaking_style', label: '说话风格', max: 500 },
  { key: 'action_tendency', label: '行动倾向', max: 500 },
]

export default function AgentProfilesPage() {
  const [profiles, setProfiles] = useState<AgentProfile[]>([])
  const [form, setForm] = useState<AgentProfileInput>({ ...blank })
  const [editingId, setEditingId] = useState<string | null>(null)
  const [concept, setConcept] = useState('')
  const [draft, setDraft] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [modelLabel, setModelLabel] = useState('当前后端预设')
  const refresh = () => api<AgentProfile[]>('/agent-profiles', hostToken()).then(setProfiles)
  useEffect(() => {
    refresh().catch(e => setError(e.message))
    api<{ name: string; provider: string; model: string }[]>('/agent-model-presets', hostToken())
      .then(items => setModelLabel(`${items[0].provider} · ${items[0].model}`)).catch(e => setError(e.message))
  }, [])
  return <>
    <section><h2>Agent 档案</h2><p>为 AI KP 和调查员队友设定角色。模型生成的草稿在确认保存后才成为可绑定的档案。</p>
      <button disabled={busy} onClick={() => { setForm({ ...blank }); setEditingId(null); setDraft(false) }}>新建档案</button>
      <ul className="character-list">{profiles.map(profile => <li key={profile.id}>
        <strong>{profile.name}</strong> · {profile.role === 'keeper' ? 'AI KP' : 'AI 调查员'}
        <p>{profile.personality}</p><button disabled={busy} onClick={() => {
          const { id, created_at, updated_at, ...values } = profile
          void created_at; void updated_at; setForm(values); setEditingId(id); setDraft(false)
        }}>编辑 · {profile.name}</button>
      </li>)}</ul>
    </section>
    {error && <p role="alert">{error}</p>}
    <section><h2>{draft ? '草稿预览与修改' : editingId ? '编辑档案' : '创建档案'}</h2>
      <label>类型<select id="profile-role" value={form.role} disabled={busy} onChange={e => setForm({ ...form, role: e.target.value as AgentProfileInput['role'] })}><option value="keeper">AI KP</option><option value="investigator">AI 调查员</option></select></label>
      {!editingId && <form onSubmit={async e => {
        e.preventDefault(); setBusy(true); setError('')
        try {
          const result = await api<{ draft: AgentProfileInput }>('/agent-profiles/generate-draft', hostToken(), 'POST', { role: form.role, concept })
          setForm(result.draft); setDraft(true)
        } catch (e) { setError(e instanceof Error ? e.message : '生成失败，概念已保留') } finally { setBusy(false) }
      }}><label>简短概念<textarea id="profile-concept" maxLength={2000} value={concept} onChange={e => setConcept(e.target.value)} /></label><button disabled={busy || !concept.trim()}>生成结构化草稿</button></form>}
      {draft && <p role="status">请查看并修改以下内容，再点击“确认保存档案”。</p>}
      <form onSubmit={async e => {
        e.preventDefault(); setBusy(true); setError('')
        try {
          await api(`/agent-profiles${editingId ? `/${editingId}` : ''}`, hostToken(), editingId ? 'PATCH' : 'POST', form)
          await refresh(); setForm({ ...blank }); setEditingId(null); setDraft(false)
        } catch (e) { setError(e instanceof Error ? e.message : '保存失败') } finally { setBusy(false) }
      }}>
        <label>名称<input id="profile-name" value={form.name} maxLength={120} required onChange={e => setForm({ ...form, name: e.target.value })} /></label>
        <div className="field-grid">{fields.map(({ key, label, max }) => <label key={key}>{label}<textarea value={form[key]} maxLength={max} onChange={e => setForm({ ...form, [key]: e.target.value })} /></label>)}</div>
        <label>模型预设<select value={form.model_preset} onChange={() => {}}><option value="default">default · {modelLabel}</option></select></label>
        <button disabled={busy || !form.name.trim()}>{busy ? '处理中…' : '确认保存档案'}</button>
      </form>
    </section>
  </>
}
