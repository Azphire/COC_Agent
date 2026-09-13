# 第十九批运行验证清单

区分代码回归、独立示例、历史存档定向检查与固定版本连续游戏。未完成项不能由其他类别替代。

- [x] 核对 HEAD `c572687caf4f17f6258ca8c0d92faed10bb05619` 与初始工作区；不建分支、不提交或推送。
- [x] 正式包字节 SHA256 与批准记录一致；原件及历史数据库不改动。
- [x] 核对本地规则书 PDF 148／印刷 147 页的 MP 自然恢复规定。
- [x] 旧卡补 MP 上限，保留当前 MP；恢复进度与原事件回执随快照保存。
- [x] 回归：59/60 分钟边界、封顶、不积存满值时间、重复事件、濒死/CON/发作义务。
- [x] 回归：明确实例、同类物品与持有者、次数/资源不足、失败耗用、原子提交、重复请求。
- [x] 回归：拒绝未支持的 HP/SAN/Luck/伤势/弹药字典效果，保留原服务边界。
- [x] 独立实测：自然输入激活、POW 大失败仍按配置消耗、MP 不足零扣除、饮用恢复。
- [x] 独立实测：实际交给 AI 后由 AI 自己使用，消耗 AI 的 MP 与该实例最后一次。
- [x] 独立实测：AI 恢复封顶、半小时恢复进度、正常停机读档后跨一小时边界。
- [x] 旧存档定向检查：数轮后门状态追问、正常保存/停机/重启后追问，无新增开锁或骰。
- [x] 旧存档定向检查：普通“确认结束，继续结算”完成终幕；重复请求返回原事件。
- [x] 前端 lint/build；真实浏览器角色卡 MP、伤势、武器、持有物与剩余次数显示。
- [x] 固定 C11 完成独立会话：61分59秒，扣暂停后61分42秒；有效请求处理43分05秒，模型耗时另列。
- [x] 连续游戏内自然生成39次摘要；在待急救骰时保存、停止并恢复，八项状态比对一致。
- [ ] 晚期回忆正确性、自然 NPC 台词和正式模组物品交接未通过；整体长测验收失败，见报告。

本地运行（仓库根目录，使用新的运行目录）：

```powershell
# 已有本批包时跳过构建；仅在缺少示例包、具备本地来源时运行：
# backend/.venv/Scripts/python.exe -X utf8 backend/scripts/build_batch19.py
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/module_package.py load --bundle data/prepared/changan/batch-19/example-package.json --directory data/prepared/changan/batch-19/my-example
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch19.py serve --run my-example --port 8019
# 另一终端：
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch19.py setup --run my-example --port 8019
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch19.py turn --run my-example --port 8019 --text "我阅读桌上的说明。"
```

正式模组将 `--bundle` 换为本批 `package-approved.json`，并用另一个新目录。`save` 正常创建快照并暂停；停止自己的服务进程后重新 `serve`，再 `load` 恢复。`export` 将现场证据写入该运行目录。多个房间或变更前后的调试时间不能拼成连续验收。

构建器依赖用户本地第十八批批准包与原稿，不生成替代正式原稿或伪造批准。构建会重新生成示例的 IR 元数据，固定版本验收开始后不要重跑构建器。

独立示例可依次输入“我拿起共鸣器”“我使用共鸣器校准激活”“我拿起补剂，饮用一剂”，按实际骰果继续。交给同伴后，请同伴自己使用；明确实例时使用角色卡显示的编号。前往休息室后输入“我在这里休息半小时”，普通聊天不推进时间。

必要检查命令（从仓库根目录）：

```powershell
Push-Location backend
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_batch19.py tests/test_action_adjudication.py tests/test_batch18.py -q
Pop-Location
Push-Location frontend
npm run lint
npm run build
Pop-Location
```

本次结果见 [报告](batch-19-report.md)、[PUBLIC](batch-19-public.md)、[HOST_DEBUG](batch-19-host-debug.md) 和 [运行覆盖记录](batch-19-coverage.json)。
