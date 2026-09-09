import { useState } from 'react'
import { CharacterApiError, charactersApi } from '../../api/characters'

export default function JsonTransfer({ characterId, exportDisabled = false }: {
  characterId?: string; exportDisabled?: boolean
}) {
  const [json, setJson] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function exportJson() {
    setBusy(true); setError('')
    try {
      const document = await charactersApi.export(characterId!)
      const text = JSON.stringify(document, null, 2)
      setJson(text)
      const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
      const anchor = window.document.createElement('a')
      anchor.href = url; anchor.download = `character-${characterId}.json`; anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) { setError(error instanceof Error ? error.message : '导出失败') }
    finally { setBusy(false) }
  }
  async function importJson() {
    setBusy(true); setError('')
    try {
      const imported = await charactersApi.import(JSON.parse(json))
      window.location.hash = `#/characters/${imported.id}`
    } catch (error) {
      setError(error instanceof SyntaxError ? 'JSON 格式不正确'
        : error instanceof CharacterApiError ? [error.message, ...error.issues.map(issue => `${issue.field}：${issue.message}`)].join('；') : '导入失败')
    } finally { setBusy(false) }
  }
  return <section>
    <h2>JSON 导入导出</h2>
    <p className="hint">导入会创建新 ID 的草稿并重新计算；随机记录标为 imported。请再次检查后最终确认。</p>
    <label>选择 JSON 文件
      <input type="file" accept=".json,application/json" disabled={busy} onChange={async event => {
        const file = event.target.files?.[0]
        if (file) {
          if (file.size > 2_000_000) { setError('文件不能超过 2 MB'); return }
          try { setJson(await file.text()); setError('') } catch { setError('无法读取文件') }
        }
      }} />
    </label>
    <label>角色 JSON
      <textarea id="character-json" rows={6} value={json} disabled={busy}
        onChange={event => setJson(event.target.value)} placeholder="粘贴角色导出的 JSON，或选择文件" />
    </label>
    <div className="action-row">
      {characterId && <button type="button" onClick={exportJson} disabled={busy || exportDisabled}>导出 JSON</button>}
      <button type="button" onClick={importJson} disabled={busy || !json.trim()}>导入为新草稿</button>
    </div>
    {error && <p role="alert">{error}</p>}
  </section>
}
