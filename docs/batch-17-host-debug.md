# 第十七批 HOST_DEBUG（精简）

final-v14历史自然验收未通过，runtime_complete=false。用户后来明确批准NPC数值v1，批准记录在approved-v1；不追溯将本次历史测试改成已获批准或通过。

- HEAD：`aa77dda5cf3aeb1c0219afff2f685f252471b29f`；main；无commit/push。
- 冻结代码：`6697003916e7523ee73404f64a289c8cee29a48a5daedddee532cbfd53ef6e43`。
- 房间：`dc6724d1-085f-4d5d-83f4-41f787733344`；准备ID：`e5a7221a-cfa8-456c-b1a4-ec372dbd58fa`。
- qwen3:8b，8192/900，83次模型调用；638条HOST_DEBUG事件、457条玩家DTO事件，含系统过程事件。
- 方法回执host_confirmed=false、numeric_approval=false。实体公开中的内部origin=host标签来自既有执行路径，不是主机手改状态或补取物。

| 证据 | 审计结果 |
| --- | --- |
| #39/#45 | 手机幸运25/50，原骰与单一实例固定。 |
| #99/#154 | 翻便签与照明实际回执。 |
| #262/#265 | 周岚急救23/70，乘务员HP3→4；无DEX、武器也可治疗，伤员留在4号。 |
| #318/#360 | 中途快照315cb537-2781-449a-9805-9a13c47169e4，运行、人物、战斗、时间、检定五项一致。 |
| #270/#291 | NPC钥匙问答未给出内容，key_hint未获得。 |
| #362/#391 | focus是车厢，但工具是apply_module_action(keys, spot_hidden)；context_missing补充后仍precondition_failed，没有钥匙检定。 |
| #439/#476/#500 | 真人合法交接手机，队友真实开启铃声；人向队友发请求阶段的错误归属被拒绝，不能称整段识别无错。 |
| #530 | 真实1d3=2，实例分别保存，grapples为空；未把敌人总数当抓握人数。 |
| #564/#567–#570 | 观察说已看清，但SAN认为事实不足，发问后拒绝队列；没有SAN骰。 |
| #576/#600 | 严重失败：观察被两层模型裁成distant_sound_lure，matches=true。手机进入dropped_items，持续声源改为impact，safe_passage/clickers_lured/sound_throw_used=true；引用不能证明投掷与半车厢距离。 |
| #613/#618/#621 | 对SAN事实的回答被选成pickup，虽被动作核验拒绝，原SAN问题未完成。 |
| #637/#638 | 保留失败快照2a35d775-e416-4b9a-9aaa-dfd8a40ab57b；未利用错误标志推进，outcome=null，服务停止。 |

两次浏览器检查均通过，持有者周岚→沈砚，治疗入口可打开，无浏览器错误；没有主机按钮补执行。221项回归、前端lint/build、ruff与diff检查通过。普通攻防伤害、抓握／背负、关门、放下／拾取／消耗、SAN资源、A/B/C终态与奖励幂等的未自然分支均仅为定向夹具。

历史目录：acceptance-v1/v2记录物品和真实骰；acceptance-v3未用于模型验收，acceptance-v3-final转场失败；debug-route-v4/v5及final-v6/v7处理移动、NPC治疗、照明；debug-route-v8有治疗／交接局部成功，也有污染状态；final-v8/v9为引用与转场问题；final-v10有局部治疗／交接／读档，鞋子投掷误引用队友手机被拒绝；final-v11暴露引号引用误拒绝；final-v12仍漏钥匙检定；final-v13仅有未用于模型验收的清单。均不拼接为本次通过。

原始证据在final-v14的HOST_DEBUG-events.json、HOST_DEBUG-plans.json、HOST_DEBUG-cycles.json、model-calls.json、room-final.json及model-verification.json。此次只恢复曾被编码为问号的摘要，未改动冻结证据。数值批准另有approved-v1/approval.json、manifest.json及加载核对，不改变这些错误结论。
