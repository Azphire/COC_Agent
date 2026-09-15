# 第二十一批精简运行清单

结果见 [本批报告](batch-21-report.md)；数值来源见 [SOURCES.md](../backend/app/rules/definitions/SOURCES.md)。默认在当前分支工作，不自动提交。以下命令从相应目录执行。

## 1. 后端验证（`backend/`）

```powershell
.venv/Scripts/python.exe -m pytest tests/test_character_rules.py tests/test_coc7_creation.py tests/test_characters_api.py tests/test_rooms.py tests/test_combat.py tests/test_batch19.py tests/test_batch20.py tests/test_batch21.py tests/test_batch9.py tests/test_agent_memory_tools.py tests/test_agent_boundaries.py -q
.venv/Scripts/ruff.exe check app tests/test_batch21.py
```

新测试覆盖职业公式/属性选择、职业切换、分组/专业/年代限制、信用档位、背景/资产、旧版本卡、实例交接/拾回、装备初始化和模组遗失。年龄仍为15–89，90+不得作为已实现项验收。

## 2. 前端验证（`frontend/`）

```powershell
npm run lint
npm run build
```

浏览器自动流程（从`backend/`，选择尚不存在的运行目录，8000/5173端口需空闲）：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/check_batch21_browser.py browser-review
```

检查随机医生与购点音乐家：职业检索、职业分组、专业选择/独立分配、剩余点数、信用资产、背景、装备数量备注；保存、重载、确认、导出/导入。通过记录在`browser-c2/result.json`。

## 3. 新包及原包保护（`backend/`）

```powershell
.venv/Scripts/python.exe -X utf8 scripts/build_batch21_rules.py
.venv/Scripts/python.exe -X utf8 scripts/build_batch21.py
.venv/Scripts/python.exe -X utf8 scripts/module_package.py audit --bundle ../data/prepared/changan/batch-21/package-approved.json --output ../data/prepared/changan/batch-21/package-audit.json
```

规则生成器只生成当前1.1.0；旧1.0.0配置在`definitions/legacy/`。模组生成器先核对旧批准包哈希，仅生成新包及差异。审计保留历史覆盖缺口，不等同于新长测通过。

## 4. 真实模型短复验（仅隔离副本）

统一使用本地Ollama `qwen3:8b`、8192上下文。现有实测不要覆盖；需要复现时另取新目录名：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/clone_batch21.py ../data/prepared/changan/batch-20/long-c39 ../data/prepared/changan/batch-21/short-review
.venv/Scripts/python.exe -X utf8 scripts/check_batch21.py serve --run short-review --port 8021
```

服务器就绪后，在另一终端使用`check_batch21.py`的`load`、`turn --text '行动原话'`、`save`、`export`子命令，携带相同`--run`/`--port`。正常保存后停止该副本服务器，重新启动再`load`；`restore-comparison.json`须8项全为true。不要直接在历史库上启动验收服务。

物品路线：加载有明确来源的中途存档→已发现报纸且日期调查失败→拿取→给队友→换车厢阅读→队友实际返还→放下→正常保存/重启/读档→拾回→重复拿取→另一实物/改写→只回顾事实。核对执行回执、实例ID、隐藏状态和原检定，不以台词当作结算。

新卡路线：角色发布/分配→开团装备结算→新增专业实际检定→实际武器使用→放下/拾回→保存/重启/读档。`setup_batch21_card_room.py`和`check_batch21_equipment.py`用于本批原创隔离夹具；装备脚本的`finish`参数完成可能自动发生的回应回合并正常结束练习。该夹具不属于正式模组的NPC数值。

## 5. 证据目录

- `short-c1/`：B终幕副本、真实骰、逐人SAN及奖励；不计长测。
- `short-c2/`：普通物品、原始索引、模型调用、保存恢复。
- `new-card-c2/`：浏览器新卡、物理学、实际装备、原创战斗夹具、恢复。
- `browser-c2/`：两种创建模式的完整浏览器流程。
- `all-c1.xml`、`final.xml`、`final-items.xml`：第一轮全量与后续定向结果；有重叠，不相加。

原稿、会话令牌、完整数据库及主持私有内容仅保留在本地`data/prepared/changan/batch-21/`，分享时使用报告和不含私密信息的覆盖索引。
