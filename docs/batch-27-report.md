# 第二十七批：异地接入与远程玩家提交角色

## 结果

**房间角色提交、主机接受后冻结开局、Tailscale 启动检测已开发并完成本机定向验证。真实异地设备验收待验。** 本机未找到 Tailscale CLI，未使用不同网络的第二台设备，没有把模拟 CLI 地址或同机浏览器计作异地成功。

执行前核对 HEAD 为 `a84d5dd2e54f217d37fdd768f789f9a6ed662c86`（repair no-check），与审查基线一致，分支 `main`，没有后续提交可跳过；跟踪文件未见修改。已读第 26 批报告、清单和当前 [规则来源](../backend/app/rules/definitions/SOURCES.md)。沿用当前分支、单执行者，无 worktree、commit 或 push。`.env`、原稿、批准包和原存档未修改。初始 Git 扫描对一个已忽略的第 23 批历史临时目录报权限警告，本批未访问或改动该目录。

第 26 批免检定示意图发现修复、三类自定义专业和 API 审计修正保留为已交付，不再列为待修缺陷；不把浏览器／确定性检定证据写成模型自然使用了每类专业。本批未发现需要先行修复的新 P0，未扩写裁定系统。

## 实现

### 房间角色提交

- 新增房间作用域的 `/character-submissions`：列表、详情、无持久化预览、提交、接受／拒绝。身份来自当前房间成员令牌，请求模型拒绝额外 `member_id`；全局 `/api/characters` 权限边界未放开。
- 请求流读取上限 **256 KiB**，包括无 Content-Length 的请求；格式错误给出字段路径，规则元数据、版本与完整角色校验分别返回明确错误。接受现有 `CharacterExport`，支持当前 1.1.0 及原有可用归档版本。
- `CharacterService.prepare_import()` 抽取既有导入重算；`finalize_candidate()` 抽取既有正式确认。最终技能、半值／五分之一值、属性派生、点数、资产和 validation 均由服务端重新计算，上传 finalized 不获信任；年龄、职业、点池、装备和自定义专业由原引擎校验。不调用创建／掷骰流程；原骰数值、时间、公式和顺序保留，记录来源为 imported，新记录 ID 避免与主机库碰撞。来源声明不意味着本主机证明了文件原骰的真实性。
- 新增 `room_character_submissions` 表，无需改已有表：房间＋成员＋版本唯一，一个当前 pending 的部分唯一索引。替换将旧 pending 记为 rejected 并保留历史；前端与服务端都阻止旧版批准。无效文件不落提交记录，预览、待处理和拒绝不生成角色库草稿。
- `SubmissionService.view()` 共用于房间快照、列表及详情；完整重算卡、来源和拒绝原因仅主机／提交者可见。其他成员只收到成员、版本、状态及时间。WebSocket 实时广播与重连回放使用原房间投影及事件过滤；提交事件仅含必要状态，无原始角色正文。
- 接受在原 `BEGIN IMMEDIATE` 事务及房间锁内完成：重验→正式确认→角色库保存→冻结快照／资源初始化→发布→按原分配逻辑分配→提交状态及请求回执。`CharacterRepository.save_in_session()` 参与同一事务，不另开提交。没有重复计算引擎或另一套分配语义。
- 沿用 `RoomEvent` 的房间＋操作者＋client_request_id 去重和请求摘要校验；同 ID 不同内容冲突。事务中途失败全部回滚；提交已落库但响应丢失、重启或并发重复点击不会再创建角色、席位或初始化事件。
- 已有分配时显示当前角色，并要求先按原流程取消分配；只在 lobby 接受新提交与审批，开始后包括 paused 均关闭。原本机发布、空闲席位自选、准备和开始继续可用。
- 前端独立组件提供文件选择、重算预览、三类专业名称和值、状态／错误说明、来源／原骰以及主机预览与接受／拒绝。审批仅属于角色准备流程，普通游戏行动未加审批。

### Tailscale 启动

`start.cmd` 已将参数原样交给启动器，因此直接复用；新增 `launch.py --tailscale` 和独立 `tailscale_access.py`。前端网络监听复用原 `--lan` 机制；`frontend/vite.config.ts` 的 HTTP／WebSocket 同源代理无需修改。默认本机启动、原 `--lan`、自定义前端端口与后端/Ollama 监听保持原语义。

检测从 PATH 和 Windows 常见安装目录寻找 CLI，对 `status --json`、`ip -4` 各设置 3 秒超时。仅用 BackendState 和 Self 的连接／地址字段验证本机 IPv4，服务就绪后显示本机入口及标注清楚的 Tailscale 异地加入地址；不输出 Peer/User 清单，不把普通网卡地址作为异地地址。缺失、未登录、等待设备批准、离线、超时或地址不一致分别提示下一步，本机功能可继续用。

本批只读核对 [Windows 安装](https://tailscale.com/docs/install/windows)、[连接设备](https://tailscale.com/docs/how-to/connect-to-devices)、[CLI](https://tailscale.com/docs/reference/tailscale-cli) 官方说明。未安装／登录 Tailscale、未发邀请、未改网络策略，未调用推理模型。Ctrl+C 复用原 Windows Job Object，仅关闭本次前后端。

## 本次验证及证据

证据根目录：`data/prepared/changan/batch-27/`（Git 忽略；源码测试与脚本可从零生成夹具）。本轮代码为上述 HEAD 加未提交的第 27 批改动，没有虚构新提交号。

| 条件 | 实际使用 |
| --- | --- |
| 日期／平台 | 2026-09-16；Windows 11，10.0.26200；Python 3.12.7；Node 22.14.0 |
| 浏览器 | Chrome 152.0.7977.83，主机／提交者／另一成员三个独立临时配置 |
| 网络 | 同一 Windows 主机；浏览器访问回环前端 5187，Vite 转发至回环后端 8027 |
| 模型 | pytest 拒绝外部 HTTP；浏览器／启动器使用隔离的未配置 openai provider、`unconfigured-api`、example.test、空密钥；没有实际使用推理模型 |
| 推理 | 本批新增调用 **0**；最终浏览器隔离库 `agent_runs=0` |
| 规则目录 | `coc7-character-creation` 1.1.0，**31 职业／103 目录技能**；浏览器提交卡另外 **3 个自定义条目**，未混入目录总数 |

| 检查 | 结果／证据 |
| --- | --- |
| 新增定向测试 | **30 passed，21.83 秒**；`tests-c2.log/xml`。首轮 28 项通过保留为 `tests-c1.log/xml`，不叠加统计 |
| 受影响回归 | **165 passed，159.04 秒**；`regression-c1.log/xml`，仅运行 `test_rooms.py`、`test_characters_api.py`、`test_coc7_creation.py`、`test_batch26.py` |
| 前端 | `npm run lint`、`npm run build` 通过 |
| 静态检查 | 所有新增／修改 Python 文件 Ruff、`git diff --check` 通过 |
| 真实浏览器流程 | `ui-c3/report.json`：三身份加入、格式错误、三类专业预览、篡改技能／HP 被重算、替换阻止旧版批准、拒绝重提、接受分配、准备开局通过 |
| 权限与状态同步 | 同机另一成员详情 403，实时 WebSocket 不含他人提交卡／背景；跨房间、列表、事件日志和重连回放过滤由 API 测试通过。本机消息同步及浏览器模拟短断网重连通过 |
| 事务与重试 | 测试注入分配后提交前异常，验证角色／席位／事件回滚；另注入 commit 后响应丢失，重启再发原请求；并发重复接受均只写一份 |
| Tailscale | CLI 缺失、未登录、运行 IPv4、自定义端口、等待批准、离线、错误地址、超时、服务错误及安装路径探测通过；正常 IPv4 属于模拟 CLI 输入 |
| 真实 Windows 启停 | `ui-c3/launcher.log`：`start.cmd --tailscale --frontend-port 5187`，HTTP/WS 就绪、实际 CLI 缺失提示、真正 Ctrl+C、退出码 **0**、8027／5187 释放 |
| 真实异地／自然行动 | **待验**；无第二台不同网络设备，未执行自然模型行动。可照做的验收步骤见 [清单](batch-27-checklist.md#真实异地验收待执行不能用同机浏览器代替) |

最终浏览器房间为 `9b6fabf1-08a3-4995-8fbb-9f93cacf023a`。已查看 `ui-c3/host/pending.png`、`ui-c3/submitter/preview.png`、`running-reconnected.png`，三类专业和值可见、无页面遮挡。确定性测试只有既有 Starlette/AnyIO 弃用提示，无测试失败。

### 保留的非产品故障记录

- `ui-c1`：沙箱内 Chrome GPU 子进程权限受限，CDP 连接断开；该轮 HTTP 就绪及启动器退出 0 仍有记录。未复用用户浏览器配置。
- `ui-c2`：获准的沙箱外 Chrome 已走完整流程，但测试运行器自身在发送 Ctrl+C 时退出，没有最终 report。保留原数据库、截图和正常启动器关闭日志，不将其缺失报告补写为通过。
- 仅修正测试控制台：运行器分离并使用隐藏独立控制台，明确忽略 Ctrl+C，发送前先写入报告；生产启动器没有为此改变进程清理逻辑。新目录 `ui-c3` 完成全流程和正常关闭。无模型调用、原骰重掷或旧失败记录覆盖。

## 保留结论与后续

沿用第 20／21 批 **77 分 58.10 秒**长测及恢复、第 23 批真实 OpenAI 接入、第 24–26 批既有调查／摘要／恢复结论，本批未独立重跑历史实测。SAN、扩展检定、基础战斗和完整模组数据均已有实现；新搜索自然成功骰分支、A/C 全路线及正式包 OpenAI 复验分别记为专项待验。

全功能清单区分“已开发”“部分完成”“已有实现但待验证”“未开发”，见 [development-checklist.md](development-checklist.md)。后续依次补有来源的其余自定义专业与职业，再按实际模组补装备和规则扩展。90+ 待完整条款；正式包 OpenAI 仍待既有具体外发授权，本批不为此阻塞。Cloudflare Tunnel、匿名公网托管和 P2 图像／OCR／独立 NPC Agent／语音／向量检索留后续。定向验收完成后停止扩展测试。
