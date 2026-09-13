# 第十九批主机证据索引

完整现场在本地 `data/prepared/changan/batch-19/`，不随 Git 提供。原稿、原数据库和第十八批包未覆盖。HEAD 仍为 `c572687caf4f17f6258ca8c0d92faed10bb05619`，当前分支 `main`，无提交或推送。

C11 冻结清单 `candidate11-freeze.json` 含 241 个文件，清单字节 SHA256 为 `1172b28621e9b56f8d5022e71f8b22a9cd38f027bc4cc19fe7d832d6cc19d1e6`；结束比对 `final-integrity.json` 无差异。正式包字节 SHA256 为 `b47bf0fb5127e4bc46fdb1c5a97fd9b41fe358eea2595351b5e19d95411e6c87`；独立示例包为 `33ad128458c9ef16e6e300979e287d75e10f597c79af9841070750ff9eaa3bc6`。批准、来源与审计分别在 `approval.json`、`npc-supplement.json`、`provenance.json`、`approved-audit.json`、`example-audit.json`。

| 记录 | 结论与定位 |
| --- | --- |
| `p0-c1/` | 第十八批原房间的 SQLite 副本，C1–C8 开发失败保留。C9 #1116 门开/面板亮；快照 `d053a721-b1b4-451f-9949-6b8fd1f2d8c0` 新进程恢复后 #1147 仍开；#1153 普通确认，#1171 B 结算一次。`p0-verification.json`、`terminal-replay.json`：没有重复开锁、检定或奖励。历史模型调用不能算本批新增调用。 |
| `example-v4/` | 跨 C5/C10/C11 的独立效果短测。#97 POW 100/50 大失败，#101 MP10→6、次数3→2；#131 MP6→2、次数2→1；#151 不足未扣。#213 补剂2→7；#243 交共鸣器，AI #276→#298 自己使用10→6、最后一次耗尽；#328 交补剂，AI #363→#385 恢复6→10封顶。#501 休息到30分钟；快照 `19800133-8284-4e75-afd3-d5f4e289cef0` 停机恢复后 #535 到60分钟、玩家7→8。`resource-verification.json` 与八项恢复比对一致。不是固定版本长测。 |
| `long-c1/` | C11 全新正式房间 `0b701394-4557-49bb-b7f8-49275b1dbce1`。10:16:38.137602→11:18:37.292075 UTC，61分59秒；操作性暂停17.61秒；请求处理2585.23秒。37条玩家输入、174次模型调用/1349.90秒、39次自然摘要。`metrics.json` 区分游戏、请求、模型、暂停及归档时间。 |
| 长测保存/结算 | #600 急救待骰时快照 `1d2bf43c-35e2-4968-a082-1569433e6a75`，PID51580停止后PID29256恢复；`restore-comparison.json` 八项一致。原骰97/60与55/50未变；原治疗行动 `8d7c7cc8-de92-4dec-b479-26a400c66a9c` 继续，#611/#613 实骰1/30，乘务员HP3→4/11，重伤保留。最终快照 `e9dcfc02-a1d8-4f4a-8bcf-5c6953802c1f` 在 `final-save.json`，没有覆盖中途保存证据。 |
| 长测记忆/交互失败 | #111/#247/#815 等虚构已持有照明物；实际库存为空。`npc.spoke=0`。#921/#1173 场景3被说成穿过2。#978 错记便签并编造NPC指示；#979额外查背包→#1004幸运99/47，47来自原临时50−1d20(3)，实际Luck未扣。#1043仍虚构手电筒；#1494把真实投鞋（#1288）错记为纸条号码。39次摘要来源ID存在，但正文事实不可靠；末尾摘要只覆盖到#1179/#1207。 |

长测最终两名调查员均 HP12/12、MP10/10、SAN50、恢复进度0，Luck为60/50；游戏分钟0。没有本批正式模组 MP 效果、持有物交接、自然 SAN 消耗、真实攻防或结局。持续运行时长达到、原规则结算及恢复通过；记忆与自然交互验收失败，不能标作整体通过。

`long-c1/live-ledger.jsonl`、`model-calls.json`、`memories-final.json`、`checks-final.json`、`HOST_DEBUG-events.json`、`HOST_DEBUG-plans.json`、`HOST_DEBUG-cycles.json`、`room-final.json` 和 `game.db` 提供完整时序。原失败、模型日志与骰结果保留，未修补现场状态。模型继续为 Ollama qwen3:8b，ctx8192、输出900/规划1600、temperature0.3、think=false、keep_alive5m；摘要阈值20/12000。

测试组有重叠，不合并计数：`c11-directed.xml` 152 passed，`c9-regression.xml` 194 passed，`resources-final.xml` 117 passed。前端 lint/build 日志为 `frontend-lint-c9.txt`、`frontend-build-c9.txt`，其后前端文件未改；浏览器截图及验证在 `example-v3/browser/mp-cost/`、`example-v4/browser/recovery-progress/`。完整范围见 [coverage](batch-19-coverage.json) 与 [运行清单](batch-19-checklist.md)。
