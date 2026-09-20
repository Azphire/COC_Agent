# 第39批：自然动作权限、真实结果与队友任务

**最终固定代码的新局已在 seq1130 实际到达结局 B；自然交互只部分通过，尚未达到本批全部完成标准。** 本批修复了目的从句借用动作权限、自然取物和医疗漏执行、实际操作对象混淆，以及部分失败结果和取消匹配问题。最终局仍出现虚构开关、提前断言通道可走、取消新措辞误分类和无关回退；最终代码的失败追问复测还出现一次未取物却声称取了钥匙。它们均保留为未通过项，不能用结局或单测通过抵消。

正式证据在 [acceptance-run5-20260919](../data/prepared/changan/batch-39/acceptance-run5-20260919/)： [完整团录](../data/prepared/changan/batch-39/acceptance-run5-20260919/session-full.md)、[玩家可见事件](../data/prepared/changan/batch-39/acceptance-run5-20260919/public-events.json)、[全员公开 JSONL](../data/prepared/changan/batch-39/acceptance-run5-20260919/public-events.jsonl)、[脱敏私有审计](../data/prepared/changan/batch-39/acceptance-run5-20260919/private-audit/)、[结局后核对](../data/prepared/changan/batch-39/acceptance-run5-20260919/postgame-review.json)。所有中间运行见 [运行索引](../data/prepared/changan/batch-39/README.md)。

## 版本、基线与执行边界

当前分支 `main`，HEAD 为 `3ddac95dfd17667ad285293c28ac7a46175cce59`。起始工作区干净，先检查 HEAD、工作区及仓库/父目录指令，再执行 `git pull --ff-only`。首次因沙箱不允许写 FETCH_HEAD 失败，经自动审批执行成功，结果 Already up to date。全程单执行者，没有分支、worktree、子 Agent、reset/stash、commit/push。实际交付是此 HEAD 加工作区补丁。

已阅读第38批报告及本机 `batch-38/acceptance-frozen-20260918` 的团录、公开事件、私有审计和保存。旧 seq1061→1079 确有目的搜索却提前投掷；旧296医疗90/60失败，却有325止血成功台词。原始输入/输出与事件摘录保存在 [baseline-evidence.json](../data/prepared/changan/batch-39/baseline-evidence.json)。旧目录有 seq120、seq606 保存点，**没有 seq1061 前的精确存档**；独立函数探针不等于完整应用复现。本批从附近保存点的独立数据库副本经正常恢复跑完整链路，不把近似现场称为逐状态精确回放。

最终局房间为 `192885c0-50f3-4c8f-8807-81b0b69e2e3e`。固定应用后正常随机建卡、分配点数、提交/接受、分配林知秋、周岚、陈拓并冻结开局。KP和队友均实际调用本机 `qwen3:8b`，保持8192上下文、900输出、`think=false`；继续使用现有规划、串行子周期和一次定向修正，没有常驻模型评审器或新调度框架。

起点 [code-version.json](../data/prepared/changan/batch-39/acceptance-run5-20260919/code-version.json) 的补丁 SHA256 为 `f576078d5e2b3d1b2487f0c70bcd45ddf3af96df2a33664e2a0c6526df37f03a`。[143个应用文件首尾核对](../data/prepared/changan/batch-39/acceptance-run5-20260919/application-version-verification.json) 全部一致。最终交付 [源码及补丁](../data/prepared/changan/batch-39/delivery-version/) SHA256 为 `0fac5d9e1a950d0c98dd6e7522825bbc3a6cb9704721e480b7f22eba1d88ed33`；差异来自离线审计脚本完善，正式局应用代码未变。版本包包含未跟踪的新应用、测试和脚本，不只记录 git HEAD。

Codex按本局公开事件选择口语玩家动作，最终局的私有计划在结局后才审阅；途中保存核验只读取比较所需状态及哈希。此前读过旧模组/审计，因此不宣称严格盲测。没有主机结束、固定检定口令、直接改库存/场景、覆盖原骰或强选结局。出现表达缺陷先记录继续；前几局发现实际副作用错误时保留现场，修复后重测并重新分类，未把调试局当正式局。

## 实际修改及边界

1. **按实际动作片段冻结权限。** `action_authority.py` 统一提取实际执行片段、执行者、目标、操作和物品；目的、假设、条件、否定与结果追问不能借出投掷等权限。保留正常“想、准备、试着”的行动表达，覆盖取出/掏出/抽出和止血/按压伤口等操作。`TurnFocus` 与现有意图校验保留玩家取物和队友查路各自的片段。增加按操作绑定的 `operation_item_ids`，背景里“钥匙后面还要用”不再成为扔鞋的操作对象。
2. **方法选择与副作用使用同一授权。** 批准交互ID作为既有 `apply_module_action` 的 `interaction_id`，绑定实体、操作、原话来源和前置条件；不再把 `take_keys` 当工具名。实际执行仍校验持有权与模组规则；医疗门槛的线索检查必须有实际医疗授权，查门不能借走急救。当前场景已公开的控制器说明用于唯一目标消歧；进入本地驾驶室不再误当退回上一节车厢。车厢照明与手持手机照明分开绑定。
3. **共用结果事实。** 新增 `agents/results.py`，供 `public_results()`、KP、NPC、队友（含 speak/追问）与记忆投影使用，表达执行者、目标、操作、成功/失败/未执行/技术失败、效果及来源。医疗读取真实治疗结算，不能拿 `treated`、HP差值或 `last_attempt_result.operations` 证明成功。局部校验共享动作/结果词汇和否定、时态、主体信息，保留有效内容；失败回退优先给已有的相关结果事实，不虚构成功、NPC不知情或新线索。**实际事件到公共压缩结果仍有操作对象字段丢失，见下文，不能宣称全链路完整。**
4. **资源与任务结算。** 会改变解法的工具箱/医疗资源声明纳入持有权核对；普通感受、计划和小动作不要求逐句引用回执。队友“我立刻过去处理伤口”可成为现有队列中的实际尝试，未来承诺仍不等于完成。公开可见、唯一可指认的伤者可绑定真实医疗目标；多个候选不猜。技术失败可恢复，实际失败保存为 blocked，已有已尝试限制阻止刷骰。
5. **取消与上下文。** 从该队友旧待办的key、操作、目标和文字解析取消，空目标不通配全部，新绑定错误不覆盖原任务身份；明确全部停下才撤全部相关任务。排队激活、执行及医疗创建前复核取消，保留历史与已结算效果。压缩重复背景，优先提供当前目标、相关结果、资源和阻碍，沿用8192预算。未要求每轮两人都发言，也没有为职业写固定攻略。

保留第36—38批的普通NPC回答、有效答案局部保留、原骰恢复和任务历史机制；受影响回归通过。真实最终局的普通NPC复杂问话仍会落入机械事实回退，不能用旧机制存在证明本次每句都自然。

## 四组完整链路对照

下表“正式”均指第五次新局，其他目录编号必须连同目录读，不跨运行混用seq。短场景均为真实qwen完整链路；各阶段源码见各自版本包。

| 必须区分的场景 | 原现场与本批证据 | 结论 |
| --- | --- | --- |
| 找可投掷物→明确投掷；找开关/问意见→操作 | 旧1061→1079提前投掷。正式844摸口袋找能扔的物品→865仅确认已有钥匙，没有投掷、失去持有、声响或放开通道；873明确扔鞋→891/892真实操作 `worn:鞋`，钥匙仍在，随后926才通过。727找开关并征询意见没有对抗或关灯；784明确关灯→804 `light/not_executed`，无环境副作用，但748已虚构摸到开关，806继续错误开关描述。 | 投掷授权和实际物品核对通过该正式样本；环境操作没有越权改状态，但无依据开关与错误反馈使整组仅部分通过。合法手机开关另有短场景正例，详见补充核验。 |
| 自然取物＋队友查路；自然委托医疗 | 旧493/517/576/608漏取物、误工具名。正式598“从包里拿出钥匙”＋查通道→616/617合法取物，626陈拓独立检查→651骰37/60成功、654发现暗处身影；但625已提前说通道可走。319“帮他止一下血”与查门分别保留；343护士即时行动→381真实急救72/60失败，任务blocked，没有按承诺完成。short25也有1634真实医疗成功正例，陈拓查门没有借用医疗检定。 | 自然取物、双方授权和医疗尝试可落地；提前路况断言及部分复测漏队友执行仍未通过。 |
| 已失败医疗、被拒取物、未执行关灯后的追问 | 正式381失败→413护士明确承认失败；原失败保存恢复不变。最终代码short26保留旧90/60失败，1589据296回答失败；1624工具箱取物→1644未执行、1646如实回退，但1675陈拓又说捡起的是钥匙，实际钥匙直到1701才取得。正式804未关灯→835 KP堆旧历史、837队友推回玩家确认。short26最后1778灯光追问无有效答复。 | 原骰和执行状态没有被改成成功；**结果追问组未通过**，仍有虚假取物、偏题和缺答。 |
| 多待办取消、取消后激活、失败后自主选择 | 旧125→152未取消旧查门。正式389:0准确取消陈拓319:13，保留护士319:3真实失败blocked；420—422恢复后没有该取消key的新子周期。short26的1716:0也命中1682:9。队列回归验证激活/执行前重查。正式501“先别再包扎了”却登记成医疗delegate；490护士又尝试原急救，被已尝试限制拒绝。陈拓有实际找包与查路，护士承认失败但安置办法含糊。 | 具体取消匹配和无复活样本通过；取消新措辞、失败后调整和两人贡献质量仅部分通过，整组不全通过。 |

最终代码补充核验：[short27/final-light-operations](../data/prepared/changan/batch-39/short27/final-light-operations/session-full.md) 从独立保存恢复，应用143文件与第五局相同。1147找开关/问意见无照明副作用及对抗；1182关车厢照明→1202未执行、1204解释条件；1213关本人手机→1231/1232真实light_off；1274重新打开→1292/1293真实phone_light。合法开关正例成立，环境未确认时没有错关手机。仍有1168偏题、1172重复已结算医疗及1241关灯后却声称更容易观察。外层执行返回1也保留记录，四个步骤和六个周期均completed、审计完成；按真实调用另记失败，不称整组全通过。

## 最终新局：实际状态、两名队友与保存恢复

| 过程 | 事件与真实结果 | 反馈质量 |
| --- | --- | --- |
| 初始搜索与自然探索 | 26摸口袋，46幸运88/40失败，50库存空；83/84便签内容；209玩家侦查72/45失败，245陈拓61/60失败。 | 57和217如实未找到；陈拓重复同方法价值较低，原玩家失败没有被覆盖。 |
| 双人请求与急救 | 319同时委托周岚止血、陈拓查门；343→381护士急救72/60失败，HP3→3、stabilized=false。 | 386失败回执，413明确承认；369查门反馈退回旧便签，与目标不符。 |
| 取消与恢复 | 389取消陈拓319:13，护士319:3保留blocked；420暂停、421加载、422恢复。 | 无取消任务复活，无恢复撤销医疗或重掷；501另一种取消说法仍错误。 |
| 失败后的互动 | 423询问伤者，428/429袭击及声音证词；463问钥匙，468黑包位置。 | 455/488没有自然回答当前问题；490护士再次急救被拒，真实医疗总数仍为1，不能称已自主调整成功。 |
| 陈拓找包 | 536请求找包，557实际尝试，583骰29/70成功，586黑包钥匙发现。 | 591用真实发现回退，属于有效贡献；559护士将陈拓当成伤者说要处理伤口，属于错误目标台词。 |
| 自然取物与查路 | 598→616/617取得钥匙；626→651骰37/60成功，654观察到2号车厢暗处身影。 | 623准确取物，659保留真实通道结果；625提前通行结论不通过。 |
| 灯与声音 | 727找开关/问意见、784关灯均未造成环境操作；844目的搜索无投掷；873明确扔鞋→891/892实际鞋声诱开，钥匙仍持有。 | 748/806虚构开关，835/837追问失败；898未自然说明已取得的通行机会。 |
| 开门、面板与停车 | 906通过→926头车；942钥匙开门→961；1006第二钥匙开面板→1024；依据1023公开拉杆说明，1039进入本地驾驶室并刹车→1058实际停车，1128逐人SAN结算，1130 `module.completed` outcome=B。 | 933无关回退、999队友重复检查被拒仍保留；结局由真实状态成立，未靠这些坏句子制造成功。 |

周岚的具体贡献是**一次真实急救尝试与失败后的如实确认**，不是成功止血；陈拓的具体贡献是**找包29/70成功、查通道37/60成功**。护理安置、协助搀扶等仅提议的内容不算已执行；两人有不同作用，但周岚失败后的策略调整不足，陈拓也有提前断言、重复检查和答非所问。

[恢复核验](../data/prepared/changan/batch-39/acceptance-run5-20260919/restore-verification.json) 的 checks、工具回执、队友状态和重复提交已结算骰均一致。[完整医疗恢复核验](../data/prepared/changan/batch-39/acceptance-run5-20260919/medical-restore-verification.json) 对照保存与恢复后的combat、原动作，双方SHA256均为 `09d7a463a26ae992a4e5f59a0e9df605751f8a504b4a7f13a66a6d639efd7b01`。这是最终新局的72/60失败，不能和旧38批90/60失败混写。

旧38批seq296在21个相关副本中原样存在，15个副本当前恢复时间线仍含原医疗动作，逐项比对无变化，其余从更早保存点或其他局恢复。见 [original-receipt-verification.json](../data/prepared/changan/batch-39/original-receipt-verification.json)。继承原事件只证明保存完整，不计作新运行结果。

## 未通过项与上下文诊断

- **P0：公共反馈仍可能虚假成功。** 正式625在626当前检查前断言通道能走；short26 seq1675在1701实际取钥匙前说已经捡起钥匙。其他操作成功不能替该动作背书，整组结果追问不通过。
- **P0：无依据资源/解法细节仍漏检。** 正式748把寻找开关写成摸到开关，806继续沿用；实际804拒绝环境操作，灯未改变。short26工具箱没有进入库存，但1644错误目标标签仍是钥匙，后续更出现错误取物说法。
- **P0：结果字段尚未完整贯通。** 原891交互有actor、target、cycle及`operated_items=[worn:鞋]`；公共压缩来源丢字段后，896 `action.result` 的operated_items为空、cycle_id=null，取钥匙616→621也有同类缺口。实际库存、私有回执和文本可核对正确副作用，但不能宣称所有生成校验都拿到完整操作对象。
- **P1：取消变体仍漏分类。** 501“先别再包扎了，让他靠稳”成为 `delegate/first_aid`，后来 unavailable；不是成功取消。389和short26精确匹配旧key的成功不能覆盖此失败。
- **P1：失败后自主选择与当前目标反馈不足。** 护士490重复原方法，被限制拦住而非自行转向；413只说再评估，安置办法不具体。查门369退回便签，NPC普通追问455/488机械回退，后期933/999仍无关。最终局无“阳光”台词，但自由场景细节校验不完整。
- **预算内有事实也会答错。** [context-review.json](../data/prepared/changan/batch-39/acceptance-run5-20260919/context-review.json) 和 [实际模型输入核验](../data/prepared/changan/batch-39/acceptance-run5-20260919/model-input-review.json) 表明，814灯光追问的KP输入3747 tokens、陈拓输入3673 tokens，二者都含804 `light/not_executed`及“2号车厢很暗”的场景。KP走readonly_recall，fact_evidence却取616、209、84等旧资料，835遂堆历史；837队友也没使用已有回执。不能归因于当前结果被8192预算裁掉，更不能靠继续堆提示宣称解决。

## 调试局与短场景的版本归属

| 新局目录 | 实际分类及结束状态 | 不能忽略的失败 |
| --- | --- | --- |
| `acceptance-frozen-20260918` | 第一次调试局，未到结局。 | 821未制动，823却说减速；short11从其独立保存修复制动后965到B，只算恢复探针。 |
| `acceptance-final-20260918` | 第二次、中间版本完局，1126到B，随后被修改版本替代。 | 后续short13/14暴露失败/资源追问问题，不能作为最终代码证明。 |
| `acceptance-verified-20260918` | 第三次调试局，未到结局。 | 771关车厢灯却789关手机；1081本地驾驶室刹车却1098退到2号车厢；1135错误说已到头车。 |
| `acceptance-sealed-20260919` | 第四次调试局，未到结局。 | 739说钥匙留着、扔鞋；757有声响但758实际丢的是钥匙，764却说扔鞋。私有审计撤回仅看公开台词的成功判断；后续无法开门。 |
| `acceptance-run5-20260919` | 最终固定代码完整新局，1130到B，应用143文件未变。 | 以本报告逐项评价交互质量，不借用前四局成功覆盖失败。 |

前期short01—19保留目的权限、混合取物、失败追问、灯光目标和本地控制器修正过程；具体团录均由索引直达。特别纠正：short03虽然台词说停下，私有cancelled_keys为空且needs_clarification=true，不能算取消通过；short06/17/19/23的继承取消不算新增证据。取消证据见 [cancelled-task-evidence.json](../data/prepared/changan/batch-39/cancelled-task-evidence.json) 及最终局/short26的结局后审计。

short20验证物品按操作绑定：1853/1854实际扔鞋、钥匙保留，随后开门、开面板、制动真实执行；只有pending_outcome=B，未完成结局，不能算完整完局。short21暴露即时医疗被当普通承诺拒绝；short22允许即时行动，但陈拓查门却借用急救检定，随后修复。short24修复操作范围后发现“伤者”未正确绑定实际NPC；short25在公开唯一伤者绑定后1634真实医疗成功，陈拓查门无医疗检定，1717关灯未执行、1749追问承认未关，但门边闪光描述和安置建议仍薄弱。short25加载代码期间仅补回旧复合治疗兼容条件，最终正式局与short26使用其后完整固定代码；不把所有短场景笼统标为同一个最终版本。

## 回归与静态检查

新增 `backend/tests/test_batch39_results.py`，52个参数化用例，覆盖目的搜索无副作用、合法取物/投掷、背景物品与实际物品分离、被强塞错误物品的状态/事件/骰不变、真实医疗结果、结果追问、资源持有、双人请求、取消队列与恢复、灯光目标和本地驾驶室等行为。不另建大型评估框架。

最终固定应用版本运行结果：**191 passed，28 warnings，155.89s**，见 [regression-final-code.log](../data/prepared/changan/batch-39/regression-final-code.log)。范围为39/36/37/38批相关用例、`test_action_adjudication.py`，以及17批真实治疗/原骰、16批库存回执及结局恢复、18批人类医疗目标不得替成队友、20批复合治疗/代词/结算后问话的定向用例。Pydantic及SQLAlchemy现有警告保留。未每改一句提示就跑全仓库测试，也未将真实模型运行当替身单测。

旧17批夹具将名为Notice的合成物品当中文钥匙/一次性测试物使用，本次只为夹具补别名，不改批准资料。37/38批即时“我立刻处理伤口”期望由拒绝改为排队真实act、goal_status=continue，并继续断言没有凭承诺生成成功回执；未来承诺仍不完成任务。

| 其他日志 | 独立结果及用途 |
| --- | --- |
| `regression-01.log` | 226 passed、9 failed，734.59s；初期广泛受影响核验，六项本批问题已修，另三项旧18批问题单独核对。 |
| `baseline-regression-17.log` | HEAD原代码只读加载核对：2 passed、4 failed，0.69s；涉及缺action_authority的旧假对象、旧移动期望与无效空TurnFocus夹具。 |
| `baseline-regression-18.log` | HEAD原代码只读核对：3 passed、3 failed，0.57s；控制器观察、旧任务变化识别、起始持有可搜索夹具。未据此声称全仓库已通过。 |
| `regression-release-attempt.log` | 168 passed、3 failed，148.93s；即时承诺语义改为真实排队后，三项旧期望需要上述更新。 |
| `regression-release-treatment.log` | 173 passed、1 failed，138.96s；新夹具缺ruleset_id，修正夹具。 |
| `regression-release-patient.log` / `regression-release-restore.log` | 分别180 passed/140.70s、11 passed/10.56s；最终完整191项又统一验证，不相加为额外独立测试。 |
| `ruff-delivery.log` / `diff-check-delivery.log` | 24个修改/新增Python文件Ruff全部通过；正常仓库配置下`git diff --check`退出码0。 |

所有日志分别保留，不累加重叠通过数。运行数据库及pytest临时目录均隔离，未让导入或测试连接默认用户数据库。

## 统计口径与分运行数字

沿用38批口径：模型失败按实际call的error_category计，包含后来修正成功的调用；队友最终决策失败另计，不计无触发条件的确定性跳过；完整/局部回退按周期与validated_partial区分，不能只数safe_fallback；原历史事件和旧周期不计入新探针。下表“澄清”是公开澄清次数，最终局267与710经人工核对均无效；其他探针不能只凭次数判断对错。

最终局23次玩家输入、11次队友尝试、34个周期、777条全员公开事件、134次模型调用；18次调用失败、4次队友最终决策失败、0个失败周期。12个旁白回退周期按口径分为完整10、局部2，不能写成12次完整回退。最大实际prompt为4445 tokens。模型858.047s，活动周期区间并集965.799s，差额107.752s含应用、校验、检查点和等骰，不是CPU时间；校验1.946s、模型队列0。开局到1130结局墙钟2127.130s，含玩家阅读/操作，不能冒充模型延迟。

下面每行都是一个独立运行，耗时用周期区间并集避免父子重复；不合并各轮复测为整体通过率。原始细分见 [delivery-metrics.json](../data/prepared/changan/batch-39/delivery-metrics.json)。

| 运行 | 玩家/队友尝试 | 调用/调用失败 | 队友最终失败 | 澄清 | 完整/局部回退 | 模型s | 周期并集s | 其他s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [acceptance-final-20260918](../data/prepared/changan/batch-39/acceptance-final-20260918/session-full.md) | 27/7 | 144/30 | 6 | 3 | 16/1 | 1001.748 | 1118.937 | 117.189 |
| [acceptance-frozen-20260918](../data/prepared/changan/batch-39/acceptance-frozen-20260918/session-full.md) | 19/6 | 102/10 | 9 | 1 | 2/2 | 740.385 | 827.618 | 87.233 |
| [acceptance-run5-20260919](../data/prepared/changan/batch-39/acceptance-run5-20260919/session-full.md) | 23/11 | 134/18 | 4 | 2 | 10/2 | 858.047 | 965.799 | 107.752 |
| [acceptance-sealed-20260919](../data/prepared/changan/batch-39/acceptance-sealed-20260919/session-full.md) | 25/5 | 138/29 | 7 | 1 | 13/1 | 948.817 | 1192.242 | 243.425 |
| [acceptance-verified-20260918](../data/prepared/changan/batch-39/acceptance-verified-20260918/session-full.md) | 27/10 | 156/30 | 9 | 2 | 17/0 | 1052.336 | 1171.148 | 118.812 |
| [short01/authority](../data/prepared/changan/batch-39/short01/authority/session-full.md) | 5/1 | 27/6 | 1 | 0 | 1/0 | 247.799 | 365.218 | 117.419 |
| [short02/take-and-route](../data/prepared/changan/batch-39/short02/take-and-route/session-full.md) | 3/0 | 8/0 | 2 | 1 | 0/0 | 93.544 | 146.640 | 53.096 |
| [short03/medical-and-cancel](../data/prepared/changan/batch-39/short03/medical-and-cancel/session-full.md) | 4/1 | 18/1 | 0 | 0 | 0/0 | 118.366 | 135.200 | 16.834 |
| [short04/failed-results](../data/prepared/changan/batch-39/short04/failed-results/session-full.md) | 4/1 | 25/6 | 1 | 0 | 0/1 | 189.728 | 208.457 | 18.729 |
| [short05/final-mixed](../data/prepared/changan/batch-39/short05/final-mixed/session-full.md) | 5/2 | 32/5 | 0 | 0 | 1/0 | 205.679 | 231.738 | 26.059 |
| [short06/final-authority](../data/prepared/changan/batch-39/short06/final-authority/session-full.md) | 3/1 | 18/6 | 0 | 0 | 2/1 | 111.181 | 126.978 | 15.797 |
| [short07/frozen-mixed](../data/prepared/changan/batch-39/short07/frozen-mixed/session-full.md) | 5/0 | 24/4 | 1 | 0 | 0/1 | 162.912 | 182.056 | 19.144 |
| [short08/frozen-medical](../data/prepared/changan/batch-39/short08/frozen-medical/session-full.md) | 3/1 | 15/0 | 1 | 0 | 0/0 | 104.351 | 119.142 | 14.791 |
| [short09/confirmed-results](../data/prepared/changan/batch-39/short09/confirmed-results/session-full.md) | 5/1 | 38/3 | 5 | 0 | 0/0 | 239.503 | 265.199 | 25.696 |
| [short10/final-result-repair](../data/prepared/changan/batch-39/short10/final-result-repair/session-full.md) | 3/1 | 21/2 | 2 | 0 | 0/0 | 128.270 | 143.270 | 15.000 |
| [short11/control-repair](../data/prepared/changan/batch-39/short11/control-repair/session-full.md) | 3/0 | 11/1 | 0 | 0 | 0/0 | 91.511 | 102.743 | 11.232 |
| [short12/final-mixed-action](../data/prepared/changan/batch-39/short12/final-mixed-action/session-full.md) | 2/1 | 13/1 | 0 | 0 | 0/0 | 83.573 | 95.049 | 11.476 |
| [short13/final-failure-followups](../data/prepared/changan/batch-39/short13/final-failure-followups/session-full.md) | 6/1 | 40/3 | 4 | 0 | 0/0 | 237.739 | 264.520 | 26.781 |
| [short14/followup-repair](../data/prepared/changan/batch-39/short14/followup-repair/session-full.md) | 7/1 | 42/4 | 6 | 0 | 0/0 | 268.309 | 298.981 | 30.672 |
| [short15/final-authority-and-results](../data/prepared/changan/batch-39/short15/final-authority-and-results/session-full.md) | 7/1 | 39/4 | 2 | 0 | 0/0 | 254.580 | 284.687 | 30.107 |
| [short16/verified-followups](../data/prepared/changan/batch-39/short16/verified-followups/session-full.md) | 3/1 | 19/3 | 1 | 0 | 1/0 | 109.433 | 124.472 | 15.039 |
| [short17/light-targets](../data/prepared/changan/batch-39/short17/light-targets/session-full.md) | 4/0 | 24/4 | 3 | 0 | 2/0 | 125.486 | 141.461 | 15.975 |
| [short18/local-control](../data/prepared/changan/batch-39/short18/local-control/session-full.md) | 1/0 | 3/0 | 0 | 0 | 0/0 | 26.810 | 32.541 | 5.731 |
| [short19/movement-results](../data/prepared/changan/batch-39/short19/movement-results/session-full.md) | 2/0 | 9/2 | 0 | 0 | 1/0 | 66.104 | 74.189 | 8.085 |
| [short20/operation-items](../data/prepared/changan/batch-39/short20/operation-items/session-full.md) | 13/1 | 61/10 | 1 | 1 | 3/1 | 379.145 | 431.109 | 51.964 |
| [short21/visible-patient](../data/prepared/changan/batch-39/short21/visible-patient/session-full.md) | 5/0 | 21/2 | 3 | 0 | 0/0 | 148.466 | 167.255 | 18.789 |
| [short22/immediate-attempt](../data/prepared/changan/batch-39/short22/immediate-attempt/session-full.md) | 5/2 | 22/3 | 0 | 0 | 1/0 | 139.850 | 161.627 | 21.777 |
| [short23/light-operands](../data/prepared/changan/batch-39/short23/light-operands/session-full.md) | 4/0 | 25/5 | 2 | 0 | 2/0 | 121.886 | 138.201 | 16.315 |
| [short24/scoped-attempts](../data/prepared/changan/batch-39/short24/scoped-attempts/session-full.md) | 5/2 | 23/4 | 1 | 0 | 1/0 | 150.629 | 172.043 | 21.414 |
| [short25/patient-and-results](../data/prepared/changan/batch-39/short25/patient-and-results/session-full.md) | 5/2 | 25/2 | 1 | 0 | 1/0 | 160.753 | 182.506 | 21.753 |
| [short26/final-failed-followups](../data/prepared/changan/batch-39/short26/final-failed-followups/session-full.md) | 7/1 | 40/5 | 3 | 0 | 1/0 | 278.582 | 308.842 | 30.260 |
| [short27/final-light-operations](../data/prepared/changan/batch-39/short27/final-light-operations/session-full.md) | 4/2 | 30/7 | 1 | 0 | 0/3 | 160.781 | 179.850 | 19.069 |

## 交付及保护核对

每个运行目录提供完整团录、玩家可见JSON、全员公开JSONL、原玩家输入、模型原输出/校验和脱敏工具回执；`audit-manifest.json`列可交付文件及SHA256。原始game/checkpoint/knowledge数据库、认证文件、完整保存和session连接信息仅留本机用于恢复，不在可交付清单。不要把整个独立运行目录当成已脱敏包；使用清单列出的导出。

[首尾保护核对](../data/prepared/changan/batch-39/protection-verification.json) 仅重新哈希初始明确清单的1187项，变化/缺失为0，覆盖原稿、批准包、用户数据库、旧存档与.env；没有恢复历史目录全量扫描。所有运行按各自认证值核对清单内导出，未发现凭据命中；这是针对已知运行凭据的检测范围，不宣称任意文件都已脱敏。最终局服务正常停止，用户已有服务未改。代码仍在当前工作区，未自动commit/push。