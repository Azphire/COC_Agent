import { useEffect, useState } from 'react'
import CharacterCreationPage from './pages/CharacterCreationPage'
import CharacterListPage from './pages/CharacterListPage'
import SystemStatusPage from './pages/SystemStatusPage'
import './App.css'

function App() {
  const [route, setRoute] = useState(window.location.hash || '#/characters')
  useEffect(() => {
    const navigate = () => setRoute(window.location.hash || '#/characters')
    window.addEventListener('hashchange', navigate)
    return () => window.removeEventListener('hashchange', navigate)
  }, [])
  const characterId = route.startsWith('#/characters/') ? route.slice('#/characters/'.length) : undefined
  return (
    <main>
      <h1>CoC 跑团 Agent</h1>
      <nav aria-label="主导航">
        <a href="#/status" aria-current={route === '#/status' ? 'page' : undefined}>系统状态</a>
        <a href="#/characters" aria-current={route === '#/characters' ? 'page' : undefined}>角色列表</a>
        <a href="#/create" aria-current={route === '#/create' ? 'page' : undefined}>创建角色</a>
      </nav>
      {route === '#/status' ? <SystemStatusPage />
        : route === '#/create' || characterId
          ? <CharacterCreationPage key={route} characterId={characterId} />
          : <CharacterListPage />}
    </main>
  )
}

export default App
