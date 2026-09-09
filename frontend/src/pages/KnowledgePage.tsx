import { useEffect, useState } from 'react'
import { api, hostToken } from '../api/session'
import type { Citation, KnowledgeSource } from '../api/knowledge'

export default function KnowledgePage() {
  const [sources, setSources] = useState<KnowledgeSource[]>([])
  const [query, setQuery] = useState('奖励骰如何判定')
  const [sourceId, setSourceId] = useState('')
  const [results, setResults] = useState<Citation[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const token = hostToken()
  async function refresh() { setSources(await api<KnowledgeSource[]>('/knowledge/sources', token)) }
  useEffect(() => { api<KnowledgeSource[]>('/knowledge/sources', token).then(setSources).catch(e => setError(e.message)) }, [token])
  return <section><h2>本地知识库</h2>
    <p>本地规则与模组 · DOCX / DOC 优先于同名 PDF，另支持 Markdown、TXT。模组原文仅供主机和 KP 检索。</p>
    <button disabled={busy} onClick={async () => { setBusy(true); setError(''); try { await api('/knowledge/index', token, 'POST', { kind: 'all' }); await refresh() } catch (e) { setError(e instanceof Error ? e.message : '索引失败') } finally { setBusy(false) } }}>{busy ? '正在索引…' : '增量索引本地文件'}</button>
    {error && <p role="alert">{error}</p>}
    {sources.map(source => <article key={source.source_id}><h3>{source.title}</h3>
      <p>{source.kind} · {source.edition} · {source.source_hash.slice(0, 12)} · {source.extraction_status}</p>
      <p>物理页 {source.extracted_pages}/{source.page_count} · {source.chunk_count} chunks · {source.indexed_at && new Date(source.indexed_at).toLocaleString()}</p>
      {source.error_summary && <p>{source.error_summary}</p>}
      <details><summary>文件提取与跳过情况</summary><ul>{source.files.map((file, index) => <li key={index}>{file.name} · {file.type} · {file.status}{file.page_count != null && ` · ${file.extracted_pages}/${file.page_count} 页`}</li>)}</ul></details>
    </article>)}
    <form onSubmit={async e => { e.preventDefault(); setError(''); try { const source = sources.find(s => s.source_id === sourceId); setResults(await api<Citation[]>('/knowledge/query', token, 'POST', { query, kind: source?.kind === 'module' ? 'module' : 'rules', sources: source ? [{ source_id: source.source_id, source_hash: source.source_hash }] : [] })) } catch (e) { setError(e instanceof Error ? e.message : '查询失败') } }}>
      <h3>测试查询（仅主机）</h3><label>来源<select value={sourceId} onChange={e => setSourceId(e.target.value)}><option value="">全部公开规则</option>{sources.filter(s => s.chunk_count).map(s => <option value={s.source_id} key={s.source_id}>{s.title}</option>)}</select></label>
      <label>查询<input value={query} maxLength={500} onChange={e => setQuery(e.target.value)} required /></label><button>检索</button>
    </form>
    {results.map(e => <article key={e.evidence_id}><strong>{e.source_title} · {e.page_kind === 'pdf' ? 'PDF' : e.page_kind === 'word' ? 'Word' : '文本'} {e.physical_page && `p.${e.physical_page}`}</strong><p>rank {e.rank} · score {e.score} · {e.evidence_id}</p><p className="preserve-lines">{e.excerpt}</p></article>)}
  </section>
}
