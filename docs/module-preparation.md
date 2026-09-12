# 模组准备与公开调查板

模组准备把本地索引中的有限证据整理为待审阅实体。主机批准后，房间保存该版本的快照。模型检索到原文不等于获准向玩家公开；公开事实以房间实体状态和权威事件为准。

## 主机操作

1. 解锁主机，先在知识来源页完成文本索引。打开「模组准备」，选择来源，填写任务名称和物理页码范围；也可按章节标题过滤。范围留空表示整个来源，但仍受三批生成预算限制。
2. 创建任务，点击「生成实体草稿」。查看进度、调用次数和错误。每次最多三批、六次实际模型请求，每批最多六个实体、八条关系，格式错误只修复一次。生成服务与游戏共用串行模型网关。
3. 逐项展开有限证据摘录，核对标题、类型、私密摘要、公开摘要、来源页码、建议检定、公开条件和验证错误。公开摘要由主机独立审阅，不从私密摘要自动填充。修改后先保存再批准；已批准项须返回草稿才能修改。
4. 拒绝不合适的草稿；模糊重复项保留给主机判断。也可手工创建 `generated_by=host` 的实体，它不强制引用模型证据。
5. 设置一个已批准场景为初始场景，勾选开场必要实体。至少还需一个已批准人物、地点、线索或物品；所有勾选的必要实体必须批准。关系只有在两端实体均批准后才能批准，`leads_to` 两端必须为场景。最后批准准备版本。
6. 在尚未开始的房间中选择并绑定该准备版本，再设置 KP 和调查员 Agent、角色席位并开始游戏。绑定即公开初始场景及明确设置为初始公开的实体。
7. 游戏进入主机审阅等待时，在主机实体面板核对提议、证据和公开摘要，选择批准、编辑后批准或拒绝。原回合继续；等待期间没有模型请求。玩家只收到通用等待／结果提示。

建议检定和公开条件提供最小 JSON 编辑器。条件可以引用当前场景、已公开的前置实体，以及同回合真实成功的技能／属性检定。实体 ID 可从主机 API 获取；此版本不提供关系图编辑器。

## 生命周期与数据

准备任务状态为 `created → extracting → review_ready → approved`。失败进入 `failed`，来源当前 hash 改变进入 `stale`。修改实体、关系或初始场景会使准备版本增加，并要求重新批准。旧房间保留旧绑定，新的房间不能绑定 stale 任务；请按新的 source hash 新建准备任务。索引中缺少已绑定版本时沿用 `knowledge_missing`，不静默替换来源。

实体类型固定为 `scene/npc/location/clue/item`，审批状态为 `draft/approved/rejected`。实体保存公开与私密摘要、证据、页码、置信度、检定、公开条件、标签、生成来源、主机编辑标志和版本。关系类型固定为 `located_in/appears_in/reveals/requires/leads_to/related_to`，使用简单审阅列表。

服务端为真正进入本批生成输入的摘录分配 evidence ID，并先持久化 generation run。输出必须引用该 run 的同 source ID/hash、合法页码证据；模型伪造 ID 或引用别批证据会留下验证错误，不能批准。每个摘录最长 420 字，原文不会被模型摘要替换。去重仅合并规范化标题相同、类型相同且证据 chunk 重叠的实体；不确定项继续保留。

新增表：`module_preparations`、`module_generation_runs`、`module_entities`、`module_entity_relations`、`room_module_preparation_bindings`、`room_entity_states`、`host_review_requests`、`preparation_save_states`。使用外键、独立状态列和唯一约束；不修改原有表列。新增活动回合唯一索引包含审阅等待状态。

## 批准、公开与修正

`approved` 表示主机允许房间使用该实体；未公开实体仍只进入 KP 上下文。`revealed` 表示工具验证条件后已公开，玩家获得冻结公开摘要和有限来源引用。`corrected` 表示主机追加了公开修正。修正保留旧事件，停用对应旧 observation，并追加新记忆。

房间绑定复制所有已批准实体及关系，之后编辑准备任务不会改变房间。`RoomEntityState` 是准备模组的实体状态唯一来源。原 `ModuleSnapshot` 只保留当前公开场景的兼容投影；`reveal_clue` 和 `update_scene` 在准备模组中转到新工具，原创《停摆的钟楼》继续使用既有流程。

公开不能撤回。重复揭示不会重复写事件；更早存档也不会抹去玩家后来已经收到的公开或修正。调查板按人物、地点、线索、物品展示标题、公开摘要、发现时间、来源和修正标记；场景另行显示。

## 审阅与 LangGraph

KP 新工具为 `inspect_approved_entities`、`reveal_entity`、`transition_scene`、`propose_module_fact` 和 `request_host_review`。调查员使用 `inspect_public_entities`。工具只接受当前房间批准实体 ID；进入没有已批准 `leads_to` 的场景需主机审阅。新事实必须引用当前 run 的同版本模组 evidence，没有合法证据则返回 `needs_host_ruling`。

每回合最多一条审阅请求，数据库同时约束房间最多一条 pending 请求。图先执行可批准动作，再经过 `wait_for_host_review`；检定请求延后到审阅批准之后创建，不建立两个并行等待项。检定结果后的新提议仍经过第二个审阅节点，但共用同回合一次上限。

等待状态持久化 `wait_reason=host_review`、pending review ID 和 checkpoint；真人检定使用独立的 `human_roll`。主机结果写入数据库后恢复同一 cycle，工具回执、审阅应用和终态响应均幂等。拒绝后只允许一次安全公开改写，不再继续提议、检定、队友或摘要；改写失败直接输出待主机裁定并结束回合。

有证据的新事实经主机确认后成为该房间专属的 approved 实体快照，保留审阅 ID 和引用；它不修改可被其他房间使用的全局准备版本。对已经公开实体的变更使用 correction。

## 权限、上下文与存档

准备、实体、关系、证据和完整审阅 API 均仅主机可访问。玩家接口使用显式字段白名单；不包含 keeper summary、隐藏关系、公开条件、待审阅正文或 evidence 摘录。私密审阅事件使用 `host_only`；公开事件仅包含通用状态或已确认摘要。

KP 只得到房间已批准实体、批准关系、当前场景、当前 run 检索证据及允许的事件和记忆。检索证据验证仅使用实际注入的裁剪摘录；未注入候选不能用于提议或引用。准备模组使用独立的简短 KP 提示和工具清单，其他成员的数值通过 inspect_character 按需读取。草稿和拒绝项不进入游戏上下文，也不能写 canonical memory。公开叙事与调查员获得同真人一致的 `public_entities` 集合；KP 的重复公开投影压缩为 ID 和状态，完整冻结摘要保留在批准实体中以节省预算。

存档同事务保存准备绑定、版本/hash、全部房间实体快照、当前场景、公开状态和审阅记录；既有 Agent 存档保存等待原因和 cycle/checkpoint 引用。重启保留审阅等待；读档只恢复仍 pending 的请求，已解决请求不回退。认证 token 和 WebSocket 连接不参与存档。公开事实保持单调，当前场景可回到存档位置，历史日志不删除。

继续复用 JSONL／Markdown 导出及房间 WebSocket 快照协议。新增 `review.waiting/status`、`review.requested/resolved`（私密）、`entity.revealed/corrected` 和 `scene.updated` 等事件；页面标识 KP、AI 队友、主机审阅、场景、线索、检定和 Agent。

## API

主机准备接口：GET/POST `/api/module-preparations`，GET/PATCH `/{id}`，POST `/{id}/generate`、`/approve`、`/refresh-status`，GET `/{id}/entities`、`/relations`、`/evidence/{evidence_id}`。

实体：POST `/api/module-entities`，PATCH `/{id}`，POST `/{id}/approve`、`/reject`、`/draft`。关系：POST `/api/module-relations/{id}/approve`、`/reject`。

房间前缀 `/api/rooms/{room_id}`：PATCH `/module-preparation`，GET `/public-entities`、`/host-entities`、`/review-requests`；POST `/review-requests/{id}/approve`、`/edit-and-approve`、`/reject`；主机手动公开／修正使用 `/entities/{id}/reveal`、`/correct`。具体结构见 `app/preparation/schemas.py` 与 OpenAPI。

## 当前范围

生成器只处理已有索引文本，不自动读取图片/地图/OCR，不下载模型。第十六批支持全文分段串行准备；每窗口仍为3批/6调用，完成段和稳定证据复用，跨段同一实体合并来源并保留已校对字段。生成覆盖不等于内容正确，仍须逐项来源校对；校对者和依据必须如实记录，不把Codex校对标成用户人工审核。工作台保留最近30条准备操作记录，不是完整内容管理审计系统。

## 第十六批：完整包和房间交互

实体可配置 `source_block_ids`、`reviewed_by`、`review_basis`、`combat_template`、`interactions`、`check_adjustments`。模型草稿不能自己写入批准战斗数值、交互和校对者。批准时校验当前来源IR、引用和绑定。已有批准版本通过 `backend/scripts/module_package.py audit/load/export` 导出与加载，常暗全文包路径、覆盖统计及缺项见 [第十六批报告](batch-16-report.md)。

主机 `POST /api/rooms/{room_id}/module-action` 确认已批准交互，参数为 `entity_id`、`interaction_id`、`source_event_seq`、`evidence_quote`、`reason`。必须引用实际玩家/AI行动，核对当前场景、已揭示目标、物品/事件及同次真实检定。对应模型工具为 `apply_module_action`。普通叙事不改库存、标志或结局。`module_runtime` 随房间存档保存；主机导航可见，玩家只读实际公开回执和持有物投影。

主机对未公开实体使用 `/correct` 可先校对冻结公开摘要，产生私密 `entity.disclosure_reviewed`；该操作仍保持隐藏，后续实际揭示才发布修正后的摘要。已公开实体继续使用公开纠错。未来结局和条件性遭遇不得因场景绑定直接公布。全文覆盖清单、准备批准和完整机制可运行性分别验收。
