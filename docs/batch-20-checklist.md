# 第二十批运行验收清单

分开判定P0主线交互、P1记忆长测、正常恢复和十九批已完成MP／次数范围。最终结果与限制见[报告](batch-20-report.md)，精确事件及来源见[HOST_DEBUG](batch-20-host-debug.md)。

## 本次执行记录

- [x] 核对HEAD、当前分支及初始改动；单执行者，无分支、worktree、commit或push。
- [x] 找到十九批本地完整现场与批准包，复用NPC v1批准；原三数据库及正式包最终指纹不变。
- [x] 从原事件重建事实视图；测试正确ID错误正文、错误摘要、不同人名／物品／场景和自然改写。
- [x] 摘要不能独占关键证据；滞后事件补齐，恢复使用对应事件前缀，纠错不直接修改资源或隐藏事实。
- [x] 两人起始均为空时观察、NPC和回顾不补照明物、不生成无关幸运骰。
- [x] 当前可接触NPC支持普通问候、处境、钥匙、包扎并提问、尊称和接续；实际台词回应问题。
- [x] 最终新房间真实搜索成功后取得钥匙、交沈岚，再由实际持有者开门及解锁面板。
- [x] 合法复合转场执行；局部潜行实际失败后仍在原场景，后续真实投鞋产生通行机会。
- [x] 先短复验：short-c48五输入、21次新调用，通过普通停机恢复；副本与源调用另计。
- [x] 最终受影响回归785 passed，静态和差异检查通过，固定207文件及模型／包参数。
- [x] 新房间long-c39有效77分58.10秒，45输入、203调用、47次自然摘要；扣除全部已记录暂停，不拼接版本。
- [x] 晚期不提供答案地回顾两面便签、NPC、调查结果、物品及路线；正文与实际证据逐项一致。
- [x] 错误手电／纸条号码前提回查纠正，复问及恢复后记忆准确；18轮纯回顾无新操作。
- [x] 两次正常保存、停机、读档继续同一游戏，各8/8；恢复正确性独立判定。
- [x] 导出原事件、计划、调用、摘要范围、输入／时长、最终状态和存档，最终冻结核对无变化。
- [x] P0／P1／恢复分别通过；MP／次数保留十九批范围，未完成结局和未配置报纸拿取明确标注。

## 后续重跑方式

使用全新目录，保留正常随机失败。以下命令从仓库根目录执行；原批准包需要在本地存在：

```powershell
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/module_package.py load --bundle data/prepared/changan/batch-19/package-approved.json --directory data/prepared/changan/batch-20/my-run
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch20.py serve --run my-run --port 8020
```

另一终端执行正常玩家流程，输入保持自由文本：

```powershell
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch20.py setup --run my-run --port 8020
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch20.py turn --run my-run --port 8020 --text "我查看眼前的便签。"
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch20.py save --run my-run --port 8020
```

`save`通过正常接口暂停并保存。核对目录`server.pid`对应进程的命令行确为该run服务后停止；再次`serve`同一run，再执行`load --run my-run --port 8020`，检查`restore-comparison.json`并继续游戏。最后正常`save`，运行`export --run my-run --port 8020`并等待导出完成后停机。不要修改数据库、提供预设骰值或把未结算行动视为已完成。

重跑顺序：先保留失败条件的小型回归／真实模型短流程，再冻结版本启动60–90分钟新游戏；自然触发多次摘要，晚期先盲问再纠错，正常读档后复问。记录全部暂停，按实际发生的搜索、交接及结局判定；遇到新的关键错误保留失败，修复后重新冻结新房间，不拼接通过。

模型保持qwen3:8b、8192上下文及20／12000摘要阈值。没有前端改动时不增加前端lint/build与浏览器检查，不为A/C全路线、所有随机战斗或既有MP示例扩大测试。正式车卡配置仍为2职业、29技能、15–89岁。
