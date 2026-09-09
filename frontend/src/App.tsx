import { useEffect, useState } from 'react'
import CharacterCreationPage from './pages/CharacterCreationPage'
import CharacterListPage from './pages/CharacterListPage'
import SystemStatusPage from './pages/SystemStatusPage'
import RoomsPage from './pages/RoomsPage'
import AgentProfilesPage from './pages/AgentProfilesPage'
import KnowledgePage from './pages/KnowledgePage'
import ModulePreparationPage from './pages/ModulePreparationPage'
import HostUnlock from './components/HostUnlock'
import { hostToken } from './api/session'
import './App.css'

function App() {
  const [route, setRoute] = useState(window.location.hash || '#/characters')
  const [unlocked, setUnlocked] = useState(!!hostToken())
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
        <a href="#/rooms" aria-current={route.startsWith('#/rooms') ? 'page' : undefined}>多人房间</a>
        <a href="#/agents" aria-current={route === '#/agents' ? 'page' : undefined}>Agent 档案</a>
        <a href="#/knowledge" aria-current={route === '#/knowledge' ? 'page' : undefined}>知识库</a>
        <a href="#/preparations" aria-current={route === '#/preparations' ? 'page' : undefined}>模组准备</a>
        {unlocked && <button onClick={() => { sessionStorage.removeItem('coc.host'); setUnlocked(false) }}>锁定主机</button>}
      </nav>
      {route.startsWith('#/rooms') ? <RoomsPage roomId={route.startsWith('#/rooms/') ? route.slice(8) : undefined} unlocked={unlocked} onUnlock={() => setUnlocked(true)} />
        : !unlocked ? <HostUnlock onUnlock={() => setUnlocked(true)} />
        : route === '#/status' ? <SystemStatusPage />
        : route === '#/agents' ? <AgentProfilesPage />
        : route === '#/knowledge' ? <KnowledgePage />
        : route === '#/preparations' ? <ModulePreparationPage />
        : route === '#/create' || characterId
          ? <CharacterCreationPage key={route} characterId={characterId} />
          : <CharacterListPage />}
    </main>
  )
}

export default App
