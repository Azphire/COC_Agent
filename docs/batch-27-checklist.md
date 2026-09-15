# 第 27 批简短操作与验收清单

基线 `a84d5dd2e54f217d37fdd768f789f9a6ed662c86`，当前分支 `main`，不自动 commit/push。结果见 [报告](batch-27-report.md)，完整状态见 [开发清单](development-checklist.md)。

## 日常操作

- [ ] 双方安装并登录 Tailscale，设备已获准互连。
- [ ] 主机运行 `start.cmd --tailscale`；自定义端口加 `--frontend-port 5187`。
- [ ] 等待本机入口和实际 Tailscale 异地地址，创建房间，将异地地址与当前邀请码告知玩家。
- [ ] 玩家用邀请码和显示名加入，在大厅选择 CharacterExport JSON，查看重算预览后提交。
- [ ] 主机打开当前提交预览；已有分配先取消，再接受并分配，或填写说明拒绝。收到新版本后重新预览。
- [ ] 接受后的玩家直接准备；其他玩家可选择主机发布的空闲角色再准备。
- [ ] 主机按 README 配置模组与 KP，所有活动席位准备后开始；结束启动会话时 Ctrl+C。

错误提示与分层连接排查见 [README](../README.md#tailscale-异地加入与玩家角色提交第-27-批)。

## 本批交付核对

- [x] 成员令牌提交；服务端导入校验与重算；原骰标记 imported，不重掷。
- [x] 一个当前待处理版本、替换旧版失效、接受／拒绝持久化。
- [x] 主机接受时原子确认、冻结、发布、初始化、分配；重复与失败重试不重复写入。
- [x] 主机／提交者完整预览；其他成员列表、详情及 WebSocket 权限过滤。
- [x] 真实 Windows、三个 Chrome 身份、HTTP／WebSocket 同源代理、准备开始、本机短断网重连、Ctrl+C 清理。
- [x] Tailscale 缺失、未登录、正常 IPv4、自定义端口及离线／超时等检测测试。
- [ ] **真实异地设备验收**：当前主机无 Tailscale CLI，未使用不同网络的第二台设备。

## 真实异地验收（待执行，不能用同机浏览器代替）

1. 记录当前 HEAD、Windows／浏览器版本、主机和玩家的网络条件（例如家庭网络与手机热点）、双方 Tailscale 已连接状态和实际前端端口。仅保存本机必要状态，不导出整个账户／设备清单。
2. 使用新的隔离 DATA_DIR、数据库、checkpoint、knowledge 和 model-settings 路径，保留原稿、批准包、原存档与 `.env`。玩家设备须位于与主机不同的实际网络。
3. 主机按本清单启动；远程玩家完成加入→选择含三类自定义专业的角色文件→预览→提交；主机接受并分配，双方核对相同角色名、冻结属性与 HP／MP／SAN／Luck。
4. 绑定原创《停摆的钟楼》与 KP，使用已获准的本机 Ollama；所有活动玩家准备后开始。远程玩家发送一次自然行动（例如“我环顾钟楼入口，看看附近有什么线索”），等待实际事件和角色状态同步。记录原始输入、事件序号、实际 provider/model 与调用数，不重掷追求成功。
5. 玩家短暂断网再恢复，确认自动重连、补齐遗漏事件，角色与检定状态一致且没有重复动作／初始化。记录前后序号和截图。
6. 主机 Ctrl+C 正常关闭，只核对本次前后端退出及端口释放。保留原始失败；修复后使用新的复验记录。
7. 只有以上不同网络的真实设备流程成功，才能将“异地联机实测”改为通过。本批不需要正式私有包 OpenAI 外发授权，不因此阻塞代码交付。

## 复现本批定向检查

以下命令从仓库根目录运行；为每轮使用**新目录名**，不覆盖原证据。不需要再次跑完整长团。

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_batch27.py backend/tests/test_batch27_tailscale.py -q -p no:cacheprovider --basetemp=data/prepared/changan/batch-27/pytest-new --junitxml=data/prepared/changan/batch-27/tests-new.xml
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch27_ui.py ui-new
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build
```

浏览器脚本必须在真实 Windows 下运行，先检查 8027／5187 空闲；使用新 Chrome 配置、隐藏独立测试控制台和新数据库，模型配置为空且目标为 example.test，不加载用户 API 密钥或调用模型。Chrome GPU 如受执行沙箱限制，需在获准的运行环境执行。测试所有必要数据均可从源码生成，不依赖 Git 未提供的正式包。
