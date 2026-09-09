# CoC 跑团 Agent

Windows 原生运行的本地主机型 CoC 第七版跑团 Agent。目标是由一名玩家在本地启动主机，支持单人游戏和其他玩家通过局域网浏览器加入；模型可选择 Ollama 或外部兼容 API。

当前支持 FastAPI 健康检查、异步 SQLite 连接、WebSocket JSON echo、模型状态显示，以及统一模型适配层的普通生成、流式文本、结构化输出和工具调用解析。启动、健康检查和单元测试不触发模型推理；真实模型检查由本地脚本单独执行。

支持数据驱动的第七版车卡：随机／购点创建、年龄调整、属性与派生值计算、职业／兴趣技能分配、草稿持久化、最终确认及 JSON 导入导出。全部车卡计算由后端确定性代码完成，不使用模型。

第二批已加入持久化多人房间、主机与远程成员认证、本地真人与 Agent 占位席位、角色快照分配、ready、服务端聊天／掷骰、WebSocket 实时同步和重连、场景与运行时资源、手动存读档及 JSONL／Markdown 日志。**本批没有接入任何 Agent 推理**。完整接口和边界见 [多人协议 v1](docs/multiplayer-protocol.md)。

## 第七版角色创建

打开页面后先解锁主机，即可进入“角色列表”，导航可切换“系统状态”“创建角色”和“多人房间”。远程玩家直接进入多人房间，通过邀请码加入。选择规则集、模式，填写姓名及年龄，然后创建草稿。随机模式一次生成整组属性；购点模式实时预估剩余点数，保存后的后端余额、派生值和校验结果为准。超支或越界可以保存为草稿，但不能最终确认。保存并通过完整校验后，点击“最终确认”；已确认的角色只读。

规则文件位于 `backend/app/rules/definitions/`：

- `coc7_character_creation.yaml`：当前默认启用，`verification_status: verified`，指已核对本批实现的车卡子集。依据用户本地的《克苏鲁的呼唤第七版规则书1907》和《克苏鲁的呼唤第七版调查员手册1.20》，文件名、PDF页码和公式边界见同目录 [SOURCES.md](backend/app/rules/definitions/SOURCES.md)。不再使用 CoC 5 配置。
- `development_character_creation.yaml`：保留为离线测试 fixture，`verification_status: unverified`；只含少量程序测试示例，界面明确提示不代表第七版规则。

第七版当前提供八项属性、教授和图书馆管理员两种职业，以及27项技能／专业。随机模式使用3d6或2d6+6后乘5；购点使用规则书3.7方案四的**460点可选规则**，八项基础属性总和必须为460，范围15–90，INT/SIZ至少40，购点EDU至少15。幸运独立掷骰，不占属性池。规则配置使用 Pydantic 校验和安全 YAML 读取；公式仅接受封闭运算，没有 Python `eval`／`exec`。

创建前选择15–89岁年龄；生成后锁定年龄，避免重复获取幸运或教育增强检定。按年龄分配STR/SIZ或STR/CON/DEX扣减，其他固定扣减由后端执行。教育增强按顺序比较当时EDU并封顶99；每轮检定骰和条件增长骰一并保存，失败时增长骰不生效。购点编辑复用已保存的骰子，不重新掷骰。属性编辑区显示年龄调整前值和后端已保存的调整后值；HP、MP、SAN、MOV、DB、体格及技能基础值使用调整后值。

职业点（当前两种职业均为EDU×4）和兴趣点（INT×2）分别记账；技能值为基础值加两类投入。信用评级须满足职业范围，母语基础值为EDU，闪避为DEX半值，创建时禁止给克苏鲁神话技能加点。未分配技能点在确认时放弃。当前不启用75技能上限、属性重骰、经历包等其他可选方案；没有逐项重骰入口。

JSON 可通过页面下载、粘贴或选择文件导入，导出含 `schema_version: 1`、导出时间、角色和规则集版本。导入会创建**新的草稿ID**，记录 `original_id`，将掷骰记录标记为 `imported`，重新计算属性调整、派生值、余额及合法性，不能覆盖原角色。导入记录仅验证内部一致性，不作为外部掷骰真实性证明。导入后需要再次最终确认。

API：`GET /api/character-rulesets`、`GET /api/character-rulesets/{id}`；`GET /api/characters`、`POST /api/characters/random`、`POST /api/characters/point-buy`、`POST /api/characters/import`；`GET/PATCH /api/characters/{id}`、`POST /api/characters/{id}/finalize`、`GET /api/characters/{id}/export`。PATCH和finalize必须携带当前整数`version`，旧版本返回409；无效请求字段返回422，不存在的角色返回404。没有角色删除接口。角色库接口及 `/docs`、`/openapi.json` 需要 `Authorization: Bearer <主机密钥>`；规则集元数据保持公开。直接从地址栏访问 API 文档不携带该请求头，可使用本地 HTTP 客户端查看 schema。

刷新页面或重启后可继续读取草稿和确认后的角色；每次读取保留原始骰子结果。尚未覆盖全部职业／技能专业、90岁及以上年龄、背景、装备、资金明细和完整战斗。

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
uv run --directory backend python scripts/host_key.py
```

若 uv 没有可用的 Python 3.12，先执行 `uv python install 3.12`。无需修改系统 Python 或使用全局 pip。

分别在两个终端启动：

```powershell
uv run --directory backend python -m app.main
```

```powershell
npm --prefix frontend run dev
```

运行以下命令在本机查看主机管理密钥，并在页面“主机解锁”中输入（不要向远程玩家分享）：

```powershell
uv run --directory backend python scripts/host_key.py --show
```

打开 `http://127.0.0.1:5173` 创建角色，或切换到“系统状态”查看后端 `ok`、SQLite `ok`、WebSocket 已连接。输入文本并点击“发送测试消息”，即可查看 echo JSON。使用 `Ctrl+C` 分别停止两端。仅本机开发模式下，前后端均默认监听 `127.0.0.1`。

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

前端 API 使用同源 `/api`，WebSocket 从 `window.location` 推导 `/ws` 和 `/ws/rooms/{id}`。`frontend/vite.config.ts` 将 HTTP 和 WebSocket 转发到主机本机的 8000 端口。旧 `frontend/.env` 中的 `VITE_API_BASE_URL` 和 `VITE_WS_URL` 不再使用，无需为每台玩家设备配置地址。生产构建若另行托管，也必须提供相同路径的反向代理。

根 `.env` 的 `HOST_ADMIN_TOKEN` 由 `scripts/host_key.py` 初始化：保留其他配置，仅缺少密钥时生成至少 32 字节随机值，默认不打印。主机密钥最多保存在浏览器 sessionStorage。模型 API Key 仅放在后端 `.env`，不得写入任何 `VITE_` 变量。两份 `.env` 都已忽略，`.env.example` 可提交。

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

## 局域网主机模式

首版只支持 **一个 Uvicorn worker**。在仓库根目录分别打开两个终端：

```powershell
uv run --directory backend uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

```powershell
npm --prefix frontend run dev -- --host 0.0.0.0
```

只有这个显式的前端启动命令监听 `0.0.0.0`；后端继续在回环地址，由 Vite 统一转发玩家请求。无需对局域网直接开放 8000，也无需逐个添加玩家 CORS 地址。后端自定义端口时同步修改 Vite 代理目标。

在主机运行 `ipconfig`，查看正在使用的以太网／无线网卡 IPv4；也可运行：

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } | Select-Object InterfaceAlias,IPAddress
```

例如实际 IP 为 `192.168.1.100`，其他玩家访问 `http://192.168.1.100:5173/#/rooms`。Windows 防火墙可能需要在可信专用网络放行 Node/Vite 的 TCP 5173；不要开放 Ollama 的 11434。上述是可信局域网的最小认证，不是生产级公网安全方案。

1. 主机解锁、创建房间，将邀请码分享给玩家。邀请码仅创建／重新生成时显示，数据库只存哈希；刷新后可重新生成，旧码立即失效，已有成员仍能重连。
2. 主机从已最终确认角色中发布调查员；玩家输入邀请码与显示名加入，选择空闲角色并 ready。主机可增加本地真人与 Agent 占位席位，分配角色并代这些席位 ready。主机管理身份不用选卡；自己扮演调查员时另建本地真人席位。
3. 所有活动玩家分配角色并 ready 后开始。Agent 显示“尚未接入自动行动”。双方发送消息、掷骰；私密消息只由本人和主机读取。
4. 主机运行中或暂停时可存档，仅暂停时可读档。载入前有确认框；恢复场景、资源和分配，保留后续历史事件和当前凭据，房间继续暂停。已离开的成员不会被读档复活。
5. 网络断开自动重连并补发遗漏事件；刷新后从持久化房间恢复。远程重连 token 按房间存在浏览器 localStorage，离开时清除。主机可下载全部日志，玩家只能下载有权限的日志。

停止时在两个服务终端分别按 `Ctrl+C`；只关闭浏览器不会停止服务。验证脚本的临时 Chrome、Vite、FastAPI 均由脚本在 finally 中关闭，端口随后检查释放。不要批量终止用户其他 Python／Node／Chrome 进程。

Ollama 始终保持 `127.0.0.1:11434`，不向局域网或公网直接开放。公网联机、Tailscale、Cloudflare Tunnel 和远程角色上传留到后续批次。

## 目录与依赖

```text
backend/
  app/
    main.py, config.py         # 应用启动与配置
    api/                      # 角色、规则集、健康、模型状态与 WebSocket
    models/                   # 统一接口、OpenAI 兼容适配器与 factory
    persistence/              # SQLAlchemy 异步 SQLite 与角色、骰子、事件三表
    domain/, dice/            # 领域对象与封闭骰式解析器
    character/, rules/        # 车卡业务、持久化仓库、规则校验及计算
    rooms/                    # 类型化会话、事务命令、可见性和实时同步
    agents/, memory/          # 预留空包
  scripts/check_local_model.py # 四项真实本地模型检查
  scripts/check_character_creation.py # 独立数据库 + 真实Chrome离线车卡验证
  scripts/check_multiplayer.py # 三个隔离Chrome上下文的多人验收
  scripts/host_key.py         # 本地主机密钥初始化与显式查看
  tests/                      # 原有测试及离线模型单元测试
  pyproject.toml, uv.lock, .python-version
frontend/                     # React / TypeScript / Vite
data/
  rules/                      # 用户自行放置本地规则文件
  modules/                    # 用户自行放置本地模组
  saves/                      # 预留存档目录
  logs/                       # 预留日志导出目录
```

后端依赖：FastAPI、Uvicorn standard、Pydantic Settings、SQLAlchemy asyncio、aiosqlite、LangGraph、LangGraph SQLite Checkpointer、OpenAI Python SDK、HTTPX、PyMuPDF、python-multipart、PyYAML。开发依赖：pytest、pytest-asyncio、Ruff。

前端保留 Vite 模板的 React、React DOM、TypeScript、Vite、React 插件、类型声明和 Oxlint。使用原生 WebSocket 和普通 CSS。

SQLite 默认为 `data/game.db`，启动时用 `metadata.create_all` 增量创建 `character_drafts`、`character_roll_records`、`character_events`，以及 `game_rooms`、`room_members`、`room_character_slots`、`room_events`、`room_snapshots`，保留已有表和数据。角色表没有迁移。房间修改在 SQLite `BEGIN IMMEDIATE` 事务中完成，room/seq 联合主键和请求 ID 唯一约束提供并发与幂等保证；提交后按权限广播。`data/agent_checkpoints.db` 仅预留配置，尚未初始化 checkpointer。数据内容、数据库文件及其附属文件均不提交，各数据目录用 `.gitkeep` 保留。

尚未实现：KP Agent、AI 队友实际推理、完整 CoC 规则工具、模组检索、分层记忆、远程角色上传、公网部署和多 worker 广播。下一批应通过统一 RoomService 命令、SessionStateV1 和 RoomEvent 接入，避免直接改数据库或在 WebSocket 中另建业务规则。

## 验证

```powershell
cd backend
uv run ruff check .
uv run pytest
cd ../frontend
npm run lint
npm run build
```

全部测试使用临时 SQLite 文件，保留原有15项健康、WebSocket、模型适配器和目录状态测试。新增测试覆盖骰式、种子注入、规则配置、舍入、年龄分段、职业与信用评级、两类技能点、持久化、版本冲突、最终确认和JSON往返。HTTP网络传输在测试中被禁止，模型全部使用mock，不要求Ollama在线，也不改动本地游戏数据库。

Windows 已安装 Chrome 时，可在后端目录运行完整浏览器流程：

```powershell
uv run python scripts/check_character_creation.py
uv run python scripts/check_multiplayer.py
```

脚本临时使用8000和5173端口，先确认无其他服务占用，再启动真实无界面Chrome、前后端和独立测试数据库。车卡脚本验证随机、购点超支与修正、技能分配、最终确认、刷新、下载／导入JSON、后端重启和WebSocket echo。多人脚本使用三个隔离浏览器配置目录验证主机、两位远程玩家与 Agent 占位、私密过滤、同一骰子结果、存读档确认、断线补发、后端重启、日志下载与敏感字段扫描、窄屏和退出清理。

**模型目录响应由测试启动器模拟**，不访问 Ollama 或外部 API。成功或失败后均关闭临时进程；截图、报告和测试数据库保留在忽略目录 `.cache/character-smoke-<uuid>/` 和 `.cache/rooms-smoke-<uuid>/`，不会删除或写入用户数据库。正常启动的系统状态接口仍保留原来的目录查询行为。

后端运行时，也可在 PowerShell 执行 `Invoke-RestMethod http://127.0.0.1:8000/api/health`。预期结果：

```json
{"status":"ok","database":"ok","model_provider":"ollama"}
```

原 `/ws` 连接需先发送 `{"type":"auth","credential_type":"host","token":"<主机密钥>"}`，认证成功后返回 `{"type":"connected","data":{"message":"WebSocket connected"}}`，随后普通 JSON 仍返回 `{"type":"echo","data":原始JSON}`。密钥不放 URL，不重发认证帧。房间 WebSocket 协议见 [多人协议](docs/multiplayer-protocol.md)。健康检查中的模型供应商只代表配置，不表示模型可用。
