import { useEffect, useState } from 'react'
import { charactersApi } from '../api/characters'
import type { Character } from '../api/characters'
import JsonTransfer from '../components/character/JsonTransfer'

export default function CharacterListPage() {
  const [characters, setCharacters] = useState<Character[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    charactersApi.list().then(result => { if (active) setCharacters(result) })
      .catch(error => { if (active) setError(error.message) })
    return () => { active = false }
  }, [])
  return <>
    <section>
      <h2>角色列表</h2>
      <a href="#/create">创建角色</a>
      {error && <p role="alert">{error}</p>}
      {!characters && !error && <p>正在读取角色…</p>}
      {characters?.length === 0 && <p>还没有角色。创建第一张角色卡，或导入 JSON 存档。</p>}
      {characters && characters.length > 0 && <ul className="character-list">{characters.map(character => <li key={character.id}>
        <a href={`#/characters/${character.id}`}>{character.name || '未命名角色'}</a>
        <span>{character.status === 'draft' ? '草稿' : '已确认'} · {character.creation_mode === 'random' ? '随机' : '购点'}</span>
        <small>{character.ruleset_id} · {new Date(character.updated_at).toLocaleString()}</small>
      </li>)}</ul>}
    </section>
    <JsonTransfer />
  </>
}
