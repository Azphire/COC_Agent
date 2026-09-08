# CoC 跑团 Agent

Windows 原生运行的本地主机型 CoC 5 跑团 Agent。目标是由一名玩家在本地启动主机，支持单人游戏和其他玩家通过局域网浏览器加入；模型可选择 Ollama 或外部兼容 API。

当前支持 FastAPI 健康检查、异步 SQLite 连接、WebSocket JSON echo、模型状态显示，以及统一模型适配层的普通生成、流式文本、结构化输出和工具调用解析。启动、健康检查和单元测试不触发模型推理；真实模型检查由本地脚本单独执行。

## 环境

- Git、uv；后端限定 Python `>=3.12,<3.13`，虚拟环境位于 `backend/.venv`。
- Node.js `>=22.12`、npm。前端来自当前官方 Vite React TypeScript 模板，详见 [Vite 入门文档](https://vite.dev/guide/)。
- Ollama 可选，连接测试不需要安装 Ollama 或下载模型。

初始环境：Git `2.48.1.windows.1`、系统 Python `3.12.7`、uv `0.12.0`、Node.js `22.14.0`、npm `10.9.2`。项目虚拟环境使用 Python `3.12.7`；另检测到 uv 管理的 Python `3.12.13`。

## 安装与启动

以下命令在仓库根目录的 PowerShell 中执行。首次使用时复制配置，保留已有本地设置：

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
if (!(Test-Path frontend/.env)) { Copy-Item frontend/.env.example frontend/.env }
uv sync --directory backend --locked
npm --prefix frontend ci
```

若 uv 没有可用的 Python 3.12，先执行 `uv python install 3.12`。无需修改系统 Python 或使用全局 pip。

分别在两个终端启动：

```powershell
uv run --directory backend python -m app.main
```

```powershell
npm --prefix frontend run dev
```

打开 `http://localhost:5173`。页面应显示后端 `ok`、SQLite `ok`、WebSocket 已连接。输入文本并点击“发送测试消息”，即可查看 echo JSON。使用 `Ctrl+C` 分别停止两端。

后端默认地址为 `http://127.0.0.1:8000`；`python -m app.main` 会读取 `.env` 的 `APP_HOST` 和 `APP_PORT`。修改配置后重启对应服务并刷新页面。前端固定使用 5173 端口，端口占用时会报错，避免自动切换后与 CORS 配置不一致。

## 配置与模型模式

后端固定读取**仓库根目录** `.env`，进程环境变量优先。`DATA_DIR`、`DATABASE_URL` 中的文件路径和 `CHECKPOINT_DB_PATH` 均以 `backend/` 为相对路径基准，解析为绝对路径，因此 `../data` 总是指向仓库的 `data/`，不依赖启动目录。

Ollama 本地模式的默认配置：

```dotenv
MODEL_PROVIDER=ollama
MODEL_BASE_URL=http://127.0.0.1:11434/v1/
MODEL_NAME=qwen3:8b
MODEL_API_KEY=ollama
```

外部 OpenAI 兼容 API 模式的配置示例：

```dotenv
MODEL_PROVIDER=openai
MODEL_BASE_URL=https://your-provider.example/v1
MODEL_NAME=your-model-name
MODEL_API_KEY=
```

外部服务以后也通过同一适配层调用，使用时填写实际地址、模型名和密钥；示例域名和模型名只是占位。本轮不调用外部 API，本地检查脚本会拒绝外部 provider。

前端单独读取 `frontend/.env`：

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_WS_URL=ws://127.0.0.1:8000/ws
```

`VITE_` 变量会暴露给浏览器，只用于连接地址；模型 API Key 仅放在后端 `.env`。两份 `.env` 都已忽略，`.env.example` 可提交。

## 本地模型

使用 Windows 原生 Ollama，安装包来自官方项目：

```powershell
winget install --id Ollama.Ollama -e --source winget
ollama --version
ollama pull qwen3:8b
ollama list
Invoke-RestMethod http://127.0.0.1:11434/api/version
```

若新安装后当前终端尚未刷新 PATH，可使用默认位置 `$env:LOCALAPPDATA\Programs\Ollama\ollama.exe`，或重新打开终端。安装后优先使用 Ollama 后台程序，不在已有实例运行时再次启动 `ollama serve`。详见 [Ollama Windows 安装说明](https://docs.ollama.com/windows)。

模型默认存储在用户目录 `%USERPROFILE%\.ollama\models`，不放入仓库，不修改全局 `OLLAMA_MODELS`。下载前确认该磁盘至少有 15GB 空闲空间。示例为 `qwen3:8b`；更换模型时只需下载目标模型并修改根目录 `.env` 中的 `MODEL_NAME`，随后重启后端。

根目录 `.env` 使用上一节的 Ollama 配置。`MODEL_API_KEY=ollama` 是 SDK 所需的非空占位值，Ollama 会忽略它，参见[官方兼容接口说明](https://docs.ollama.com/api/openai-compatibility)。可选 `MODEL_TIMEOUT_SECONDS=120` 用于限制请求等待时间。

Ollama 保持默认的 `127.0.0.1:11434` 本机监听，不将其改为 `0.0.0.0`，也不开放该端口的入站规则。访问顺序为：玩家浏览器 → FastAPI → 模型适配层 → 本机 Ollama。浏览器只请求后端 `/api/model/status`，只收到 `provider`、`model`、`available`；状态接口读取模型列表，不提交 Prompt，不返回 Ollama 地址、目录或密钥。外部 provider 当前报告未就绪，不进行外部连通测试。

在后端目录运行一次真实检查：

```powershell
cd backend
uv run python scripts/check_local_model.py
ollama ps
```

脚本先以空 Prompt 加载配置中的模型并报告耗时，再依次检查短中文回复、流式文本、Pydantic JSON 校验和 `roll_dice(100, 1)` 工具参数。它不会执行骰子、下载模型或连接外部 provider。首次加载的计时与后续短请求计时分开记录；如果模型已加载，会明确标注。

适配层使用 `openai.AsyncOpenAI`，无内部会话历史，SDK 自动重试设为 0。Ollama 请求关闭思考输出以缩短本轮验证；普通响应返回文本、工具调用及可选结构化对象。流式模式仅支持文本，工具及 JSON 校验使用非流式模式，避免丢失工具参数或跳过完整 JSON 校验。调用方在结束后关闭模型客户端，提前结束流时关闭异步流。

## 局域网连接测试

假设主机局域网 IP 为 `192.168.1.100`，修改根目录 `.env`：

```dotenv
APP_HOST=0.0.0.0
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://192.168.1.100:5173
```

修改 `frontend/.env`，让其他玩家的浏览器连接主机地址：

```dotenv
VITE_API_BASE_URL=http://192.168.1.100:8000
VITE_WS_URL=ws://192.168.1.100:8000/ws
```

重启后端，前端改用 `npm --prefix frontend run dev -- --host 0.0.0.0`。其他玩家打开 `http://192.168.1.100:5173`。请替换为主机实际 IP；若 Windows 防火墙提示，在可信的专用网络上允许对应服务访问。

当前局域网功能仅验证各浏览器独立连接与 echo，尚无房间、广播、玩家身份或权限控制。

## 目录与依赖

```text
backend/
  app/
    main.py, config.py         # 应用启动与配置
    api/                      # 健康检查与 WebSocket
    models/                   # 统一接口、OpenAI 兼容适配器与 factory
    persistence/database.py   # SQLAlchemy 异步 SQLite
    agents/, character/, rules/, memory/, rooms/  # 预留空包
  scripts/check_local_model.py # 四项真实本地模型检查
  tests/                      # 原有测试及离线模型单元测试
  pyproject.toml, uv.lock, .python-version
frontend/                     # React / TypeScript / Vite
data/
  rules/                      # 用户自行放置本地规则文件
  modules/                    # 用户自行放置本地模组
  saves/                      # 预留存档目录
  logs/                       # 预留日志导出目录
```

后端依赖：FastAPI、Uvicorn standard、Pydantic Settings、SQLAlchemy asyncio、aiosqlite、LangGraph、LangGraph SQLite Checkpointer、OpenAI Python SDK、HTTPX、PyMuPDF、python-multipart。开发依赖：pytest、pytest-asyncio、Ruff。

前端保留 Vite 模板的 React、React DOM、TypeScript、Vite、React 插件、类型声明和 Oxlint。使用原生 WebSocket 和普通 CSS。

SQLite 默认为 `data/game.db`，启动时通过 `SELECT 1` 验证连接，暂不创建业务表。`data/agent_checkpoints.db` 仅预留配置，尚未初始化 checkpointer。数据内容、数据库文件及其附属文件均不提交，各数据目录用 `.gitkeep` 保留。没有下载规则书或模组。

尚未实现：KP Agent、AI 队友、随机/购点车卡、CoC 规则工具、模组检索、分层记忆、存档读档、私密信息和 Log 导出。

## 验证

```powershell
cd backend
uv run ruff check .
uv run pytest
cd ../frontend
npm run lint
npm run build
```

原有两项测试仍使用临时 SQLite 文件：健康接口返回 200 和 `status=ok`；WebSocket 收到 `connected` 事件并完成一次 JSON echo。新增测试验证模型 factory、未知 provider、普通响应和工具参数解析，以及模型存在、缺失、离线时的状态接口。模型调用全部使用 mock，pytest 不要求 Ollama 在线，也不改动本地游戏数据库。

后端运行时，也可在 PowerShell 执行 `Invoke-RestMethod http://127.0.0.1:8000/api/health`。预期结果：

```json
{"status":"ok","database":"ok","model_provider":"ollama"}
```

WebSocket 连接后返回 `{"type":"connected","data":{"message":"WebSocket connected"}}`；发送任意 JSON 后返回 `{"type":"echo","data":原始JSON}`。健康检查中的模型供应商只代表配置，不表示模型可用。
