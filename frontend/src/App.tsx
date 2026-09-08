import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'

type Health = {
  status: string
  database: string
  model_provider: string
}

type ModelStatus = {
  provider: string
  model: string
  available: boolean
}

type ConnectionStatus = '连接中' | '已连接' | '已断开' | '连接失败'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const wsUrl = import.meta.env.VITE_WS_URL || 'ws://127.0.0.1:8000/ws'

function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState('')
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null)
  const [modelError, setModelError] = useState('')
  const [connection, setConnection] = useState<ConnectionStatus>('连接中')
  const [message, setMessage] = useState('')
  const [echo, setEcho] = useState<unknown>(null)
  const [messageError, setMessageError] = useState('')
  const socketRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 4000)

    async function checkModel() {
      try {
        const response = await fetch(`${apiBaseUrl}/api/model/status`, {
          signal: controller.signal,
        })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const result: ModelStatus = await response.json()
        if (active) setModelStatus(result)
      } catch {
        if (active) setModelError('无法取得模型状态')
      } finally {
        window.clearTimeout(timeout)
      }
    }

    void checkModel()
    return () => {
      active = false
      controller.abort()
      window.clearTimeout(timeout)
    }
  }, [])

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 5000)

    async function checkHealth() {
      try {
        const response = await fetch(`${apiBaseUrl}/api/health`, {
          signal: controller.signal,
        })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const result: Health = await response.json()
        if (active) setHealth(result)
      } catch (error) {
        if (active) {
          setHealthError(
            error instanceof Error && error.name !== 'AbortError'
              ? `无法取得健康状态：${error.message}`
              : '健康检查超时，请确认后端已启动',
          )
        }
      } finally {
        window.clearTimeout(timeout)
      }
    }

    void checkHealth()

    let socket: WebSocket | null = null
    try {
      socket = new WebSocket(wsUrl)
      socketRef.current = socket
      socket.onopen = () => {
        if (active) setConnection('已连接')
      }
      socket.onclose = () => {
        if (active) setConnection((previous) => previous === '连接失败' ? previous : '已断开')
      }
      socket.onerror = () => {
        if (active) setConnection('连接失败')
      }
      socket.onmessage = (event) => {
        if (!active) return
        try {
          const result = JSON.parse(event.data)
          if (result?.type === 'echo') {
            setEcho(result)
            setMessageError('')
          } else if (result?.type === 'error') {
            setMessageError(result.data?.message || '后端返回错误')
          }
        } catch {
          setMessageError('后端返回了无法解析的消息')
        }
      }
    } catch {
      queueMicrotask(() => {
        if (active) setConnection('连接失败')
      })
    }

    return () => {
      active = false
      controller.abort()
      window.clearTimeout(timeout)
      socketRef.current = null
      if (socket?.readyState === WebSocket.CONNECTING) {
        socket.addEventListener('open', () => socket?.close(), { once: true })
      } else {
        socket?.close()
      }
    }
  }, [])

  function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const socket = socketRef.current
    if (!message.trim()) return
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setMessageError('WebSocket 尚未连接，请启动后端后刷新页面')
      return
    }
    try {
      socket.send(JSON.stringify({ type: 'test', text: message.trim() }))
      setMessageError('')
    } catch {
      setMessageError('发送失败，请确认连接后重试')
    }
  }

  return (
    <main>
      <h1>CoC 跑团 Agent</h1>
      <p className="description">开发环境连接测试</p>

      <section aria-labelledby="status-heading">
        <h2 id="status-heading">服务状态</h2>
        <div role="status">
          <p>后端健康状态：{healthError || (health ? health.status : '检查中')}</p>
          {health && <p>SQLite：{health.database}</p>}
          <p>WebSocket：{connection}</p>
          <p>模型状态：{modelError || (modelStatus
            ? `${modelStatus.provider} / ${modelStatus.model} · ${modelStatus.available ? '可用' : '未就绪'}`
            : '检查中')}</p>
        </div>
        <p className="hint">模型状态仅检查服务连接和模型是否已下载。</p>
        <p className="hint">启动后端或修改连接配置后，刷新页面重新连接。</p>
      </section>

      <form onSubmit={sendMessage}>
        <label htmlFor="test-message">测试消息</label>
        <div className="message-row">
          <input
            id="test-message"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="输入一条测试消息"
            autoComplete="off"
          />
          <button type="submit" disabled={connection !== '已连接' || !message.trim()}>
            发送测试消息
          </button>
        </div>
        {messageError && <p role="alert">{messageError}</p>}
      </form>

      <section aria-labelledby="echo-heading">
        <h2 id="echo-heading">后端 Echo JSON</h2>
        <pre aria-live="polite">{echo === null ? '等待发送测试消息…' : JSON.stringify(echo, null, 2)}</pre>
      </section>
    </main>
  )
}

export default App
