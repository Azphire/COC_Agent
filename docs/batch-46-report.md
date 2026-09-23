# 第46批：KP答复覆盖与队友委派执行

基线：`0f3043828eb25133938add9dd18ac70dc259fed7`（repair），2026-09-23。沿原工作区实施，不commit/push，不改变准备包、角色、存档或模型设置，不新增外部API调用。第45批真实同次流式与8132→7201 tokens公平输入对照保留原结论，本批不重复计作待完成项或重新测性能对照。

## 实现与边界

- 复用本轮已解析请求和最终选定公开来源，生成稳定`answer_requirements`，分别处理KP观察、历史问题、队友请求与表达格式。`answer_coverage`绑定需求ID、正文实际原样片段、来源ID和来源片段；附属细节、NPC发言及仅填写ID不能代替正文回答。
- 正文覆盖检查使用实际来源的数量、归属、估计、未知状态及相关语义约束，不编码固定模组答案。它是保守发布契约，不是通用自然语言语义证明；真实题仍单独逐项核对原正文和选定来源。
- 缺项进入原一次修复预算，携带缺失需求、来源和已验证片段；原片段先通过公开、来源及结果校验，再要求修复保留。完整覆盖仅在最终检查，未结束前缀继续执行公开、来源及结果保护，不因尚未回答后续问题而拒绝前缀。
- 原生回答、修复回答与服务端回退分别记录`answer_origin`。逐调用保存原输出和覆盖审计，正式发布另记`answer_complete`。回退保留安全的`observed_detail`及对应映射，并明确告知未完整生成；正式事件、日志和供记忆读取的正文使用同一最终文本。
- 队友已选择act/assist后，复用`bind_task_operands`补充唯一、当前可操作且有请求依据的目标；歧义保留有限候选，沿原澄清路径处理。动态schema分别约束实体ID与真实可用物品实例ID，保留讨论、拒绝和自行选择做法。
- 规范化后重新计算fingerprint。技术失败豁免须同时匹配原请求key、目标、操作及尚未执行的子回合；旧无来源冷却、无关失败任务、已执行动作和取消不取得豁免。主机行为日志另存规范化前后数据，原模型输出不覆盖。

## 原失败只读复现

[real-07冷却来源](../data/prepared/batch-46/real07-cooldown-source.json)与[原候选离线回放](../data/prepared/batch-46/real07-teammate-target-replay.json)保留原DB/WAL校验。原两份队友候选将窗锁线索ID填入物品实例字段，target为null；原冷却也是null目标，fingerprint同为`0d886d…`，触发`target_action_cooldown`。补齐请求已经绑定的当前窗锁目标后fingerprint为`718633…`，两候选均通过行为策略。此为离线复现，不计作真实执行成功，也未清除旧冷却。

KP首次3个覆盖反例及队友首次16个反例的失败日志分别保留在[kp-coverage](../data/prepared/batch-46/kp-coverage/baseline-first-failure.log)和[teammate-targets-first-failure.txt](../data/prepared/batch-46/teammate-targets-first-failure.txt)。独立审查另保存[四类语义误放行](../data/prepared/batch-46/coverage-review-probes.json)，后续修复及同题复验与首败分列。

## 定向验证

首轮第42–45批及动作恢复回归为261通过、3失败，原日志保留[affected-tests-01.txt](../data/prepared/batch-46/affected-tests-01.txt)。没有运行全量测试。失败涉及泛场景观察契约与旧流式mock、提示说明旧措辞断言，以及第44批取证器假设最后一条模型消息恒为JSON；取证器现从实际messages定位原JSON上下文，保留修复提示和每次调用。

真实验收前第42–46批及动作恢复矩阵 **325项通过**，见[final-affected-tests.txt](../data/prepared/batch-46/final-affected-tests.txt)。随后补充纯格式问句和同文不同说话人的来源去重，KP定向 **34项通过**，见[format-and-attribution.log](../data/prepared/batch-46/kp-coverage/format-and-attribution.log)。队友定向及原第37批两项受影响用例 **22项通过**；验收驱动含调用排序反例 **9项通过**。真实失败后的受影响复验另列下节，各组重叠，不相加成唯一用例总数。

生成／修复／失败回退两条实链测试通过；追加测试确认修复稿即使覆盖完整，也不能覆盖首稿已验证片段，失败后保留原正文和未完成状态。相关日志为[runtime-tests-03.txt](../data/prepared/batch-46/runtime-tests-03.txt)、[retained-answer-validation-02.txt](../data/prepared/batch-46/retained-answer-validation-02.txt)。前端流式状态机9项通过，见[frontend-stream.txt](../data/prepared/batch-46/frontend-stream.txt)。全部18个本批Python变更文件Ruff通过，`git diff --check`退出0。Windows工作区测试目录ACL导致的首次启动错误与测试夹具错误均保留，未计为产品通过证据。原第37批重试夹具补足同请求／目标／操作／执行回合来源；第43批正常流式夹具补合法覆盖映射，保持原正文、单次调用和增量断言。

## 真实验收

入口：`backend/scripts/validate_batch46.py --name <新目录>`。仅从第45批`real-05`已预检准备做只读SQLite backup（含WAL），沿既有正常HTTP换凭据、重新分配原卡、ready/resume流程恢复。主问题与独立窗锁委派不换题，不重做前置问话、聊天填窗或压缩。主回合正文、延迟、两成员浏览器记录和结算快照在委派前冻结并校验hash。

`real-01`在端口预检因5146被已有进程占用而退出，模型调用0，尚未执行准备恢复或固定题；原失败目录及[准备失败说明](../data/prepared/batch-46/driver-preparation-failures.md)保留，未停止未知进程。`real-02`仅将本批隔离服务端口改为8146／5246，使用相同源准备及固定题。

### real-02：完整保留的失败运行

本次主回合的两次KP输出均未通过。首次正文只回答书架空档与窗户；历史数量、未知书名和归属只写在覆盖映射内，没有出现在正文，不能算回答。原wire schema还把来源片段列为可选，导致两次输出都合法省略该字段，随后被覆盖检查拒绝。正式发布为`server_fallback`、`answer_complete=false`，并显示“本次答复未完整生成，未答部分仍待补充。”，未静默标完整。

主回合5次本地模型调用，实际input/output为28571/1993 tokens，玩家提交到首DOM为70.974282秒。最终两窗口各一个正式气泡，与历史一致且没有浏览器错误；由于两次生成均失败，本次不能证明成功正式正文的同次增量与生成中重连。原始采证的模型调用排序曾将修复稿误标为首稿；原文件没有覆盖，[独立补充审计](../data/prepared/batch-46/real-02/supplemental-assessment.json)按实际请求开始时间另列首稿及修复稿，驱动已补排序反例。

独立委派成功规范到当前窗锁实体`3160e2af-5168-4ffd-88b2-e6207ab7c374`，选择assist并创建实际队友子回合，没有再被旧null目标冷却拒绝。但唯一工具回执是`already_revealed=true`，来源e230属于主回合；任务没有产生本次非零操作。原结算器错误地把计划中的`observation_completed`和行动提议e265合成为成功回执，销掉请求243:3。这是任务完成假阳性，不能算任务执行通过；本批据此补结算反例。任务根回合及子回合合计7次模型调用，input/output为34078/2042 tokens。

非零幂等单独通过：选用主回合实际新揭示e230，使用原正文、原`client_request_id`正常重送，再经正常暂停／恢复重送同一请求。两次均复用原事件和回合；回执、时间、资源、骰、任务进度、模型调用等11项快照一致，没有新增模型调用。该证据是已结算操作重放，不外推为执行中崩溃恢复。见[idempotency.json](../data/prepared/batch-46/real-02/idempotency.json)。委派和重放后主回合三个冻结文件的SHA均未变化。

real-02共12次本地模型调用，实际input/output为62649/4035 tokens，外部API调用0。不同回合和不同运行的结果分列，不拼接成一次通过。

### 针对real-02的同题修复

来源ID、来源原文和状态改为生成schema必填，存档读取仍兼容旧默认值。纯观察继续使用`observed_detail`；含历史或其他问题的混合请求使用完整`public_narration`，原观察权限约束保留。修复提示明确先把缺项答入正文，再摘取映射。wire反例首次3失败／1通过日志与修复后wire、覆盖、第43批流式、第45批请求／观察及runtime合计100通过日志分别保留在[real02-wire-first-failure.log](../data/prepared/batch-46/kp-coverage/real02-wire-first-failure.log)和[real02-wire-fixed.log](../data/prepared/batch-46/kp-coverage/real02-wire-fixed.log)。

只读观察结算现在要求当前队友子回合有通过验证且与正式事件一致的KP正文，来源使用该正式结果事件；行动提议、旧揭示回执、未验证正文及失败回退均不能合成为本次成功。真实检定／模块／战斗操作的成败继续独立结算，旁白失败不能抹去已发生操作。新增六个实路径分支中，四个错误结算反例有真实首败；正向夹具另修正为既有`result_event_seqs`字段。六分支及队友、第43批结果、第44／45批任务共 **69项通过**，见[observation-settlement-validation.txt](../data/prepared/batch-46/observation-settlement-validation.txt)。原失败库仅做[只读审计](../data/prepared/batch-46/real02-observation-false-completion-audit.json)，没有把旧错误账本改写成通过。此时20个变更Python文件Ruff及`git diff --check`均通过，见[静态检查记录](../data/prepared/batch-46/static-verification-post-real02.txt)。

### real-03：正文完整，映射失败

同准备、同题复验的原生稿与修复稿正文均已覆盖固定题：书架几处空档显眼、通屋外窗户、托马斯早先估计六本且不知具体书名，并明确来自此前陈述。人工逐项核对本轮选定scene、e49和公开委托来源无冲突。首稿原文为：

> 你环顾道格拉斯的书房，注意到书架上的几处空档十分显眼，还有一扇通向屋外的窗户。托马斯早先估计少了六本书，但他并不知道具体书名。这些信息来自他之前的陈述，而你目前的观察是书房的布局和窗户的状态。

但是首稿映射把对象名／原问题当作`body_quote`，并把来源“显眼”误写成“显y”；修复稿又把来源原文填为正文片段。拒绝不实映射符合契约，但导致语义完整正文丢失，属于映射可用性缺陷，不能再描述成模型正文仍漏答。正式发布仍为不完整回退，因此该运行KP正式答复未通过。主回合首DOM74.091149秒；双窗口最终各一气泡与历史一致，成功正式正文的同次增量／重连仍未通过。

独立委派选中正确窗锁并进入实际子回合；唯一工具回执仍复用主回合旧e230，子回合没有有效正式观察正文。本次账本正确保留请求243:3、执行者、窗锁目标和`generation_failed`，技术失败来源限定为当前子回合、`executed=false`，没有再把提议或旧揭示标为成功。旧e133失败检定另存，没有冒充当前回执。真实失败保留通过，任务完成未通过。

本次主回合5调用input/output为28810/2523，独立任务含子回合7调用34525/1858，合计12调用63335/4381；每阶段耗时见[metrics-audit.json](../data/prepared/batch-46/real-03/metrics-audit.json)。主回合新揭示e230再次经原请求重送及正常暂停／恢复后原请求重送，11项快照均一致；主冻结hash保持。[独立审计](../data/prepared/batch-46/real-03/supplemental-assessment.json)与[来源保护核对](../data/prepared/batch-46/real-03/source-protection-audit.json)保留了真实结果：源三DB/WAL、准备包和模型配置hash一致，角色和profile原文一致，没有新增或改写源存档。

### 针对real-03的映射与回退修复

增加确定性的映射规范化：仅处理已声明的合法需求和该需求允许的来源，从实际正文的完整句／相邻句中找到唯一最小有效片段，并从该来源提取真实原文；候选仍须通过完整语义约束。只修`body_quote/source_quote`，不改正文、需求、来源ID或状态。非法ID、错源、缺回答、多解或冲突仍拒绝。原始输出及原始覆盖审计独立保存，另记有效映射和规范化过程，不把规范化计作额外模型调用或模型修复。相关覆盖、wire、第43批流式及第45批请求／观察 **110项通过**，见[normalization-affected.log](../data/prepared/batch-46/kp-coverage/normalization-affected.log)。

回退复核另发现：一个错误历史句或错误观察极性，会使整段拒绝并丢掉同稿已验证片段。两个真实首败保留在[mixed-retention-contract-and-first-failure.txt](../data/prepared/batch-46/mixed-retention-contract-and-first-failure.txt)。现逐片通过原公开／来源／结果边界、按首次有效内容保留，并在每次拼接及保存前重新检查整体；重建正文保留实际操作回执。错误字段类型不能使原始日志读取中断。即使拼出的安全片段覆盖全部需求，仍记录`server_fallback`、`answer_complete=false`并显式提示生成失败。首稿原输出保留测试亦覆盖适配器先解析schema的实际记录顺序。

最后运行链路、片段保留、拼接隐私、第43批结果／NPC及当前观察结算 **40项通过**，见[normalization-runtime-retention-final04.txt](../data/prepared/batch-46/normalization-runtime-retention-final04.txt)。拼接隐私用例首次执行时修复已落地，首跑即通过，不能算首败。中间一次命令使用不存在的测试文件未收集用例；旧片段保留夹具随后更新为允许保留修复稿独立通过的缺项，同时继续拒绝替换首稿验证片段，并要求回退标记。这些过程日志均未覆盖。

本批浏览器观察器按`cycle/stream/attempt`记录每次真实可见前缀后的断线，每个版本最多一次；只以最终版本自身的重连快照判定通过，不拿失败首稿补修复稿证据。两版本相同前缀的实际观察器JS反例及采证驱动 **11项通过**。产品前端代码未改，原第43批前端状态机9项通过仍保留。最终22个Python变更文件Ruff及diff检查通过，见[冻结记录](../data/prepared/batch-46/static-verification-real04.txt)。

### real-04：最终真实运行仍未通过

本次原生稿与修复稿正文都实际回答了两观察、托马斯早先的六本估计及具体书名未知，逐项来源对照见[本轮人工核对](../data/prepared/batch-46/kp-coverage/real04-manual-main-audit.md)。原始映射仍错误地摘取对象名／原问题；规范化后的首稿仅数量项通过，修复稿两项历史需求通过。观察的重复提及和后续建议使唯一片段规则拒绝规范化，不能据人工认为正文完整就绕过契约发布。没有被接受的生成attempt，正式结果仍为`server_fallback`、`answer_complete=false`。

本次主回合6次模型调用（队友决策亦使用一次原有行为修复），input/output为35904/2905 tokens；提交到首DOM70.872秒。两成员最终各一正式气泡，与AdjudicationRecord、正式事件和历史完全一致。观察成员在attempt 1、2各有一次真实断线，但最终是回退，仍不能算成功正式正文的同次流式验收。后续独立任务的`recent_dialogue`及实际发送messages使用同一正式回退正文，没有把拒绝的原稿混入公开记忆；持久事实记忆单独保留真实窗锁e230来源，不冒充新旁白事实。见[正式结果一致性](../data/prepared/batch-46/real-04/formal-result-consistency.json)。

独立委派只有Plan和Summary两次调用，input/output为8816/680 tokens。此次因“当前行动必需历史证据超过记忆选取预算”阻塞，没有队友决策调用、没有执行子回合、没有本次操作。请求242:0保留但target为null，主回合的建议问题又被解析为未完成delegate。不能沿用real-03的正确目标和实际尝试来称本轮委派通过；这项真实缺口保留，未扩建记忆或改模型窗口。

本次非零幂等仍单独通过：主回合新增揭示e230原请求正常重送及正常暂停／恢复后重送，原事件、回合、回执、时间、资源、骰和任务快照一致。它证明这一已结算揭示不会重复结算；未验证执行中崩溃或非零资源消耗重试。主回合冻结文件hash保持。详见[独立审计](../data/prepared/batch-46/real-04/supplemental-assessment.json)、[逐阶段用量与耗时](../data/prepared/batch-46/real-04/metrics-audit.json)及[源资料保护](../data/prepared/batch-46/real-04/source-protection-audit.json)。

只读复核另确认，历史片段本身可通过公开／来源／结果边界；当前实际窗锁回执中的“用力就能打开”被库存工具正则误认作`力就能`工具，导致最后的组合检查清空保留正文。已窄修`bind_item_prose`的身体／力／方式加情态短语，真实工具仍须有本人持有的实例，同句后续工具不能借前面的徒手动作跳过校验。新增反例首轮6失败／4通过，修复后新增及原库存／第45批观察 **31项通过**，见[inventory-manner-regression.txt](../data/prepared/batch-46/inventory-manner-regression.txt)。

同一real-04冻结上下文的[只读旧函数对照](../data/prepared/batch-46/kp-coverage/real04-retention-baseline-function-replay.json)明确使用基线函数仅在隔离进程内注入：三个历史片段通过、回执及组合失败；[修复后只读回放](../data/prepared/batch-46/kp-coverage/real04-retention-readonly-after.json)五项通过，DB/WAL及主采证hash一致。此回放没有live stream对象、没有模型调用或正式发布，不代替新的真实验收；原real-04正式事件和失败状态保持原样。最终24个Python变更文件Ruff及diff检查通过。

保守边界仍在：观察句后再次提及同一对象的建议／概述，可能使映射无法找到唯一最小片段；修复保留规则还要求原验证片段逐字存在，等义改写可能被拒绝。这些限制及真实预算阻塞没有用跨运行证据掩盖。

## 分项结论与本批用量

| 验收项 | 结论与具体范围 |
| --- | --- |
| KP正式正文 | **未通过**。real-03／04原稿语义答全，但最终覆盖映射／安全检查失败并发布回退；原稿不能代替正式正文 |
| 队友实际执行 | **未闭环**。real-02／03选中正确目标并进入子回合，但没有有效本次观察结果；real-04受预算阻塞且目标为空，无子回合 |
| 任务失败保留 | real-03通过：原请求、执行者、窗锁目标和真实generation_failed保留，旧揭示／提议不再销账。real-04请求保留但目标为空，仍是缺口 |
| 非零操作幂等 | real-02、03、04各自的主回合新揭示，分别完成原请求重送及暂停／恢复重送；均无重复结算，不跨运行拼证据 |
| 流式保护 | 相关定向回归通过；本批各真实运行均确认最终气泡与历史一致、失败回退未标完整。新完整答复契约下的成功同次增量／生成中重连仍未通过；第45批real-07原流式结论保留 |

| 运行 | 主回合调用与实际input/output | 独立任务含子回合调用与实际input/output | 主回合提交至首DOM |
| --- | --- | --- | --- |
| real-01 | 0，端口预检退出 | 0，未提交 | 不适用 |
| real-02 | 5；28571 / 1993 | 7；34078 / 2042 | 70.974282s |
| real-03 | 5；28810 / 2523 | 7；34525 / 1858 | 74.091149s |
| real-04 | 6；35904 / 2905 | 2；8816 / 680 | 70.872s |

本批共 **32次真实本地模型调用**，实际usage合计 **170704 input / 12001 output tokens**，外部API调用0。模型阶段时间、队列、首chunk、验证时间均保留逐调用记录；失败调用缺少`model_finished_at`时保持null。不同热度、输出和修复次数不作提速比较，不重做第45批公平输入对照。

本批启动进程已关闭，8146／5246端口释放；未知5146进程未处理，见[清理核对](../data/prepared/batch-46/process-cleanup-audit.json)。没有commit/push，没有改原准备、角色、存档和模型配置。剩余问题限定为覆盖映射的保守误拒及本轮委派预算／目标缺口，不宣称两项真实失败已全部闭环。
