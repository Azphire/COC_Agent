import { useEffect, useState } from 'react'
import { api, hostToken } from '../api/session'

type Provider = 'ollama' | 'openai'
type Configuration = { provider: Provider; model: string; base_url: string; api_key_set: boolean; output_mode: 'json_object' | 'json_schema' }
type Saved = Configuration & { revision: number; configurations: Partial<Record<Provider, Configuration>> }
type Status = { provider: string; model: string; state: 'unconfigured' | 'unverified' | 'available' | 'failed'; error: string | null; busy: { kind: string; room_id?: string; status?: string; stage?: string }[] }
const labels = { unconfigured: '未配置', unverified: '未验证', available: '可用', failed: '失败' }
const empty = (provider: Provider): Configuration => ({ provider, model: '', base_url: provider === 'ollama' ? 'http://127.0.0.1:11434/v1/' : '', api_key_set: false, output_mode: 'json_object' })

export default function ModelSettingsPanel() {
  const [saved, setSaved] = useState<Saved | null>(null)
  const [form, setForm] = useState<Configuration>(empty('ollama'))
  const [key, setKey] = useState('')
  const [status, setStatus] = useState<Status | null>(null)
  const [models, setModels] = useState<string[]>([])
  const [catalogueError, setCatalogueError] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [working, setWorking] = useState(false)
  const [test, setTest] = useState<unknown>(null)
  const refresh = () => api<Status>('/model/status', hostToken()).then(setStatus)
  const loadModels = () => api<{ models: string[]; error: string | null }>('/model/ollama-models', hostToken())
    .then(result => { setModels(result.models); setCatalogueError(result.error || '') })

  useEffect(() => {
    let active = true
    api<Saved>('/model/config', hostToken()).then(result => { if (active) { setSaved(result); setForm(result) } }).catch(e => { if (active) setError(e.message) })
    const poll = () => api<Status>('/model/status', hostToken()).then(result => { if (active) setStatus(result) }).catch(e => { if (active) setError(e.message) })
    void poll()
    api<{ models: string[]; error: string | null }>('/model/ollama-models', hostToken()).then(result => {
      if (active) { setModels(result.models); setCatalogueError(result.error || '') }
    }).catch(e => { if (active) setCatalogueError(e.message) })
    const timer = window.setInterval(poll, 4000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  return <section aria-labelledby="model-heading">
    <h2 id="model-heading">当前模型设置</h2>
    <p role="status">实际生效：{status ? `${status.provider} / ${status.model || '未填写'} · ${labels[status.state]}` : '读取中…'}</p>
    {status?.error && <p role="alert">{status.error}</p>}
    <p>KP、队友、档案和准备生成共用 default。刷新状态不发起推理；“试运行”会向当前模型发送一次结构化任务，必要时修复一次，API 可能计费。</p>
    {!!status?.busy.length && <div role="status"><strong>未完成任务：完成或取消回合后才能切换。</strong><ul>{status.busy.map((task, i) => <li key={i}>
      {task.kind} {task.status} {task.stage} {task.room_id && <a href={`#/rooms/${task.room_id}`}>打开房间</a>}
    </li>)}</ul></div>}
    <form onSubmit={async event => {
      event.preventDefault(); setWorking(true); setError(''); setNotice(''); setTest(null)
      try {
        const result = await api<Saved>('/model/config', hostToken(), 'POST', { provider: form.provider, model: form.model, base_url: form.base_url, output_mode: form.output_mode, api_key: key })
        setSaved(result); setForm(result); setKey(''); setNotice('已保存并生效；重启后恢复此配置。'); await refresh()
      } catch (e) { setError(e instanceof Error ? e.message : '保存失败'); await refresh().catch(() => {}) }
      finally { setWorking(false) }
    }}>
      <fieldset disabled={working || !saved}>
        <label>模型服务<select id="model-provider" value={form.provider} onChange={event => {
          const provider = event.target.value as Provider
          setForm(saved?.configurations[provider] || empty(provider)); setKey(''); setNotice('')
        }}><option value="ollama">本机 Ollama</option><option value="openai">OpenAI 兼容 API</option></select></label>
        {form.provider === 'ollama' ? <>
          <label>本机模型<select id="model-name" value={form.model} onChange={event => setForm({ ...form, model: event.target.value })}>
            <option value="">选择已安装模型</option>
            {form.model && !models.includes(form.model) && <option value={form.model}>{form.model}（未在列表中）</option>}
            {models.map(model => <option key={model}>{model}</option>)}
          </select></label>
          <button type="button" onClick={() => loadModels().catch(e => setCatalogueError(e.message))}>刷新本机模型</button>
          {catalogueError && <p>{catalogueError}；可切换到 API 或继续车卡。</p>}
        </> : <>
          <label>API 服务地址<input id="model-url" type="url" value={form.base_url} placeholder="https://平台地址/v1/" onChange={event => setForm({ ...form, base_url: event.target.value })} /></label>
          <label>模型名<input id="model-name" maxLength={200} value={form.model} onChange={event => setForm({ ...form, model: event.target.value })} /></label>
          <label>API 密钥（{form.api_key_set ? '已设置，留空保留' : '未设置'}）<input id="model-key" type="password" autoComplete="new-password" value={key} onChange={event => setKey(event.target.value)} /></label>
          <label>结构化输出<select id="model-output-mode" value={form.output_mode} onChange={event => setForm({ ...form, output_mode: event.target.value as Configuration['output_mode'] })}>
            <option value="json_object">JSON 模式（后端校验完整规则）</option><option value="json_schema">严格 JSON Schema（平台需支持）</option>
          </select></label>
        </>}
        <button type="submit" disabled={!!status?.busy.length}>保存并切换</button>
      </fieldset>
    </form>
    <div className="message-row">
      <button disabled={working} onClick={() => refresh().catch(e => setError(e.message))}>刷新模型状态</button>
      <button disabled={working || !saved || !!status?.busy.length || status?.state === 'unconfigured'} onClick={async () => {
        setWorking(true); setError(''); setNotice('正在试运行已保存的模型…')
        try { setTest(await api('/model/test', hostToken(), 'POST', {})); await refresh(); setNotice('试运行结束，结果见下方。') }
        catch (e) { setError(e instanceof Error ? e.message : '试运行失败') }
        finally { setWorking(false) }
      }}>试运行已保存模型</button>
    </div>
    {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
    {test !== null && <details open><summary>结构化试运行记录</summary><pre>{JSON.stringify(test, null, 2)}</pre></details>}
  </section>
}
