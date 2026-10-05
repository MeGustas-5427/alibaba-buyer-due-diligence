# 采集契约

## 范围与入口

仅使用用户已有、已登录的 Alibaba 客户通会话。默认北京时间昨天 00:00 到今天 00:00，全部业务员；重跑明确给 `--target-date YYYY-MM-DD`，拒绝未结束的北京时间日期。不采集聊天、不登录数据库、不创建子任务、不上传或发送数据。

```powershell
python -X utf8 scripts/workflow.py collect --workspace <working-directory>
# 可选：--target-date 2026-10-04 --dictionary <private-dictionary.json> --sales-map <private-map.json> --node <node-executable>
python -X utf8 scripts/workflow.py next --run <run>
```

唯一 run 中顺序为：

`collection.input_resolved → collection.customer_detail_done → collection.profile_links_resolved → collection.buyer_profile_done → collection.nanyue_export_done → collection.final_validated → research.input_validated → … → research.final_validated`。

每个阶段由 `advance` 重验产物后签发证明，不手改状态。所有原始采集产物放在 `<run>/collection/`。每阶段向用户报告阶段名、数量及校验结果。最后一个采集阶段把实际 JSON/XLSX/phase_status 哈希锁定给同一运行的研究阶段，不新建研究运行。

## 浏览器执行

采集必须使用原生 Chrome DevTools MCP 工具连接现有 Chrome 登录会话、观察页面；完整读取 [原生浏览器接入](native-browser.md)，直接发现原生工具并执行有界 `list_pages`，不初始化 browser-client、不连接或排障扩展。CRM 只接受 `alicrm.alibaba.com` 或 `i.alibaba.com/hub/alicrm/`；主页只接受 `profile.alibaba.com/profile/myprofile.htm` 或 `my_profile.htm`。拒绝其他主机、凭据、非 HTTP(S) 协议与端口。链接原值保存在私有来源，HTTPS 升级只发生在导航/请求内存中。

采集脚本是已有页面中的只读业务 API 请求，不是 UI 的只读 DOM 检查。只通过原生 DevTools 的真实页面执行工具执行；禁止用 read-only evaluate 注入、绕过工具权限或控制另一浏览器来规避阻塞。默认人工确认原生调试弹窗，AHK 仍需单独授权及精确进程身份核验。脚本内部使用站点当前会话的防伪值，仅发回同一个 Alibaba 服务，不能从页面打印/导出 Cookie、令牌、原始 HTML 或请求头。Agent 不检查浏览器凭据存储。

原生工具缺失、权限冲突或连接失败时停止并报告，不回退浏览器扩展、其他浏览器或手动 Console。原生连接、登录、验证码、风控是不同层面的阻塞；遇到登录/验证码由用户处理，不能将故障写成零客户。

## 本地接收器与操作顺序

接收器在前台运行；用执行工具保留进程句柄。Windows 若使用 Start-Process，必须 `-WindowStyle Hidden`。每个 run 同时只能有一个接收器，默认端口 17889，可指定空闲端口；端口占用直接停止，不能杀不明进程。

1. `receiver --run <run> --kind list`。只使用返回的 `devtoolsFunctionFile`，通过原生 `evaluate_script` 执行，不把 Console 表达式错传为函数。文件包含短期本地访问凭据，不打印、不进对话或 Git；工具参数的宿主审计边界见原生接入说明。列表 worker 首个请求验证当前 API；失败立即停。等待 `list.wrapper.json` 后通过下面的 `receiver-stop` 结束接收器，再 `advance`，校验列表、字典转义和 ID 覆盖。
2. `receiver --kind detail`。同样注入 CRM 页，默认逐个客户作为烟雾检查及连续采集，任一请求失败立即停止派发。查看 `progress.json`：done=total、failed=pending=0 后停接收器并 `advance`。
3. 再 `advance` 进入链接覆盖验证：全部详情均有归属；无链接客户保留，非空但格式无效的链接阻塞，不悄悄丢掉。
4. 有链接：`receiver --kind profile`，通过原生 DevTools 在主页页面执行。无已打开的有效主页时，读取 `receiver-profile.private.json` 的本地凭据，仅在执行内存中向本机 `/profile-url` 请求导航目标并直接给原生 DevTools 导航工具；不打印、拼接或暴露完整定位 URL。无可用原生导航或私密传参能力则停止。收齐后停接收器并 `advance`。无链接：直接 `advance` 记录 `skipped_no_profile_links`，仍保留全部客户并继续导出。
5. `advance` 生成并校验 v3 JSON 和 26 列 XLSX；再 `advance` 重验六阶段并绑定研究输入。主页 JSON 格式变更会停止，不执行页面中任意脚本来“解析”数据。

所有子命令均通过同一个入口：

```powershell
python -X utf8 scripts/workflow.py receiver-status --run <run> --kind <list|detail|profile>
python -X utf8 scripts/workflow.py receiver-stop --run <run> --kind <list|detail|profile>
python -X utf8 scripts/workflow.py advance --run <run>
```

`receiver-status/stop` 先认证并比较 runId、路径、kind、端口、PID 和该进程启动标识，不按旧 PID 杀进程。正常停机删除自己的两种注入文件和私有凭据，保留业务与错误记录；异常退出留下的凭据已无有效服务，重启时轮换。若身份检查失败，停止复用/终止操作，先诊断。

接收器仅绑定 127.0.0.1，校验 Host、精确 Origin、每次启动随机凭据、runId、当前阶段、单条租约/客户 ID；限制 32 MiB 请求、30 分钟服务生命周期、10 分钟批次租约。拒绝任意来源、跨运行、重复结果、未发放批次和损坏 JSON；不记录请求 URL、凭据或原始报错。站点请求上限 30 秒。当前逐客户串行且反复校验来源，大批次较慢，不承诺大规模吞吐。

中断后首先 `status`，核对本地进度；只重采未成功的客户，成功 JSONL 保留。出现错误后必须先检查真实登录、风控或页面格式；确认已解除才用 `receiver --resume-after-review`。该开关记录已审查的恢复意图，不授予绕过登录/验证码的权限。不清空错误日志；成功结果才能消解对应历史错误。输出变化使依赖阶段失效。

## 数量与字典

故障后先使用 `python -X utf8 scripts/workflow.py diagnose --run <run> --signal <实际观察>`；可重复 --signal。支持 cim_query_failed、process_query_failed、ahk_identity_unverified、devtools_failed、receiver_identity_mismatch、unknown_failure 及登录/验证码/风控/API/主页停止信号；不再接受扩展故障信号。它只读当前运行，区分通信、进程查询、站点与本地产物失败；不授予新权限、不自动操作 AHK、不解除失败阶段。根因未确定时用 unknown_failure，不能靠手选信号证明根因。已核实的原生授权/宿主许可参数和连接核验流程见 [原生浏览器接入](native-browser.md)。保留退出码：stop=2、check/complete=0。

固定 pageSize=500；只有明确分页大小拒绝才退到 100。认证、风控、网络错误不得用分页重试掩盖。API total 必须存在且是非负整数，分页期间变化、重复 ID、缺行、日期或业务员范围不一致都会停止。

超过 5000 条：必须提供从当前账号核实的私有 `--sales-map`，不可使用作者账号的业务员表。格式：

```json
{"verifiedAt":"2026-10-05T00:00:00+08:00","includesUnassigned":true,"segments":[{"name":"SYNTHETIC example only","salesId":"exact-current-account-value"},{"name":"unassigned","salesId":"exact-observed-unassigned-value"}]}
```

示例值不能用于实际采集。未分配客户的筛选值也必须从当前账号观察取得，不猜 sentinel。每段仍 ≤5000；合并按精确 ID 去重，重叠内容须一致，与全局总数/最终复查数一致。没有映射或单段仍超限则停止，没有 allowOverLimit。

字典可选，格式兼容 `{"dictionaries":{…}}`；缺字典/未知码保留 unresolved，不能猜译。字典与销售映射路径/哈希随运行锁定，不能混用其他账号配置。

## 零客户

只有成功且明确覆盖目标日期、全部业务员的 API 响应 total=0 且 records=[]，经过列表/转义门禁后才能得到 `completed_no_customers`。保留 wrapper、空 enriched、证明及最终回执；其余阶段为 `not_applicable_no_customers`，不伪造六阶段通过、不制造空研究或 PDF。工具失败、空响应或缺 total 不适用此分支。
