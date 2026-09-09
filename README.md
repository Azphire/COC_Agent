# CoC 跑团 Agent

Windows 原生运行的本地主机型 CoC 第七版跑团 Agent。目标是由一名玩家在本地启动主机，支持单人游戏和其他玩家通过局域网浏览器加入；模型可选择 Ollama 或外部兼容 API。

当前支持 FastAPI 健康检查、异步 SQLite 连接、WebSocket JSON echo、模型状态显示，以及统一模型适配层的普通生成、流式文本、结构化输出和工具调用解析。启动、健康检查和单元测试不触发模型推理；真实模型检查由本地脚本单独执行。

支持数据驱动的第七版车卡：随机／购点创建、年龄调整、属性与派生值计算、职业／兴趣技能分配、草稿持久化、最终确认及 JSON 导入导出。全部车卡计算由后端确定性代码完成，不使用模型。

已加入持久化多人房间、主机与远程成员认证、角色快照、服务端聊天／掷骰、WebSocket 重连和存读档。第三批实现 AI KP、AI 调查员队友、原创模组、分层记忆，以及通过 LangGraph interrupt/resume 等待真人掷骰的可玩回合。多人底层协议见 [多人协议 v1](docs/multiplayer-protocol.md)，Agent 行为见 [Agent 运行机制](docs/agent-runtime.md)。

## 开始一局 Agent 调查

1. 确认本机 Ollama 已运行、`ollama list` 中已有 `qwen3:8b`。在“系统状态”查看模型是否可用。此步骤不下载模型。
2. 主机解锁后打开“Agent 档案”，手工创建一个 **AI KP** 和一个 **AI 调查员**。也可输入概念生成结构化草稿；检查、修改并点击“确认保存档案”后才会保存。编辑已绑定档案前先解除绑定。
3. 创建房间并发布最终确认的角色。至少准备真人及 AI 队友各一张角色卡；给 AI 调查员席位分配角色。主机自己扮演调查员时，要另建“本地真人”席位，管理身份本身不绑定角色。
4. 在“AI 与模组设置”选择原创 **停摆的钟楼**，绑定 AI KP 档案和 AI 队友档案。远程玩家加入并自己准备；主机给本地真人和 AI 席位设置准备，然后开始游戏。
5. 真人在“调查行动”输入框提交行动，例如“我进入维修间，仔细检查工作台”。提交后等待 KP；需要检定时出现卡片，显示真实技能值、难度和奖惩骰。目标真人或主机点击“掷骰完成检定”，所有骰点与判定由服务端产生。
6. KP 根据真实结果推进场景／线索，再以只含公开资料的模型调用生成叙事。AI 队友最多行动一次，然后等待下一次真人输入。聊天输入框只发送聊天，不会触发 Agent 回合。
7. 模型失败会显示安全错误，主机可“重试 Agent 回合”或“取消 Agent 回合”。重试保留已完成骰点、事件和调用计数；预算耗尽时取消后提交新行动。
8. 在回合结束或等待检定时存档；暂停后载入。重启后仍可继续待掷检定，已解决的骰子不会再次掷出。载入保留后续历史事件和当前凭据。

主机可展开“Agent 调试面板”并刷新，查看 graph 节点、模型、耗时、结构化输出、工具回执、输入事件与记忆；玩家不能读取这些数据。模组快照在绑定时固定，源 YAML 后续修改不影响旧房间。

8GB 显存下默认所有调用串行复用一个模型，`MODEL_CONTEXT_LIMIT=8192`、`MODEL_OUTPUT_LIMIT=900`、`MODEL_TEMPERATURE=0.3`、`MODEL_KEEP_ALIVE=5m`。建议首次加载使用 `MODEL_TIMEOUT_SECONDS=240`；在根 `.env` 修改后重启后端。`default` 预设来自这些后端配置，不在档案中保存密钥。每轮最多 6 次实际调用（含格式修复、最多一次前置条件计划修正及一次摘要），每次最多 4 个工具。一个队友时，无检定通常 3 次、有检定通常 4 次；增加队友会占用同一预算。

第三批验收命令（仓库根目录，需已安装 Chrome，且 8000／5173 端口空闲）：

```powershell
# Fake Model：真实 HTTP、SQLite、WebSocket 和三个独立浏览器，不访问 Ollama
uv run --directory backend python scripts/check_agent_cycle.py
# 明确执行本地真实模型验收，不安装或下载模型，不调用外部 API
uv run --directory backend python scripts/check_agent_cycle.py --ollama
```

两个脚本模式均使用 `.cache/agent-*-smoke-<uuid>/` 内的临时数据库、checkpoint 和测试主机凭据；结束后清理所启动的进程并留下报告、截图、日志。不会启动或修改用户数据库。自动单元测试也只使用 Fake Model。

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

## 本地规则书与模组 RAG（第四批）

主机解锁后进入“知识库”，点击增量索引，检查来源状态、页数、chunk 数及 hash，再测试查询。规则书放在 `data/rules/`；模组使用 `data/modules/<模组文件夹>/`，每个一级文件夹是独立来源，只递归读取该文件夹内部。文件夹名默认作为标题，已有 `manifest.json` / `manifest.yaml` / `manifest.yml` 优先提供元数据。无 manifest 时 ID 由稳定相对路径生成。

支持 PDF、Markdown、TXT、DOCX 和 DOC。同目录同名版本按 **DOCX → DOC → PDF** 选择；不同名称的受支持文件分别索引。DOC 使用本机已安装的 Microsoft Word，只读打开并禁用宏；缺少 Word 时明确记录失败，不安装转换器。DOCX 使用标准 XML 提取，不伪造物理页码。PDF 保留从 1 开始的 physical page 和文件提供的 page label；无文本层标记 `ocr_required`。图片、压缩包、缓存、输出及未知格式记录后跳过。没有 OCR、embedding 或新模型下载。

在仓库根目录运行：

```powershell
uv run --directory backend python -m app.knowledge.cli index --kind rules
uv run --directory backend python -m app.knowledge.cli index --kind modules
uv run --directory backend python -m app.knowledge.cli status
uv run --directory backend python -m app.knowledge.cli verify
uv run --directory backend python -m app.knowledge.cli query --kind rules "奖励骰如何判定"
uv run --directory backend python -m app.knowledge.cli cleanup --dry-run --game-database ../data/game.db
```

知识库默认独立存放于 `data/knowledge/knowledge.db`，由 `KNOWLEDGE_DB_PATH` 配置；可用 CLI 全局参数 `--database ../.cache/batch-4/knowledge-v2.db` 指定隔离库，参数放在子命令前。增量索引保留旧 source hash，原始文件不会被移动、改名或覆盖。清理默认仅预览；明确使用 `--apply` 才会清理当前游戏数据库中没有房间、存档或 claim 引用的旧派生版本。多个游戏数据库共用知识库时不要执行清理，当前命令只核查一个游戏库。

在房间大厅或暂停状态的“知识来源”面板选择规则书和可选模组，再绑定 AI KP、AI 队友。原始模组全部为 `keeper_only`；未结构化的本地模组需由主机填写简短公开开场。系统不自动生成 NPC 或线索，后续公开内容仍经既有场景、`reveal_clue` 和房间事件产生。原创《停摆的钟楼》继续供确定性测试使用。

KP 的规则陈述要引用当前 run 的有效规则证据；公开模组事实要引用已公开实体。无依据会显示“需要主持人裁定”。时间线实时显示 actor、cycle、发言、行动、检定与简短引用，展开引用可看页码和受限摘录。主机调试面板显示检索 query、score/rank、返回与实际注入的 evidence ID；玩家看不到隐藏证据或 KP 私密记忆。

存档固定来源 ID 和 hash。源文件变化不会自动更新旧房间；若看到 `knowledge_missing`，旧历史仍可读，新 Agent cycle 暂停。重新索引相同版本，或在暂停状态明确重新绑定可用版本后继续。所有原文、索引、提取缓存和开发记录均由 Git 忽略；第二批的 JSONL / Markdown 日志导出继续沿用，本批没有新增正式导出规范。

实现细节、验收配置与已知限制见 [RAG 架构](docs/rag-architecture.md) 和 [第四批报告](docs/batch-4-report.md)。

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
3. 所有活动玩家分配角色并 ready 后开始。已绑定档案的 Agent 可以参与调查回合；双方仍可发送聊天、普通骰式掷骰。私密消息只由本人和主机读取。
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
    agents/, memory/          # 模组、工具、LangGraph 回合、模型网关、分层记忆
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

第五批再新增八张准备、实体、审阅和存档辅助表，不修改已有表列。

SQLite 默认为 `data/game.db`，启动时用 `metadata.create_all` 增量创建原有角色／房间八张表及第三批 Agent 九张表，保留已有数据；没有修改旧表列。房间修改在 SQLite `BEGIN IMMEDIATE` 事务中完成，数据库约束保障序号与幂等，提交后按权限广播。`CHECKPOINT_DB_PATH` 指定 LangGraph SQLite 文件；未配置时使用游戏数据库同目录的 `<数据库名>.checkpoints.db`。本地数据和数据库不提交，各数据目录用 `.gitkeep` 保留。

尚未实现：完整 CoC 规则、完整模组自动结构化、战斗／追逐／疯狂／成长、远程角色上传、公网部署和多 worker 广播。当前支持本地文本 RAG、一个原创练习模组和已核对的最小属性／技能检定，不使用向量数据库或 embedding。

## 模组准备、主机审阅与调查板（第五批）

解锁主机并完成本地文本索引后，进入「模组准备」，选择来源和物理页码／章节范围，创建任务并生成实体草稿。逐项查看有限证据摘录，编辑公开摘要、检定和公开条件，再批准或拒绝；设置一个已批准初始场景，确认开场必要实体后批准准备版本。模型不会自动批准或开始游戏。

在未开始的房间中绑定准备版本，再绑定 KP、AI 队友和角色并开始。新事实需要主机确认时，回合进入审阅等待；主机可批准、编辑后批准或拒绝，随后恢复原回合。玩家调查板只显示已公开人物、地点、线索和物品，刷新、重连与读档后恢复。

来源 hash 改变会使旧准备任务变为 `stale`；已有房间保留冻结版本，新房间须使用当前来源的新准备任务。当前只处理文本信息，不含图片、地图或 OCR。《常暗之厢》开场可选择 Word 物理页 2–3，并按同样步骤人工核对证据；实际验收结果见[第五批报告](docs/batch-5-report.md)。完整生命周期、API、存档和权限说明见[模组准备文档](docs/module-preparation.md)。

## 文档结构与场景导航（第六批）

新准备房间在绑定前，需要在「模组准备 → 文档结构」构建 ModuleIR：展开目录、核对低置信度标题与来源位置，选择节点校正类型和父节点，填写场景公开标题与摘要，再标记 initial scene。将第五批已批准的 NPC、地点、线索和物品绑定到节点，配置并批准场景转换，最后批准结构快照。原 DOC 和正文顺序不变。

房间保存 current scene、已访问场景和导航 revision。KP 按当前节点及必要子节点读取；调查员和玩家只接收公开场景、调查板与公开事件。普通回合不搜索整个模组，长场景在当前子树内选取受限片段。主机跨章节搜索或主机批准的 incomplete 结构允许 FTS5 补充；规则书继续走原 RAG。

主机游戏界面的「当前场景导航」显示路径、前一场景、NPC、转换条件、审阅状态和实际 cycle 节点审计。无合法转换时沿用主机审阅；主机也可批准一次性转场。存读档固定 snapshot、source hash 和位置；出现 `module_structure_missing` 时保留调查板和历史，暂停新回合。恢复匹配知识库或在原 hash 未变时重建原准备任务，再调用 `POST /api/rooms/{id}/module-navigation/reload` 并恢复房间。

《常暗之厢》可复用本地 Word 只读提取，构建完整基础目录后仅校正开场与一次邻接转换，绑定第五批已批准开场实体。没有已批准 NPC 时不要自动批准未来人物。Fake 三浏览器验收命令为 `backend/.venv/Scripts/python.exe backend/scripts/check_module_navigation.py`；真实验收加 `--real --config .cache/batch-6/real-config.json`，配置参考[ModuleIR 文档](docs/module-ir.md)，结果见[第六批报告](docs/batch-6-report.md)。脚本只在 `.cache` 的独立数据库运行并清理临时服务，真实模型固定使用已有 `qwen3:8b`。

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
