# CoC 跑团 Agent

Windows 原生运行的本地主机型 CoC 第七版跑团 Agent。由一名玩家在本地启动主机，支持单人游戏和其他玩家通过局域网或已获准连接的 Tailscale 网络加入；模型可选择 Ollama 或外部兼容 API。

当前支持 FastAPI 健康检查、异步 SQLite 连接、WebSocket JSON echo、模型状态显示，以及统一模型适配层的普通生成、流式文本、结构化输出和工具调用解析。启动、健康检查和单元测试不触发模型推理；真实模型检查由本地脚本单独执行。

支持数据驱动的第七版车卡：随机／购点创建、年龄调整、属性与派生值计算、职业／兴趣技能分配、草稿持久化、最终确认及 JSON 导入导出。全部车卡计算由后端确定性代码完成，不使用模型。

已加入持久化多人房间、主机与远程成员认证、角色快照、服务端聊天／掷骰、WebSocket 重连和存读档。第三批实现 AI KP、AI 调查员队友、原创模组、分层记忆，以及通过 LangGraph interrupt/resume 等待真人掷骰的可玩回合。多人底层协议见 [多人协议 v1](docs/multiplayer-protocol.md)，Agent 行为见 [Agent 运行机制](docs/agent-runtime.md)。

## 开始一局 Agent 调查

1. 运行根目录 `start.cmd`，主机解锁后在“系统状态”选择本机 Ollama 或 OpenAI 兼容 API，保存并试运行。模型未就绪时仍能车卡和配置模型；启动不会推理或下载。
2. 主机解锁后打开“Agent 档案”，手工创建一个 **AI KP**；需要队友时再创建 **AI 调查员**。也可输入概念生成结构化草稿；检查、修改并点击“确认保存档案”后才会保存。编辑已绑定档案前先解除绑定。
3. 创建房间并发布最终确认的角色。给每个调查员席位分配一张角色卡；单人可只使用真人席位。主机自己扮演调查员时，要另建“本地真人”席位，管理身份本身不绑定角色。
4. 选择模组：原创练习在“AI 与模组设置”选择 **停摆的钟楼**；正式准备包按下节导入后，在“模组准备与主机审阅”的“批准版本”中选择并绑定。绑定 AI KP 档案及所需的队友档案。远程玩家加入并自己准备；主机给本地真人和 AI 席位设置准备，然后开始游戏。
5. 真人在“调查行动”输入框提交行动，例如“我进入维修间，仔细检查工作台”。提交后等待 KP；需要检定时出现卡片，显示真实技能值、难度和奖惩骰。目标真人或主机点击“掷骰完成检定”，所有骰点与判定由服务端产生。
6. KP 根据真实结果推进场景／线索，再以只含公开资料的模型调用生成叙事。AI 队友最多行动一次，然后等待下一次真人输入。聊天输入框只发送聊天，不会触发 Agent 回合。
7. 模型失败会显示安全错误，主机可“重试 Agent 回合”或“取消 Agent 回合”。重试保留已完成骰点、事件和调用计数；预算耗尽时取消后提交新行动。
8. 在回合结束或等待检定时存档；暂停后载入。重启后仍可继续待掷检定，已解决的骰子不会再次掷出。载入保留后续历史事件和当前凭据。

主机可展开“Agent 调试面板”并刷新，查看 graph 节点、模型、耗时、结构化输出、工具回执、输入事件与记忆；玩家不能读取这些数据。模组快照在绑定时固定，源 YAML 后续修改不影响旧房间。

8GB 显存下默认所有调用串行复用一个模型，`MODEL_CONTEXT_LIMIT=8192`、`MODEL_OUTPUT_LIMIT=900`、`MODEL_TEMPERATURE=0.3`、`MODEL_KEEP_ALIVE=5m`。建议首次加载使用 `MODEL_TIMEOUT_SECONDS=240`；在根 `.env` 修改后重启后端。`default` 预设来自这些后端配置，不在档案中保存密钥。每轮默认最多 12 次实际调用，格式修复、参数修复、队友修复和按需摘要都计入预算；每份计划最多 4 个工具。一个队友时，基本流程为计划、结果叙事和队友决策三次调用；检定等待不重新生成计划。计划输出预算至少 1600 tokens，增加队友、修复和摘要会占用同一回合预算。

第三批验收命令（仓库根目录，需已安装 Chrome，且 8000／5173 端口空闲）：

```powershell
# Fake Model：真实 HTTP、SQLite、WebSocket 和三个独立浏览器，不访问 Ollama
uv run --directory backend python scripts/check_agent_cycle.py
# 明确执行本地真实模型验收，不安装或下载模型，不调用外部 API
uv run --directory backend python scripts/check_agent_cycle.py --ollama
```

两个脚本模式均使用 `.cache/agent-*-smoke-<uuid>/` 内的临时数据库、checkpoint 和测试主机凭据；结束后清理所启动的进程并留下报告、截图、日志。不会启动或修改用户数据库。自动单元测试也只使用 Fake Model。

## 正式模组的日常导入与开团

1. 使用普通 `start.cmd` 启动，解锁主机，打开“模组准备”。在“导入已准备模组”中选择本地 JSON 文件并点击“导入准备包”。本机《常暗之厢》使用 `data/prepared/changan/batch-21/package-approved.json`，文件 SHA256 为 `93dee39824e41a0d9544fc5e869845c0ffcaeb43dd54603aec1eae840372ddd4`。
2. 原稿保留在当前应用来源目录的 `modules/常暗之厢/`（默认 `data/modules/常暗之厢/`）。导入核对原稿 hash、引用、场景及转换；缺少原稿或内容变化会说明原因。私有原稿和正式包不随 Git 分发。
3. 核对标题、来源、7 场景／44 实体／12 转换、数值批准、当前必需操作缺项和历史实测范围。已登记的 NPC v1 批准继续有效，导入不调用模型。未知补充进入实体审阅；有缺项或结构未批准的版本不能开团。历史 `blocked`／`host_ruling` 是来源分类，不等于当前操作缺项。
4. 创建未开始的房间，给调查员分配角色、绑定 AI KP；在“模组准备与主机审阅”选择刚导入的“批准版本”，点击“绑定准备版本”，准备后开始游戏。游戏直接使用系统状态页当前生效的模型配置。
5. 相同内容重复导入复用版本；新内容形成新版本。已绑定房间保留冻结快照。刷新与重启后仍可选择；保存、暂停、载入和继续沿用普通房间流程。

命令行 `backend/scripts/module_package.py load --bundle PATH --directory data/prepared/...` 仍保留显式独立库用途，与 UI 共用导入服务；日常游戏无需再启动独立 `serve`。静态校验通过与自然游戏实测分别呈现。交付边界见[第二十四批报告](docs/batch-24-report.md)及[运行清单](docs/batch-24-checklist.md)。

## 第七版角色创建

打开页面后先解锁主机，即可进入“角色列表”，导航可切换“系统状态”“创建角色”和“多人房间”。远程玩家直接进入多人房间，通过邀请码加入。选择规则集、模式，填写姓名及年龄，然后创建草稿。随机模式一次生成整组属性；购点模式实时预估剩余点数，保存后的后端余额、派生值和校验结果为准。超支或越界可以保存为草稿，但不能最终确认。保存并通过完整校验后，点击“最终确认”；已确认的角色只读。

规则文件位于 `backend/app/rules/definitions/`：

- `coc7_character_creation.yaml`：当前默认启用，`verification_status: verified`，指已核对本批实现的车卡子集。依据用户本地的《克苏鲁的呼唤第七版规则书1907》和《克苏鲁的呼唤第七版调查员手册1.20》，文件名、PDF页码和公式边界见同目录 [SOURCES.md](backend/app/rules/definitions/SOURCES.md)。不再使用 CoC 5 配置。
- `development_character_creation.yaml`：保留为离线测试 fixture，`verification_status: unverified`；只含少量程序测试示例，界面明确提示不代表第七版规则。

当前默认规则目录为 **1.1.0**：八项属性、31项职业、103个目录技能／专业稳定 ID。职业覆盖规则书基础28项及手册会计师、消防员、护士；另支持本卡自定义外语、艺术／手艺、科学专业，分别分配和检定；跨专业可选加值仍未实现。随机模式使用3d6或2d6+6后乘5；购点使用规则书3.7方案四的**460点可选规则**，八项基础属性总和必须为460，范围15–90，INT/SIZ至少40，购点EDU至少15。幸运独立掷骰，不占属性池。规则配置使用 Pydantic 校验和安全 YAML 读取；公式仅接受封闭运算，没有 Python `eval`／`exec`。

创建前选择15–89岁年龄；生成后锁定年龄，避免重复获取幸运或教育增强检定。按年龄分配STR/SIZ或STR/CON/DEX扣减，其他固定扣减由后端执行。教育增强按顺序比较当时EDU并封顶99；每轮检定骰和条件增长骰一并保存，失败时增长骰不生效。购点编辑复用已保存的骰子，不重新掷骰。属性编辑区显示年龄调整前值和后端已保存的调整后值；HP、MP、SAN、MOV、DB、体格及技能基础值使用调整后值。

职业点按条目采用 EDU×4 或 EDU×2＋指定／所选属性×2，兴趣点为 INT×2，分别记账；技能值为基础值加两类投入。信用评级须满足职业范围，母语基础值为EDU，闪避为DEX半值，创建时禁止给克苏鲁神话技能加点。未分配技能点在确认时放弃。当前不启用75技能上限、属性重骰、经历包等其他可选方案；没有逐项重骰入口。

JSON 可通过页面下载、粘贴或选择文件导入，导出含 `schema_version: 1`、导出时间、角色和规则集版本。导入会创建**新的草稿ID**，记录 `original_id`，将掷骰记录标记为 `imported`，重新计算属性调整、派生值、余额及合法性，不能覆盖原角色。导入记录仅验证内部一致性，不作为外部掷骰真实性证明。导入后需要再次最终确认。

API：`GET /api/character-rulesets`、`GET /api/character-rulesets/{id}`；`GET /api/characters`、`POST /api/characters/random`、`POST /api/characters/point-buy`、`POST /api/characters/import`；`GET/PATCH /api/characters/{id}`、`POST /api/characters/{id}/finalize`、`GET /api/characters/{id}/export`。PATCH和finalize必须携带当前整数`version`，旧版本返回409；无效请求字段返回422，不存在的角色返回404。没有角色删除接口。角色库接口及 `/docs`、`/openapi.json` 需要 `Authorization: Bearer <主机密钥>`；规则集元数据保持公开。直接从地址栏访问 API 文档不携带该请求头，可使用本地 HTTP 客户端查看 schema。

刷新页面或重启后可继续读取草稿和确认后的角色，保留原始骰。旧 1.0.0 规则按版本读取，旧确认卡和房间快照不迁移重算。

1.1.0 同时支持1920年代／现代信用评级现金、资产和日常消费额度、资产明细、六类背景及关键联系、9项普通装备和4项已核对武器。卡上装备与游戏中的实际持有分开；起始随身物结算遵守模组规则。自定义物品没有自动数值加成，枪械默认空弹。

在“职业与技能 → 自定义专业”中选择类别并输入名称。外语基础 1、艺术／手艺基础 5、科学基础 1，上限沿用 99；目录内专业（包括数学基础 10）直接选择，不能用同名自定义项改变基础值。添加后可选作对应职业分组或兴趣专业，改名保留 ID；删除会清理分组及投入并退回点数。每卡最多 50 项，名称 1–40 字；服务端校验重复名称和 ID、类别、年代与点数。导入重新计算，已确认卡及房间快照保留定义和数值；旧 1.0.0 卡保持原有范围。第 26 批结果与待验范围见 [报告](docs/batch-26-report.md) 和 [清单](docs/batch-26-checklist.md)。

尚未支持90岁及以上条款、全手册职业、任意专业、跨专业可选加值，以及自动射击、霰弹分档、爆炸范围、链锯／绞索等武器特例。目录与来源见 [SOURCES.md](backend/app/rules/definitions/SOURCES.md)。

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

以后只需双击根目录 `start.cmd`，或在 PowerShell 中执行：

```powershell
.\start.cmd
```

启动器从自身位置定位仓库，检查 uv、Node/npm、依赖、主机密钥和端口。缺少依赖时显示一次性安装命令，不自动安装或改写环境。HTTP 代理实际就绪后才显示访问地址；端口占用会报错，不接管已有服务。

运行以下命令在本机查看主机管理密钥，并在页面“主机解锁”中输入（不要向远程玩家分享）：

```powershell
uv run --directory backend python scripts/host_key.py --show
```

打开启动器显示的地址创建角色，或在“系统状态”查看后端、SQLite 和 WebSocket。输入测试消息可查看 echo JSON。启动窗口按一次 `Ctrl+C` 统一停止本次服务，等待“Launcher services stopped”；启动失败也会清理。本机模式默认监听 `127.0.0.1`。启动器只管理其 Windows Job Object 内创建的进程，保留已有 Ollama 和其他服务。

后端默认端口 8000，读取根 `.env` 的 `APP_HOST` / `APP_PORT`。启动器将实际端口传给 Vite 的 HTTP 与 WebSocket 代理。前端默认 5173，也可运行 `start.cmd --frontend-port 5174`。修改监听端口后重启启动器。始终使用单个后端进程和一个 Uvicorn worker。

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

上述 `.env` 仅作首次模型配置的初始值。推荐在“系统状态 → 当前模型设置”保存：Ollama 从本机列表选择模型；API 填写平台 Chat Completions 兼容地址、模型名、密钥。示例域名和模型名只是占位，不能用于真实验收。

当前配置保存在 `DATA_DIR/host-model-settings.json`（可用 `MODEL_SETTINGS_PATH` 指定隔离路径），保存后优先于 `.env` 中的 provider、模型名、服务地址及输出模式，重启恢复。非模型配置仍来自原环境；文件不会改写 `.env`。前端只收到脱敏配置和密钥来源，密码输入框保持空白。手动输入的密钥保存在这个已忽略的本地文件中，不应分享。

官方 OpenAI 可直接使用根 `.env` 或进程环境中的 `OPENAI_API_KEY`。在设置页选择 API、填写 `https://api.openai.com/v1/` 和有权限的模型名（本批隔离测试默认 `gpt-4.1-mini`），密钥框留空后保存。仅添加密钥不会自动切换原有 provider/model。

凭据优先级：**同一服务地址显式保存的密钥 → 原环境为该服务配置的有效 `MODEL_API_KEY` → 官方 OpenAI 的 `OPENAI_API_KEY`**。空串和 `ollama` 占位值不阻止回退。进程环境优先于根 `.env`；环境密钥只保存来源，不复制为永久旧值，修改环境后重启即可使用新值。`OPENAI_API_KEY` 不用于 Ollama、第三方地址、非 HTTPS、其他端口或非 `/v1/` 路径。修改服务地址不会继承旧平台的显式密钥；切到 Ollama 再回到原 API 地址可以保留该 API 配置中的显式密钥。

KP、队友、档案、准备生成及摘要统一使用当前 `default`。全部生成任务和回合空闲时才能保存切换；等待检定、主持审阅、摘要、排队及失败待恢复回合都会列出。失败回合须先用原配置重试完成或取消，再切换；已有骰点、物品回执、角色和记忆保留。切换不会重做行动或自动改换 provider。

状态分为“未配置／未验证／可用／失败”。刷新只读取后端状态，Ollama 额外查询本机列表；不请求外部 API 或推理。保存后显示未验证；点击“试运行已保存模型”才发送游戏动态 KeeperNarration schema 的隔离生成，最多一次格式修复，不执行工具或掷骰。API 可能计费。试运行通过仅证明该结构可调用。本地第二十二批记录的最终短测未产生检定，不能算游戏验收通过；真实 OpenAI 与本批补充验证见[第二十三批报告](docs/batch-23-report.md)。

兼容 API 默认使用 JSON 模式，并附游戏生成 schema，所有输出仍由后端完整校验。严格 JSON Schema 模式需要平台支持；游戏工具参数包含开放字典时会明确要求改用 JSON 模式，不偷偷删去规则或自动重发。平台认证、额度、超时和参数错误均脱敏；返回的 token usage 原样计入调用记录，未返回时为 null。结构化输出区别见 [OpenAI 官方文档](https://developers.openai.com/api/docs/guides/structured-outputs)。

前端 API 使用同源 `/api`，WebSocket 从 `window.location` 推导 `/ws` 和 `/ws/rooms/{id}`。Vite 使用启动器传入的实际后端端口。旧 `frontend/.env` 中的 `VITE_API_BASE_URL` 和 `VITE_WS_URL` 不再使用。生产构建若另行托管，也必须提供相同路径的反向代理。

根 `.env` 的 `HOST_ADMIN_TOKEN` 由 `scripts/host_key.py` 初始化：保留其他配置，仅缺少密钥时生成至少 32 字节随机值，默认不打印。主机密钥最多保存在浏览器 sessionStorage。模型 API Key 仅保存在后端，不能写入任何 `VITE_` 变量。

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

模型默认存储在用户目录 `%USERPROFILE%\.ollama\models`，不放入仓库，不修改全局 `OLLAMA_MODELS`。模型下载由用户自行执行；本批启动器和设置界面都不下载。在模型设置刷新列表，选择已安装模型后保存即可切换。

根目录 `.env` 使用上一节的 Ollama 配置。`MODEL_API_KEY=ollama` 是 SDK 所需的非空占位值，Ollama 会忽略它，参见[官方兼容接口说明](https://docs.ollama.com/api/openai-compatibility)。可选 `MODEL_TIMEOUT_SECONDS=120` 用于限制请求等待时间。

Ollama 保持默认的 `127.0.0.1:11434` 本机监听，不开放该端口的入站规则。浏览器通过后端使用模型，模型配置和状态接口仅允许主机访问。Ollama 未启动时仍可进入车卡及模型设置，切换到 API；API 模式的启动不检查 Ollama。

在后端目录运行一次真实检查：

```powershell
cd backend
uv run python scripts/check_local_model.py
ollama ps
```

脚本先以空 Prompt 加载配置中的模型并报告耗时，再依次检查短中文回复、流式文本、Pydantic JSON 校验和 `roll_dice(100, 1)` 工具参数。它不会执行骰子、下载模型或连接外部 provider。首次加载的计时与后续短请求计时分开记录；如果模型已加载，会明确标注。

适配层使用 `openai.AsyncOpenAI`，无内部会话历史，SDK 自动重试设为 0。Ollama 请求关闭思考输出以缩短本轮验证；普通响应返回文本、工具调用及可选结构化对象。流式模式仅支持文本，工具及 JSON 校验使用非流式模式，避免丢失工具参数或跳过完整 JSON 校验。调用方在结束后关闭模型客户端，提前结束流时关闭异步流。

## 局域网主机模式

使用同一个启动器开放前端供局域网玩家加入：

```powershell
start.cmd --lan
```

此模式前端监听 `0.0.0.0`，后端沿用 `APP_HOST`（默认回环地址），由 Vite 统一转发玩家请求。已有 `APP_HOST=0.0.0.0` 配置也会启用局域网前端。启动器显示可用 IPv4 玩家地址；无需逐个配置玩家地址或手动修改代理。

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

停止时在启动器窗口按 `Ctrl+C`；只关闭浏览器不会停止服务。验证脚本只清理其创建的临时 Chrome、Vite、FastAPI，随后核对端口释放。

Ollama 保持 `127.0.0.1:11434`，玩家入口统一使用前端端口。Tailscale 与房间角色文件提交见下节；Cloudflare Tunnel 和匿名公网托管留后续。

## Tailscale 异地加入与玩家角色提交（第 27 批）

实现和本机三浏览器验证已完成；**真实异地设备验收仍待完成**。本批实测主机未安装 Tailscale CLI，没有把同机浏览器或模拟地址算作异地成功。详见 [第 27 批报告](docs/batch-27-report.md) 与 [操作／验收清单](docs/batch-27-checklist.md)。

1. 主机和玩家先安装并登录 Tailscale，设备须已获准连接同一网络。按 [Windows 安装说明](https://tailscale.com/docs/install/windows) 和 [设备连接说明](https://tailscale.com/docs/how-to/connect-to-devices) 操作。启动器不代办账号、登录、邀请或网络策略。
2. 主机在仓库根目录运行 `start.cmd --tailscale`。自定义前端端口可用 `start.cmd --tailscale --frontend-port 5187`。等待出现本机房间入口和 **Tailscale 异地加入地址** `http://<本机实际 Tailscale IPv4>:<前端端口>/#/rooms`。默认 `start.cmd` 与原 `--lan` 继续可用。
3. 主机打开本机入口、解锁并创建房间，向玩家提供上述异地地址与房间邀请码。玩家在自己的浏览器输入邀请码和显示名；不需要主机密钥或成员 ID。
4. 玩家在大厅“提交角色文件”选择从现有车卡工具导出的 **CharacterExport JSON**。先查看服务端重算的属性、派生值、点数及语言／艺术手艺／科学专业，再点击“提交角色”。请求上限为 **256 KiB**；有格式、版本或校验错误时按提示在原车卡工具修正并重新导出。本批不提供远程车卡编辑器。
5. 主机在“玩家角色提交”打开该成员的当前预览，查看重算结果、来源和原骰，然后“接受并分配”或“拒绝提交”。接受会正式确认角色、冻结房间快照并初始化角色资源，自动分配给提交者。提交者已有角色时，先在“房间调查员”取消原分配。拒绝不创建角色库草稿；玩家可以提交新版本，旧版不能再被批准。
6. 使用自己提交的角色时，接受后直接点击“准备”；使用主机发布角色的玩家仍可点击空闲角色的“选择”，再准备。主机为本地真人／AI 席位分配并准备。按上方“开始一局 Agent 调查”配置模组及 KP；所有活动玩家准备后，主机开始游戏。角色提交审批只发生在开局前，不增加普通行动审批。
7. 短暂断网后页面自动重连；刷新可恢复房间成员身份。主机停止服务时在启动窗口按 Ctrl+C，仅关闭本次前后端，不停止已有 Tailscale 或 Ollama 服务。开始后（包括暂停）不可提交、替换或接受新角色。

启动器从 PATH 或 Windows 常见安装目录定位 CLI，对 `tailscale status --json` 和 `tailscale ip -4` 分别设置 3 秒超时，只核对本机连接状态及地址，不输出设备／账户清单。命令依据见 [官方 CLI 文档](https://tailscale.com/docs/reference/tailscale-cli)。未安装、未登录、离线或无法取得地址时会显示“异地入口尚未就绪”和对应下一步，本机车卡仍可使用。仅前端监听网络地址；HTTP 和 WebSocket 使用现有 `/api`、`/ws` 同源代理，不需要改后端或 Ollama 监听。

### 按实际故障定位

| 现象 | 下一步 |
| --- | --- |
| 启动器没有异地地址 | 按提示安装／登录／连接 Tailscale；核对本机地址后重启启动器。普通网卡地址不算异地地址 |
| 玩家无法连通主机设备 | 在玩家设备执行 `tailscale ping <主机 Tailscale IPv4>`；失败时先核对双方客户端连接和设备获准状态 |
| 设备连通，但页面打不开 | 主机先确认本机入口已就绪；玩家核对前端端口，Windows 可用 `Test-NetConnection <主机 Tailscale IPv4> -Port <前端端口>`。仅此端口失败时再检查对应 Windows 防火墙或 Tailscale 网络访问规则 |
| 页面能打开，但加入返回邀请码无效 | 使用当前房间邀请码；主机重新生成后旧码失效，已有成员凭据仍可重连 |
| 已加入但一直重连／状态不更新 | 查看浏览器 Network 中 `/ws/rooms/...` 是否成功升级并收到认证消息；确认仍通过同一前端地址访问。凭据失效或被移出时重新加入；只要凭据有效就保留原房间并等待自动重连 |

仅主机和提交者可查看完整提交卡与处理说明；其他成员仅收到提交版本和状态。成员令牌依旧按房间保存在其浏览器中，全局角色库仍需要主机身份。

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

已接入 SAN 自动遭遇、疯狂状态，以及普通检定的可选幸运消耗和孤注审批／结算，见 [规则与接口](docs/check-settlement-and-encounters.md)。亲自操作可按 [第十一批本机手测指南](docs/batch-11-manual-test.md) 建立隔离副本，验证幸运、自动 SAN、孤注与存档续算。已实现对抗／组合检定、基础战斗及伤势、HP／MP／SAN／Luck、库存与武器投影，以及已批准正式准备包的日常导入。正式 B 主线、SAN 和独立战斗有历史 Ollama 证据；第23批 OpenAI 短测没有 SAN／疯狂／战斗，不能据此宣称 API 全规则验收。第27批已加入房间角色提交与 Tailscale 启动检测，真实异地仍待验。通用追逐／成长、完整 CoC 规则、匿名公网部署和多 worker 广播仍未实现。继续使用本地文本 RAG，无向量数据库或 embedding。完整状态见 [开发清单](docs/development-checklist.md)。

## 模组准备、主机审阅与调查板（第五批）

解锁主机并完成本地文本索引后，进入「模组准备」，选择来源和物理页码／章节范围，创建任务并生成实体草稿。逐项查看有限证据摘录，编辑公开摘要、检定和公开条件，再批准或拒绝；设置一个已批准初始场景，确认开场必要实体后批准准备版本。模型不会自动批准或开始游戏。

在未开始的房间中绑定准备版本，再绑定 KP、AI 队友和角色并开始。新事实需要主机确认时，回合进入审阅等待；主机可批准、编辑后批准或拒绝，随后恢复原回合。玩家调查板只显示已公开人物、地点、线索和物品，刷新、重连与读档后恢复。

来源 hash 改变会使旧准备任务变为 `stale`；已有房间保留冻结版本，新房间须使用当前来源的新准备任务。当前只处理文本信息，不含图片、地图或 OCR。《常暗之厢》开场可选择 Word 物理页 2–3，并按同样步骤人工核对证据；实际验收结果见[第五批报告](docs/batch-5-report.md)。完整生命周期、API、存档和权限说明见[模组准备文档](docs/module-preparation.md)。

## 文档结构与场景导航（第六批）

新准备房间在绑定前，需要在「模组准备 → 文档结构」构建 ModuleIR：展开目录、核对低置信度标题与来源位置，选择节点校正类型和父节点，填写场景公开标题与摘要，再标记 initial scene。将第五批已批准的 NPC、地点、线索和物品绑定到节点，配置并批准场景转换，最后批准结构快照。原 DOC 和正文顺序不变。

房间保存 current scene、已访问场景和导航 revision。KP 按当前节点及必要子节点读取；调查员和玩家只接收公开场景、调查板与公开事件。普通回合不搜索整个模组，长场景在当前子树内选取受限片段。主机跨章节搜索或主机批准的 incomplete 结构允许 FTS5 补充；规则书继续走原 RAG。

主机游戏界面的「当前场景导航」显示路径、前一场景、NPC、转换条件、审阅状态和实际 cycle 节点审计。无合法转换时沿用主机审阅；主机也可批准一次性转场。存读档固定 snapshot、source hash 和位置；出现 `module_structure_missing` 时保留调查板和历史，暂停新回合。恢复匹配知识库或在原 hash 未变时重建原准备任务，再调用 `POST /api/rooms/{id}/module-navigation/reload` 并恢复房间。

《常暗之厢》可复用本地 Word 只读提取，构建完整基础目录后仅校正开场与一次邻接转换，绑定第五批已批准开场实体。没有已批准 NPC 时不要自动批准未来人物。Fake 三浏览器验收命令为 `backend/.venv/Scripts/python.exe backend/scripts/check_module_navigation.py`；真实验收加 `--real --config .cache/batch-6/real-config.json`，配置参考[ModuleIR 文档](docs/module-ir.md)，结果见[第六批报告](docs/batch-6-report.md)。脚本只在 `.cache` 的独立数据库运行并清理临时服务，真实模型固定使用已有 `qwen3:8b`。

## 玩家行动裁决（第七批）

真人行动先进入私有 `KeeperPlan`，服务端核对原文引句、角色、当前目标、工具权限、检定与转场条件，执行完成后才生成公开 `KeeperNarration`。观察入口或询问去向不会自动转场；只有真人明确移动、目标匹配批准出口且条件满足，才允许进入下一场景。检定等待期间只显示请求，骰点由服务端产生。

意图或目标不明确时，界面显示“需要澄清”和问题。补充行动会作为新的 action event 关联该问题，上一回合已经结束。当前场景有已公开 NPC 时，可以在行动框选择交谈目标；KP 只按已批准公开摘要扮演 NPC。准备工作台显示批准人物计数，合成测试人物必须勾选专用标记。

AI 队友可以 `pass`，不会被强制生成台词。服务端检查最近三次输出、其他队友和真人动作，重复内容最多修复一次，再重复则沉默；相同动作与目标默认冷却两个回合，场景或公开结果变化可解除，存档会保存冷却状态。

工具缺少当前场景内容时允许一次局部补读；同场景 revision 冲突允许一次复核，权限与前置条件错误不盲目重试。参数修复不能发明目标 ID。已成功工具、检定、转场和公开事件通过 receipt 或等价幂等记录避免重复执行。摘要失败保留旧摘要和待摘要范围，后续回合重试；达到失败上限后主机可在调试面板手动重建。

主机调试面板可查看意图、evidence quote、计划、允许/拒绝动作、补读和恢复、工具 receipt、最终叙事、队友拒绝与 pass、摘要 stale 状态。玩家只能看到公开结果、等待状态和澄清问题。完整协议见[行动裁决文档](docs/action-adjudication.md)，实测结果见[第七批报告](docs/batch-7-report.md)。

《常暗之厢》行为验收依次进行：观察环境、调查入口但不移动、与当前 NPC 交谈、完成一次真人检定、让队友获得多次机会、明确进入批准的下一场景，再存档、重启、读档并继续观察。命令在仓库根目录运行：

```powershell
backend/.venv/Scripts/python.exe backend/scripts/check_action_adjudication.py
backend/.venv/Scripts/python.exe backend/scripts/check_action_adjudication.py --real --config .cache/batch-6/real-config.json
```

脚本使用隔离数据库和三个 Chrome profile，真实模式只调用现有本地 `qwen3:8b`。一次摘要超时为隔离验收注入，单独计入审计；Fake 模式另注入局部工具错误。原始模组、`.env` 和用户库不修改。真实 PUBLIC/HOST_DEBUG 记录位于 `.cache/batch-7/常暗之厢-session.md`。

## 检定与公开叙事（第八批）

明显环境、已公开信息、普通 NPC 交谈和已批准无障碍移动默认无需检定，直接给出公开结果。检定必须有不确定因素、不同的成败结果、真实角色技能、当前可见目标，以及实体批准条件或玩家明确风险和已实现规则依据。相同状态下已完成的同目标同技能检定不能重复；状态变化后仍需重新通过其余验证。

准备工作台可选择 `automatic`、`requires_check`、`requires_condition` 或 `host_review`，沿用实体条件 JSON，无需改旧表列。自动成功仅表示完成普通行动或获取批准的公开信息，不自动公开隐藏线索。公共检定卡、时间线和叙事使用“侦查／敏捷”等规则集显示名，骰点、难度、目标、等级和奖惩骰由服务端格式化。

已实现的技能／属性、难度、大成功／大失败、奖惩骰优先使用绑定精确规则版本的 RuleTopicRegistry；解释性及未结构化问题继续走 RAG，按概念分别检索，再过滤和合并。普通回合继续使用场景结构，不检索模组全库。

公开叙事逐项验证当前场景、公开实体和真实执行结果，最多修复一次；仍无效时使用按本轮行动、真实检定和公开状态生成的简短中文文本，不再调用模型或重复工具。队友只在新线索、转场、直接交谈或自身目标触发时获得调用机会。摘要达到事件数量或上下文预算阈值才生成。

主机调试面板可查看检定提案与拒绝原因、规则来源、叙事验证与回退、队友跳过原因、每回合调用数和模型耗时。`MODEL_THINK=false` 沿用现有非思考行为并变为可配置项；`SUMMARY_EVENT_THRESHOLD=20` 与 `SUMMARY_CONTEXT_THRESHOLD=12000` 控制摘要阈值。详见[检定与叙事政策](docs/check-and-narration-policy.md)和[第八批报告](docs/batch-8-report.md)。真实公开／主机记录在 `.cache/batch-8/常暗之厢-session.md`。

```powershell
backend/.venv/Scripts/python.exe backend/scripts/check_check_policy.py
backend/.venv/Scripts/python.exe backend/scripts/check_check_policy.py --real --config .cache/batch-6/real-config.json
```

脚本使用隔离数据库、三个 Chrome profile 和本地现有 `qwen3:8b`，执行七轮必要性验收及存档、重启、读档后的第八轮；不修改原模组、用户库或 `.env`。

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

## 第九批：规则提问与剧情上下文

游戏输入区现在支持“调查行动／规则提问”。规则提问直接返回绑定公开规则的主题说明或检索原文，不启动行动裁决、检定、转场、队友或摘要。调查板显示当前场景、先前获知和位置未确认；回顾旧线索带明确历史限定。游戏摘要只选择剧情事件，角色分配以服务端当前状态为准。

接口、范围与恢复口径见 [规则提问与剧情上下文](docs/rule-questions-and-story-context.md)，本批实际验证见 [第九批报告](docs/batch-9-report.md)。
