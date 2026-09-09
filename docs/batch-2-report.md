# 第二批执行报告：多人房间与游戏会话内核

执行日期：2026-09-09。开始和结束时 HEAD 均为 `f796ae37010d16c47921253a9ae8575171b55053`。开始时 `git status --short` 为空，仓库及适用父目录未发现 AGENTS.md。没有创建分支／worktree，没有 commit、push 或暂存文件。

## 完成范围

已实现房间大厅、主机管理身份、本地真人、远程真人和 Agent 占位席位；发布最终确认角色的不可变快照、分配和 ready；开始／暂停／恢复／结束；类型化场景和 HP/MP/SAN/Luck 运行时资源；服务端聊天与 DiceService 掷骰；事务事件流、幂等请求；认证 WebSocket、可见性过滤、心跳和重连；自动持久化、手动存读档和 JSONL／Markdown 日志下载。

Agent 席位明确显示“尚未接入自动行动”，未伪造 Agent 消息。未改变任何已核验 CoC 7 规则数值或 SOURCES.md。未实现远程角色上传、模组解析、RAG、战斗等完整规则、账号、公网穿透、容器或多 worker。

## 主要文件

| 文件／目录 | 改动 |
| --- | --- |
| backend/app/auth.py、config.py、main.py | 密钥认证、配置、错误脱敏、HTTP 权限边界、房间服务生命周期 |
| backend/app/persistence/room_models.py、database.py | 五张新增表及索引；沿用 async SQLite、create_all；写锁等待 30 秒 |
| backend/app/rooms/schemas.py | SessionStateV1、CharacterRuntimeV1、存档格式及严格请求模型 |
| backend/app/rooms/service.py | 房间聚合命令、权限、事务、快照、事件及幂等 |
| backend/app/rooms/realtime.py | 首帧认证、过滤后同步、presence、队列、心跳与清理 |
| backend/app/api/rooms.py、api/websocket.py | 房间 REST/WS 路由；原 echo 加认证后保留响应格式 |
| backend/scripts/host_key.py | 保留现有 .env 配置、随机初始化、显式 --show |
| frontend/src/pages/RoomsPage.tsx | 主机／玩家大厅、实时会话、角色分配、场景编辑、存读档、日志 |
| frontend/src/api/rooms.ts、api/session.ts、components/HostUnlock.tsx | 类型、相对地址、房间凭据、幂等 ID、主机解锁 |
| frontend/src/App.tsx、App.css、api/characters.ts、pages/SystemStatusPage.tsx | 导航、样式、已有页面认证适配 |
| frontend/vite.config.ts、两份 .env.example、README.md | 同源 HTTP/WS 代理、本机／局域网运行与密钥说明 |
| backend/tests/test_rooms.py、已有相关测试 | 新增 56 项测试，旧测试适配必要的主机认证 |
| backend/scripts/check_multiplayer.py、check_character_creation.py | 真实多人验收、车卡与状态页回归，独立数据库和离线目录 mock |
| docs/multiplayer-protocol.md | 状态机、字段、接口、权限、重放／存档语义及后续接入点 |

## 数据库与领域策略

新增 `game_rooms`（UUID、状态、主机、邀请码哈希、revision、版本化 state、时间）、`room_members`（role/controller/access、显示名、ready/active、token 哈希、时间）、`room_character_slots`（源角色 ID、完整快照、公开摘要、唯一成员绑定）、`room_events`（room/seq 复合主键、actor、visibility、payload、请求 ID／摘要、时间）、`room_snapshots`（名称、创建者、源事件序号、格式版本、状态和分配）。原三张角色表没有迁移、删除或重建。

与计划建议命名保持一致。两处存储组织调整：成员的 slot_id 从席位的唯一 member_id 计算；所有运行时资源统一放在 game_rooms.session_state.characters，不在角色席位行再存一份。这样分配和资源各有一个权威来源。

状态机为 `lobby → running ⇄ paused`，`running/paused → ended`。ended 永久只读。主机管理身份不参与 ready／角色检查；开始和恢复时检查全部活动 player。离开与移出使成员失活并解除绑定，不删除成员。显示名按 casefold 去重，有数据库部分唯一索引。

只能发布 finalized 角色。完整卡复制一次并保持不变，资源初值只适配现有 derived_values；主机可查看全部完整卡，成员只看自己的完整卡，其他角色仅公开摘要。资源类型严格限制，更新必须提供 expected_revision。没有补充或猜测新 CoC 规则。

## 接口、认证与事件

REST 以 `/api/rooms` 为前缀，覆盖创建／列出／加入／读取、邀请码轮换、席位和 ready、角色发布／分配、状态机、session-state、messages／rolls、events、snapshots／load、logs。消息和骰子接受 UUID client_request_id；重复内容返回原事件，同 ID 不同内容返回 409。骰子结果只由原 DiceService 生成。

房间 WebSocket 为 `/ws/rooms/{room_id}`。首帧 auth 在 5 秒内验证，之后发送 auth.ok、过滤后的 room.snapshot、遗漏的 room.event、room.synced 和 presence.changed；心跳 ping/pong，其他变更走 REST。客户端在 room.synced 推进游标，按 seq 去重。连接队列为临时状态，重启后从 SQLite 恢复。

REST／WS／日志共用 public、actor_and_host、host_only 过滤。完整角色库、主机控制、存档和完整日志受保护。HTTP 使用 Authorization 请求头，WS 仅首帧传 token，没有查询参数凭据。错误响应不回显输入，API 设置 no-store。

本地 .env 已补充随机主机密钥，原配置保留，执行过程没有输出真实值。浏览器主机密钥只放 sessionStorage。邀请码与远程 token 在数据库只存 SHA-256；邀请码仅创建／轮换时返回，远程 token 仅加入时交付并按房间放 localStorage，退出清除。主机验证使用常量时间摘要比较。

每次修改在 SQLite BEGIN IMMEDIATE 事务内分配事件序号并提交状态；数据库复合主键和幂等唯一约束兜底。按房间 asyncio 锁只负责排列提交广播与初始同步。服务器只支持一个 Uvicorn worker。

## 存档准确语义

running/paused 可存档，仅 paused 可读档，均仅主机可操作。存档包含源 event_seq、SessionStateV1、运行时资源和角色分配。读档恢复这些字段，保持 paused，追加 snapshot.loaded 并广播按权限过滤的完整房间快照。

不恢复邀请码、token、active、ready、last_seen 或 presence，不回退 revision、不删除后来事件。已失活成员不会复活，对应旧分配留空并记录在 loaded 事件中。原角色卡始终不变。前端载入前显示真实确认框。

## 实际检查结果

| 检查 | 结果 |
| --- | --- |
| 开始时前端 lint/build、Ruff check | 通过 |
| 开始时 pytest | 沙箱临时目录权限错误；在同一仓库独立临时目录以允许执行方式重跑后 159 passed |
| 开始时全仓 Ruff format --check | 3 个旧文件需格式化，已在实施前报告 |
| 最终 Ruff check --no-cache . | 通过 |
| 本批涉及文件 Ruff format --check | 通过，46 files already formatted |
| 最终全仓 Ruff format --check | 50 个文件通过；2 个无关旧文件仍不符合，见下文 |
| 最终全部 pytest | **215 passed，1 warning，105.85 秒**，其中新增 56 项房间测试 |
| 最终前端 npm run lint | 通过，无 warning |
| 最终前端 npm run build | 通过，TypeScript 和 Vite 生产构建成功 |
| 原模型状态离线回归 | 全量测试包含 present/missing/offline 三种 mock；真实浏览器也使用 mock 目录 |
| git diff --check | 通过 |

保留的两个格式基线问题：`backend/scripts/check_local_model.py`、`backend/tests/test_models.py`。未为本批修改无关模型脚本／测试。第三个旧文件 test_model_status.py 因增加主机认证而属于本批必要适配，已格式化。pytest 唯一警告是原 Starlette TestClient 使用 AnyIO BlockingPortal 别名的弃用提示。

测试覆盖凭据哈希／轮换、远程越权、唯一名称、三类席位、角色快照不随源库变化、分配／ready／状态机、只读终局、严格资源模型、服务端骰子与幂等、多个独立服务实例的并发序号、不同 REST/WS 可见性、未认证 WS 拒绝、重连补发、存档资源／绑定恢复、失活成员、凭据／连接保留、日志过滤、重启恢复、主机密钥初始化和 CORS。

## 真实浏览器验收

三个独立 Chrome 配置目录分别模拟主机和两位远程玩家；通过真实 UI 发布三个已确认角色、分配 Agent 占位、ready、开始、聊天和掷骰。三方看到同一服务端结果，第三位玩家的 DOM、WS 帧和导出均看不到另一玩家的私密消息。

主机通过真实鼠标点击并接受原生读档确认框，验证场景与 HP 恢复、房间暂停、历史保留、连接持续在线。模拟网络断开后补发消息且页面没有重复；实际重启后端后自动重连，房间、角色绑定和存档仍可读。下载两种日志，检查过滤、结构和凭据；验证 URL 由页面 origin 推导、没有 token query、主机 token 不在 localStorage、窄屏无横向溢出、退出清除凭据。

多人验收成功报告：`.cache/rooms-smoke-1415d654015b461e83e363d46bfaf0c1/report.json`。同目录各 host/player/outsider 子目录包含截图和导出文件。

原车卡真实浏览器回归全部通过：随机、购点超支修正、技能编辑、最终确认、刷新、JSON 下载／导入、后端重启、系统健康／离线模型目录和已认证 echo。成功报告：`.cache/character-smoke-be0b333ff8224a4c8c806fcb1e0deaeb/report.json`。

验收使用真实 HTTP、SQLite、WebSocket 和 Chrome，不是两个页面共享 localStorage。没有模拟实际第二台物理机器；局域网兼容性通过独立浏览器上下文与同源地址／Vite 代理验证。所有临时 Chrome、Node/Vite、FastAPI 进程均已关闭，8000／5173 端口已释放。未调用 Ollama、外部模型 API 或任何模型推理。

## 用户数据与启动

用户 `data/game.db` 只做只读完整性检查，没有运行用户数据库的初始化／迁移／写入。开始和结束 SHA-256 均为 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。所有测试和浏览器数据均在独立临时数据库。被忽略的 .env 按计划新增主机密钥。

本机默认启动：`uv run --directory backend python -m app.main`，另一个终端 `npm --prefix frontend run dev`。页面为 `http://127.0.0.1:5173`。

局域网启动（分别两个终端，仓库根目录）：

```powershell
uv run --directory backend uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
npm --prefix frontend run dev -- --host 0.0.0.0
```

主机查看密钥：`uv run --directory backend python scripts/host_key.py --show`。通过 ipconfig 查实际 IPv4，玩家打开 `http://<主机IPv4>:5173/#/rooms`，输入邀请码和显示名。只需在可信专用网络放行前端 TCP 5173。两个终端分别 Ctrl+C 停止服务。Ollama 继续保持 `127.0.0.1:11434`。

## 限制与后续接口

本版只用于可信局域网的单主机／单 worker。无 TLS、公网限流／账号和多进程广播；首次重放及导出面向小型私人团，超大历史需后续流式／分页优化。邀请码刷新后需重新生成，因为不保存明文。成员凭据丢失后需由主机移出旧席位再重新加入；没有远程角色上传／凭据找回。

下一批 KP、队友 Agent 和模组状态机应使用 `RoomService.command`、严格请求模型、SessionStateV1 和 RoomEvent，显式声明 actor/operator 权限，保持服务端骰子和提交后广播，避免直接操作 ORM 或经 WebSocket 另建业务逻辑。详见 [多人协议](multiplayer-protocol.md)。

## git status --short

```text
 M .env.example
 M README.md
 M backend/app/api/websocket.py
 M backend/app/config.py
 M backend/app/main.py
 M backend/app/persistence/database.py
 M backend/scripts/check_character_creation.py
 M backend/tests/conftest.py
 M backend/tests/test_characters_api.py
 M backend/tests/test_model_status.py
 M backend/tests/test_websocket.py
 M frontend/.env.example
 M frontend/src/App.css
 M frontend/src/App.tsx
 M frontend/src/api/characters.ts
 M frontend/src/pages/SystemStatusPage.tsx
 M frontend/vite.config.ts
?? backend/app/api/rooms.py
?? backend/app/auth.py
?? backend/app/persistence/room_models.py
?? backend/app/rooms/realtime.py
?? backend/app/rooms/schemas.py
?? backend/app/rooms/service.py
?? backend/scripts/check_multiplayer.py
?? backend/scripts/host_key.py
?? backend/tests/test_rooms.py
?? docs/
?? frontend/src/api/rooms.ts
?? frontend/src/api/session.ts
?? frontend/src/components/HostUnlock.tsx
?? frontend/src/pages/RoomsPage.tsx
```

## git diff --stat

原命令只统计已跟踪文件的修改，尚未跟踪的新文件列在上面的 status 中；没有为改变统计结果而执行 git add。

```text
 .env.example                                |  2 +
 README.md                                   | 76 +++++++++++++++++++----------
 backend/app/api/websocket.py                | 18 ++++++-
 backend/app/config.py                       |  1 +
 backend/app/main.py                         | 58 ++++++++++++++++++++--
 backend/app/persistence/database.py         |  3 +-
 backend/scripts/check_character_creation.py | 13 +++--
 backend/tests/conftest.py                   |  7 ++-
 backend/tests/test_characters_api.py        |  5 +-
 backend/tests/test_model_status.py          | 12 +++--
 backend/tests/test_websocket.py             |  4 ++
 frontend/.env.example                       |  4 +-
 frontend/src/App.css                        | 12 +++++
 frontend/src/App.tsx                        | 10 +++-
 frontend/src/api/characters.ts              |  6 ++-
 frontend/src/pages/SystemStatusPage.tsx     | 12 +++--
 frontend/vite.config.ts                     |  8 ++-
 17 files changed, 199 insertions(+), 52 deletions(-)
```
