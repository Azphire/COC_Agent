# 第二十四批运行清单

## 日常开团

1. 双击根目录 `start.cmd`，按当前配置解锁主机。
2. “模组准备”→“导入已准备模组”，选择本机 `data/prepared/changan/batch-21/package-approved.json`。
3. 核对文件 SHA256 `93dee39824e41a0d9544fc5e869845c0ffcaeb43dd54603aec1eae840372ddd4`，确认原稿仍在 `data/modules/常暗之厢/`。
4. 检查 7 场景／44 实体／12 转换、NPC v1 已批准、当前必需操作无缺项。历史来源缺值与实测范围分别查看。
5. 创建房间、分配角色、绑定 KP，在“批准版本”选择导入版本并绑定；准备后开始。导入不调用模型，游戏使用当前系统模型配置。
6. 正常描述调查对象，例如“我翻找工作台背面和抽屉”。需要时完成检定；失败保留原骰，发现与拿到分开核对。
7. 保存→暂停→载入；停机重启后可继续原存档和检定。

## 本批已核对

- [x] 基线及原修改；历史搜索事件与原骰只读核对。
- [x] 两条搜索路径的成功／失败、限定目标、调查板、记忆、唯一回执和等待检定恢复。
- [x] 主机导入、原稿哈希／引用／批准记录校验、事务回滚及不可绑定保护。
- [x] 真实 Chrome 导入→绑定→开团；重复导入、刷新及停机重启后复用。
- [x] 最终正式包 44 实体字段与补充值保真；旧房间冻结。
- [x] 独立 `future_news` SAN 夹具真实原骰 35/50，损失 0，停机载入 9/9。
- [x] 最终专项 24 passed；前端 lint/build；Ruff；README 更新。
- [ ] 真实 API 自然调查、对应结果、实际持有、摘要、保存／停机／载入后继续：等待具体外发授权，当前新增调用 0。

## 定向复验命令

从仓库根目录执行。已存在的数据目录不删除；测试 `--basetemp` 应使用新的名称。

```powershell
$env:PYTHONPATH='backend'
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_batch24_packages.py backend/tests/test_batch24_search.py -q -p no:cacheprovider --basetemp=data/prepared/changan/batch-24/pytest-review
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch24_evidence.py live-c4 --san-run live-c3
```

真实 API 短段待确认将必要模组上下文、隔离房间状态及行动发送到 `https://api.openai.com/v1/`，使用已有 `gpt-4.1-mini` 后，在**现有最终房间**继续：

```powershell
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch24.py live-c4 --resume --live
```

上述 `--live` 会产生真实 API 调用；零推理 UI 复验省略 `--live`。不要为求成功重建路线或删除数据库；中断后先检查已保存 cycle/check，再沿原状态继续。独立 SAN 夹具已完成，不重复掷骰。

细节、原始证据位置和验收边界见[本批报告](batch-24-report.md)。
