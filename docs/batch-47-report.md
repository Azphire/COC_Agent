# 第47批：放行正确答复，打通当前委派

基线：`d650badbc0b4ef401ca6dd9969aff9b3be3e1022`（repair），2026-09-23。沿原工作区实施，不自动commit/push；保留第46批失败库、日志和第45批real-05准备。没有增加正常回合模型调用、外部API或模型窗口。

## 实现及离线复现

- 覆盖规范化从实际正文及声明来源提取原文，允许多个独立验证的一致候选，按充分片段长度、正文位置稳定选择。完整正文中的数量、极性、未知、估计及归属检查同时约束原生和规范化映射；问题、建议及单独对象名不能代替回答。跨句归属与条件须随片段保留，正文不为通过引用检查而改写。
- 修复保留绑定原需求、来源和关键事实，支持可确定的等义改写；不确定时仍保留原已验证片段，独立验证新增缺项和整体组合。一次修复预算和stream版本沿用，保守拼接继续标记`server_fallback`及`answer_complete=false`。
- 任务按原话区分建议问题和执行委派，混合范围分别处理。登记阶段先绑定唯一可见目标和来源，预算失败仍持久保留请求key／执行者／目标；恢复仅对缺失或None补冻结值。旧错误委派按原请求定向纠正并保留审计。
- 本轮请求及实际依赖进入必需集合；无关待办和旧短期目标保留账本及索引。消除重复投影，在既有总上下文预算中先装必需证据，保留来源、状态、实例、余量和实际回执；超限记录具体条目与预算。

第46批real-04两份原稿的[覆盖首败](../data/prepared/batch-47/coverage/real04-baseline.json)与[修复后回放](../data/prepared/batch-47/coverage/real04-after.json)分别保留。两稿现均四项覆盖完整，正文未改；这只是离线回放，不改写旧正式回退。

记忆[带原冻结目标的首败复现](../data/prepared/batch-47/memory/real04-baseline-frozen-targets.json)确认旧建议待办／短期目标扩散必需项。同样3000字符的[本轮范围复验](../data/prepared/batch-47/memory/real04-scoped-local-budget.json)必需e242／e230／e141／e49共1932字符，完整装入；旧任务仍在`task_index`，未清空历史。最初缺冻结目标的回放单独保留，未冒充首败。

## 验证与真实运行

首轮38文件受影响矩阵为 **455通过／2失败**，见[原日志](../data/prepared/batch-47/affected-tests-01.txt)。第44批明确引用旧未完成任务时的依赖选择回归已修复，原安全断言未改；唯一具体关联的旧任务进入当前依赖，100条无关任务不被提升，歧义保留索引。另一项`test_ordinary_cycle_is_plan_narration_only`的假模型用例期望2次调用而实际3次，在未修改`d650`隔离代码中同样失败，见[基线原日志](../data/prepared/batch-47/ordinary-baseline-01.txt)。该既有失败如实保留。

实测前任务定向 **99通过**（26新增＋73既有），覆盖正常登记事务→预算失败→存档／暂停／读档，以及旧空值不覆盖有效目标、21条待办不丢。见[任务实现与命令](../data/prepared/batch-47/tasks/task-implementation-report.md)及[日志](../data/prepared/batch-47/tasks/final-validation.log)。其较广检查另有4项第35／37批失败，已在独立基线复现相同4失败／3通过，未放宽回执规则；本批没有声称全量测试通过。

记忆定向 **41通过**，随后真实`prepare_run/build_context`接线与持久审计 **12通过**；确实超过3000但在原总预算内的完整实例清单通过，超过总预算时回滚后另开事务持久审计且账本不变。见[选择测试](../data/prepared/batch-47/memory/targeted-tests-01.txt)和[上下文集成](../data/prepared/batch-47/memory/integration-tests-01.txt)。

覆盖初轮 **84通过**后，交叉审阅发现同来源对象描述互换、附带姓名冒充说话人、指代句把估计升级确值及条件语气遗漏反例，原始探测分别保存在[review-01](../data/prepared/batch-47/memory/coverage-independent-review-01.json)、[review-02](../data/prepared/batch-47/memory/coverage-independent-review-02.json)、[review-03](../data/prepared/batch-47/memory/coverage-independent-review-03.json)。补修后[最终91项通过](../data/prepared/batch-47/coverage/directed-review-final.log)。新检查一度误拒real-04正常对象列举，保留[中间误拒](../data/prepared/batch-47/coverage/real04-full-runtime-replay-review.json)；[最终完整runtime回放](../data/prepared/batch-47/coverage/real04-full-runtime-replay-review-fixed.json)确认两原稿均通过，正文和八项源文件hash未变。

旧任务依赖修复后的记忆／预算／来源HO [11文件66项通过](../data/prepared/batch-47/memory/final-affected-tests-01.txt)；最后补重复旧任务投影先去重、同key不同内容仍保守的边界，另[35项通过](../data/prepared/batch-47/memory/duplicate-reference-final-01.txt)。各测试组有重叠，不相加为唯一用例总数；未跑全量。

驱动[validate_batch47.py](../backend/scripts/validate_batch47.py)复用第46批只读SQLite backup、正常HTTP恢复、两成员浏览器及按attempt断线观察器。只执行固定主问题和独立窗锁委派，委派前冻结主采证；不重新备团、前置问话、填窗或压缩。第46批已结算揭示重送／暂停恢复的幂等结论保留，不重新扩展实测范围。

采证驱动[13项通过](../data/prepared/batch-47/driver-tests-02.txt)，前端流式状态机[9项通过](../data/prepared/batch-47/frontend-stream-01.txt)。目录ACL和初始Node参数错误记于[环境记录](../data/prepared/batch-47/environment-check-errors.txt)，不计作产品通过。

## real-01：主回合通过，独立委派保留登记首败

使用原本机`ollama/qwen3:8b`，上下文16384、输出预留900、总上下文字符12000，配置与real-05相同。完整证据在[real-01](../data/prepared/batch-47/real-01/acceptance.json)，主回合在委派前冻结，之后三份主证据hash未变。

主回合`0c6808ea-d670-49be-a6d6-7516d8a5327f`正式e235正文为：

> 你环顾道格拉斯的书房，注意到书架上有几处明显的空档，还有一扇通向屋外的窗户。托马斯早先估计少了六本书，但他并不知道具体书名。这些信息来自他之前的陈述，目前尚未有进一步的确认。

两项观察对应当前书房来源；数量估计、书名未知、说话人及历史时间对应e49托马斯证词。正式结果`answer_origin=repaired`、`answer_complete=true`，四项覆盖完整。首稿及原始映射未删：首稿历史语气没有明确保留“早先”，原模型映射含对象名／问题，经过原有一次修复后正式发布；没有新增模型审核。

[原始／有效映射与预算](../data/prepared/batch-47/real-01/main-coverage-and-budget.json)和[正文一致性审计](../data/prepared/batch-47/real-01/formal-result-consistency.json)保留完整依据。正式事件、裁决记录、两个成员最终气泡及后续委派实际messages中的正文一致；失败首稿没有进入后续公开记忆投影。完整正文不必另存一份摘要，正式事件／recent_dialogue是本次实际记忆路径。

[流式审计](../data/prepared/batch-47/real-01/stream-assessment.json)匹配最终**attempt 2**，两窗口各3次递增前缀，第二窗口在该次生成结束前断线并恢复；最终各一个气泡，与历史逐字一致。整轮首DOM **59.981秒**（包含此前首稿可见时间）；最终成功attempt首DOM距整轮提交 **89.642秒**，不是用失败首稿的增量或重连替修复稿验收。浏览器错误0。

独立只读[语义复核](../data/prepared/batch-47/real-01-review/semantic-review.md)确认上述来源和版本匹配；成功稿重连snapshot早于同次模型完成30.656秒。复核不调用模型，也不修改冻结证据。

独立委派根回合`bd29f169-9a03-4063-8275-05b6c2ffa09e`请求`243:3`，真实队友子回合`0715e6b0-d6cc-4c5f-8b26-9e456c69a83b`进入窗锁`3160e2af-5168-4ffd-88b2-e6207ab7c374`。队友正式e290验证通过，回答“窗锁确实因年久而松动，从外面足够用力就能打开”，形成当前子回合的`observe/success`结果；旧e230的`already_revealed`回执不单独算本次成功。

但该运行仍未完整通过委派验收：原公开投影只有长标题“松动的窗锁”，登记时没有带入批准别名“窗户／窗锁”，严格目标核验清掉了模型给出的ID。早期账本及冻结操作数`target_id=None`，最终仍为`attempted`。完整[首败任务审计](../data/prepared/batch-47/real-01/task-execution-audit.json)保留，不因后来队友选中正确目标而抹去登记缺口。

本次预算阻塞已消除：委派决策必需e243／e230／e141／e49共**1950字符**，选取总用量**2957字符**，原总预算12000内完整装入。旧短期目标没有被误升为必需；账本未清空。见[任务选择及实际请求预算](../data/prepared/batch-47/real-01/task-coverage-and-budget.json)。

| real-01阶段 | 实际调用 | input / output tokens | 分阶段latency（秒，含该阶段验证） |
| --- | ---: | ---: | --- |
| 主回合 | 5 | 28411 / 2344 | 规划45.125；正文首稿32.092；修复34.968；队友讨论11.421；摘要4.108 |
| 独立委派根回合及子回合 | 6 | 28768 / 1818 | 根规划36.780；队友决策11.733；根摘要4.015；子规划34.609；子正文13.796；子摘要3.093 |
| real-01合计 | 11 | 57179 / 4162 | 只报告测量值，无同条件提速结论 |

每次调用的排队、模型、验证时间及缺失时间戳保留在[metrics-audit](../data/prepared/batch-47/real-01/metrics-audit.json)，不把没有`model_finished_at`的失败首稿补成成功完成。外部API调用0。[保护审计](../data/prepared/batch-47/real-01/source-protection-audit.json)确认源DB／WAL、包、配置、角色及原存档行hash保持。

## 首败定位后的定向修补

只读核对real-01及第46批real-04的`RoomEntityState.snapshot.aliases`均已含“窗户／窗锁”，实体为当前可见`revealed`，来源e230。新增回归先真实失败，见[别名绑定首败](../data/prepared/batch-47/tasks/alias-binding-first-failure.log)。补修只为当前公开ID、同一房间且`revealed/corrected`的实体补入批准字符串别名，登记／决策规范化／入子回合复用，未改变公开API或放宽模型ID核验。隐藏、历史、歧义目标仍不能据此猜定；任务、失败及恢复[最终105项通过](../data/prepared/batch-47/tasks/alias-binding-final.log)，包括预算前登记和存档重载的真实数据库路径。

另交付只读[audit_batch47.py](../backend/scripts/audit_batch47.py)：分别核查正式结果一致性、调用指标及当前任务子回合／回执。10项反例测试通过；任何目标报告已存在即拒绝覆盖。第46批real-04[演练报告](../data/prepared/batch-47/audit-driver/replay-real04/metrics-audit.json)复得原调用量，不改变其失败结论。

复验准备只读检查发现：47real-01终态已有e290实际观察和剩余2轮的有效目标冷却，直接重送会进入重复执行约束。为重验原未执行条件，后续仅将第46批real-04失败库（同样来自45real-05准备、无该次child及冷却）按SQLite只读backup恢复到新目录，再提交同一窗锁问题。保留源主回合回退、旧失败任务及所有历史，不改库倒退到某个采样点，不清除有效冷却。该委派复验与47real-01主回合分开报告，不能合成一轮全通过。

## task-recheck-01：保留来源人名造成的假歧义

[委派专项驱动](../backend/scripts/recheck_batch47_task.py)从上述46real-04只读恢复，主问题、前置问话、填窗和压缩均未执行；正常换成员凭据、原卡重新分配、ready／resume，终态回合未取消。驱动及继承检查[21项通过](../data/prepared/batch-47/memory/task-recheck-driver-tests-02.txt)。

请求`284:3`进入根回合`e0441955-7aff-4b38-bf6e-e9960f1149f3`和实际子回合`4576aa39-4fcd-4ffb-9a89-f9f1cc6a337f`。当前正式e333回答“你检查了窗锁，确认它确实因年久而松动，从外面足够用力就能打开。”，验证完整、当前观察回执有效、两窗历史一致。但批准别名同时命中了“根据托马斯早先的说法”的**来源人名**与行动对象窗锁，登记保留两个候选而未绑定目标，账本仍`attempted`。该次仍未通过完整委派验收，见[任务审计](../data/prepared/batch-47/task-recheck-01/task-execution-audit.json)。

必需e284／e230／e141／e49共2050字符，选择总用量2724字符，原12000预算内完整。旧错误建议`213:86`已按原请求改为question，原delegate以`reclassified`及原因留在审计；旧失败委派`242:0`仍保留，没有批量清空。

本次实际**6调用，30396 input／1723 output tokens**。分阶段latency：根规划50.703秒、队友决策10.780秒、根摘要3.312秒、子规划34.187秒、子正文11.937秒、子摘要3.671秒。整轮首公开DOM71.083秒（队友发言），子回合首流式正文122.585秒；见[时序](../data/prepared/batch-47/task-recheck-01/task-timing.json)。这些是该次委派的指标，不含source-main旧调用。

[独立语义复核](../data/prepared/batch-47/task-recheck-01-review/semantic-review.md)另保留队友公开措辞偏差：e306提前称“轻松打开”，来源只支持“足够用力就能打开”；e307为提议，都不能补作本次完成。正式e333保留正确条件。本次仅有单句前缀，双窗历史一致不等于新的多段流式验收。

只读审计首次遇到合法`narration=None`的纯委派父计划，采证工具异常且未写任何报告；补空值处理后[14项测试通过](../data/prepared/batch-47/audit-driver/tests-null-parent.txt)，随后成功生成本次报告，源证据未变。该采证异常与产品首败分别记录。

来源范围修补只在目标匹配时排除明确“根据／据＋姓名＋说法／证词”等短语及紧随的引语，原请求、偏移及操作授权保留，`target_source.excluded_reference_spans`记录排除依据。来源人名在后续实际动作中再次出现仍可成为对象，真正双对象仍保留歧义，条件句不因此获执行权。

[最终任务组113项通过](../data/prepared/batch-47/tasks/source-scope-final.log)，含托马斯／窗锁双实体真实数据库登记→预算失败→存读档→子回合冻结→本次正式观察结算。此前夹具字段问题和首败日志保留在[任务说明](../data/prepared/batch-47/tasks/task-implementation-report.md)。另用实际5个公开实体、4个当前目标、真实库存和原请求做[独立只读复验](../data/prepared/batch-47/memory/real-task-binding-final.json)，唯一绑定窗锁，保留`284:3`及e230来源；托马斯仍在候选集合，未通过删实体制造唯一性，源文件hash不变。

## task-recheck-02：当前委派执行与结算通过，提前措辞偏差保留

再次从**同一46real-04源库**只读backup，配置、题目和正常恢复流程与task-recheck-01相同，只复验对象范围补修；没有换题、增添前置问话或重跑主问题。[本次记录](../data/prepared/batch-47/task-recheck-02/acceptance.json)保持`scope=task_only_recheck`，source-main仅为旧证据副本，不计入新主回合或本次用量。

当前请求`284:3`在队友决策前已持久绑定窗锁`3160e2af-5168-4ffd-88b2-e6207ab7c374`、执行者和e230来源。根回合`cea8267b-2071-4144-b574-68702d0218ee`进入实际子回合`8f27df18-e0d4-4714-98b5-233c91c8907d`，冻结请求、实际计划及观察目标一致。本次正式e333为：

> 你查看了书房窗户，确认窗锁确实因年久而松动，从外面足够用力就能打开。

正式结果`native`、`answer_complete=true`，源文支持松动和足够用力的条件，没有声称实际开窗。`observe/success`结果引用**本次子回合e333**，当前请求已从pending移入`request_history`，`status=completed`、`result_cycle_id`指向上述child、`result_event_seqs=[333]`；不是根据旧揭示e230或提前提议结算。详见[任务审计](../data/prepared/batch-47/task-recheck-02/task-execution-audit.json)。旧`242:0`失败委派和`213:86`建议问题仍保留，因此行为记录汇总`task_status=attempted`，不通过清空旧任务把汇总状态刷成completed。

两成员各一条e333，与正式事件及裁决正文一致，浏览器错误0；见[正文与历史核对](../data/prepared/batch-47/task-recheck-02/formal-result-consistency.json)。本次单句观察不承担主回合多段增量／重连验收，主回合该项仍只来自real-01成功attempt2。

本轮必需e284／e230／e141／e49共**2246字符**，实际选择**2920字符**，在既有12000总预算内完整；旧任务只保留索引，完整目标来源及原请求没有被截断。原始映射、来源、必需选择及逐次实际request budget见[覆盖与预算](../data/prepared/batch-47/task-recheck-02/task-coverage-and-budget.json)。

实际**6调用，31050 input／1868 output tokens**。分阶段latency：根规划40.891秒、队友决策10.734秒、根摘要5.141秒、子规划33.344秒、子正文13.094秒、子摘要6.062秒。整轮首公开DOM**61.295秒**，子回合首流式正文**113.579秒**；完整模型／验证／排队时间见[指标审计](../data/prepared/batch-47/task-recheck-02/metrics-audit.json)。

未解决项如实保留：e306队友仍提前称“窗锁已经确认松动，从外面可以轻松打开”，其中“轻松”缺少来源支持；e307动作提议也使用已执行语气。二者不是本次正式观察或完成依据。本批**当前委派的绑定、执行和结算链通过**，不将队友全部公开措辞标为语义全通过，也不增加审核模型或扩展通用记忆架构。

[独立语义复核](../data/prepared/batch-47/task-recheck-02-review/semantic-review.md)逐项核对当前key、真实发送的请求投影、子回合及正式结果；旧两条任务仅列为deferred，6次实际请求均在原token预算内（最高15798／16384）。整轮采证耗时129.815秒，单独记录，不作为提速结论。

## 交付范围与保护

主回合real-01通过；其独立委派登记首败及task-recheck-01假歧义首败均原样保留。task-recheck-02仅证明该次当前委派执行与结算，不把不同运行的通过项拼成一次全通过。

本批真实本地模型合计**23次，118625 input／7753 output tokens**，外部API调用0。普通回合没有新增模型步骤，修复仍用原一次预算；没有扩大模型窗口或修改配置。所有耗时均为测量，无同条件性能对照，不宣称提速。

受影响验证分组已在上文逐项列示，有重叠不累计为唯一总数；未跑全量。最终[26个Python文件AST／Ruff及diff检查通过](../data/prepared/batch-47/static-final-02.txt)。既有基线失败与公开队友措辞偏差均保留。

[收尾核验](../data/prepared/batch-47/final-verification.json)确认三次运行涉及的原DB／WAL、包和配置hash至交付仍未变，原角色／profile／准备及存档行保持；最终实测的产品代码与当前工作区一致，8147／5247隔离端口已释放。文档链接检查通过，HEAD仍为`d650badbc0b4ef401ca6dd9969aff9b3be3e1022`，未commit/push。
