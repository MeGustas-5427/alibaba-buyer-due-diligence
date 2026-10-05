# 必需的原生 Chrome DevTools 接入与恢复

本技能的采集必须直接使用原生 Chrome DevTools MCP 工具连接已有 Chrome 会话。不要初始化 browser-client、连接或排障浏览器扩展，也不以扩展失败作为接入条件。原生工具缺失、权限冲突或连接失败时停止，不回退扩展、其他浏览器或手动 Console。必须遵守更高优先级的宿主限制；本技能不修改全局配置或授予工具权限。

本仓库不包含 MCP 服务端、Chrome 配置、浏览器扩展安装器或 AHK 自动点击脚本。页面采集、Python 接收器和门禁自带，不依赖旧私有插件。DevTools 工具由宿主提供；原生调试弹窗默认由用户亲自确认。AHK 仅是已有受信任辅助程序的 Windows 用户的可选接入。

## 1. 直接预检原生通道

先 `status --run <run>`，保留原始失败操作。当前用户明确要求按本技能执行采集时，原生 DevTools 是该任务的指定路线；不重复询问已明确的路线选择，也不从旧任务继承权限。核实当前授权与宿主许可后记录两个事实，不需要先制造失败信号：

```powershell
python -X utf8 scripts/workflow.py diagnose --run <run> --devtools-authorized --host-allows-devtools
# CIM 查询失败时，保留所有尚未解决的观察：
python -X utf8 scripts/workflow.py diagnose --run <run> --signal cim_query_failed --devtools-authorized --host-allows-devtools
```

参数只是对已核实权限的声明，脚本不授予权限。更高优先级的宿主规则禁止原生通道时，不能填写 `--host-allows-devtools`，应报告冲突而不是转向扩展。若还允许使用特定 AHK 辅助程序，另外给 `--ahk-authorized`，不能从 DevTools 授权推导 AHK 授权。只有原生连接调用实际失败才用 `devtools_failed`；根因未确定用 `unknown_failure`，诊断分类不是根因证据。

| 结果 | 后续动作 |
|---|---|
| `NATIVE_DEVTOOLS_REQUIRED` | 核实当前原生授权与宿主许可；不尝试扩展 |
| `HOST_CHANNEL_NOT_ALLOWED` / `REVIEW_BROWSER_AUTHORIZATION` | 停止原生通道；解决宿主限制或取得当前授权 |
| `CHECK_AUTHORIZED_DEVTOOLS` | 仅做下述一次有界连接核验；仍 `collectionAllowed=false` |
| `CHECK_AHK_WITHOUT_CIM` | 限定进程核验；查询失败不等于不存在，不启动重复程序 |
| `AHK_IDENTITY_UNVERIFIED` | 不复用、不终止、不替换、不再启动 |
| `DEVTOOLS_CONNECTION_FAILED` | 原生通道失败；保留错误，停下诊断，不循环重启或切换通道 |
| 站点阻塞、未解决采集错误、校验失败、非活动运行 | 优先停止；通道授权不能解除 |

退出码：`stop=2`、`check/complete=0`。在专用 PowerShell 调用中用 `$diagnosticExitCode = $LASTEXITCODE`、`exit $diagnosticExitCode` 保留 Python 退出码。不要只看外层 Shell 成功。

## 2. 核验原生连接

1. 从当前工具目录发现原生 `chrome-devtools` 的 `list_pages`、`select_page`、`evaluate_script` 或等价原生能力，核对**当前 schema 和宿主许可**，不要固定服务器名或把只读 DOM evaluate 当执行通道。没有能力则停止并说明缺失的原生工具，不改走扩展，不自行安装/启动调试服务或独立 Chrome profile。
2. 默认让用户确认 Chrome 原生远程调试弹窗。若明确选用 AHK，先按下一节核验/启动辅助程序，完成连接后立即停止；连接失败也做相同的身份核对清理。
3. 对已有原生 Chrome 做一次有界 `list_pages`，选择真实 CRM 或主页页签并前置，不采用其他 profile。不将包含私有买家定位参数的列表结果复制到报告；安全导航目标仍按接收器 `/profile-url` 内存解析流程处理。
4. 可将 `scripts/collection/page_probe.browser.js` 的函数交给已允许的页面执行工具。它只返回主机、路径和加载状态，不读取 Cookie、私有 URL 查询参数或浏览器凭据存储，也**不证明已登录**。CRM 接受 `alicrm.alibaba.com` 和 `i.alibaba.com/hub/alicrm/`；主页必须是 `profile.alibaba.com/profile/myprofile.htm` 或 `my_profile.htm`。仍需观察真实页面的登录/验证码/风控状态。
5. 通过以上检查后，按 [采集契约](collection-contract.md) 启动当前阶段接收器。首次实际 API 请求由采集器验证；API 失败立即停止，不能将页面探针或连接成功当作采集成功。

## 3. 原生 DevTools 执行接收器函数

采集只使用 `receiver` 返回的 `devtoolsFunctionFile`：它是函数表达式，供原生 `evaluate_script.function` 传参；`pageId` 来自本次真实页面发现，不猜 ID。`snippetFile` 仅为历史兼容产物，不作为本流程的执行入口。若工具提供 `dialogAction`，使用 `dismiss`，不默认接受页面 JavaScript 弹窗；原生 Chrome 调试授权是独立的用户确认。不重复启动 worker。

通过宿主允许的本地文件读取能力在内存中取字符串并传给原生工具，不输出到对话、公共日志或 Git。这些文件含当前接收器短期凭据；工具参数可能被宿主审计，不承诺工具调用记录完全不保留它。无法在允许的边界内传参时停止并报告，不改走其他通道。Python 正常停机删除两种注入文件及私有凭据。

函数立即返回 `started`；后台 worker 保存真实结果。先读本地 progress，完成后独立执行 `receiver-stop`，再 `advance`。仍使用同一批输入和六阶段采集校验，研究七阶段不变。只续未成功客户；恢复原生连接不能清空错误、重采成功客户或改完成标记。

## 4. 可选 AHK 辅助程序生命周期（Windows）

本工具不提供或下载点击器。使用者须事先审阅明确指定的 `.ahk`：仅处理本次 Chrome 原生“允许远程调试”弹窗，不能点击登录、验证码、风控、支付或其他授权。建议辅助程序自身带短时自动退出，防止宿主中断后继续扫描；未审查的脚本不得启动。默认人工确认不需要安装 AutoHotkey。

生命周期脚本优先使用 PATH 中的 PowerShell 7 (`pwsh`)，没有时才选 Windows PowerShell。若所选运行时的执行策略或组织策略拒绝脚本，直接停止，不添加 `ExecutionPolicy Bypass`、不修改策略、不再换解释器重试。本地验收使用 PowerShell 7；Windows PowerShell 5.1 在维护者机器上受策略阻止，兼容性未验收。

```powershell
# 没有本次身份记录时，提供本机真实 AutoHotkey v2 路径；占位符须替换。
python -X utf8 scripts/workflow.py browser-helper --run <run> --action inspect --executable <absolute-AutoHotkey64.exe>
# 仅当核验确无辅助进程，且当前用户和宿主确实批准时：
python -X utf8 scripts/workflow.py browser-helper --run <run> --action start --executable <absolute-AutoHotkey64.exe> --script <reviewed-local-helper.ahk> --devtools-authorized --host-allows-devtools --ahk-authorized --approval "当前用户授权该确切辅助程序的原话" --host-policy "当前允许此通道的宿主规则引用"
# 连接成功或失败之后，均核验并停止自己启动的那一个：
python -X utf8 scripts/workflow.py browser-helper --run <run> --action inspect
python -X utf8 scripts/workflow.py browser-helper --run <run> --action stop
```

仍未解除的站点/工具信号一并重复 `--signal`；不能省略信号获得更宽松结果。登录、验证码、风控、API 失败应同时 `block --reason <真实原因>`，后续启动会被活动状态门禁拦截。诊断不自动解除 `block`。

`inspect` 不用 CIM。有本次记录只查精确 PID；没有记录时只查 AutoHotkey64/32，不枚举无关进程。查询失败/权限不足不等于不存在；有进程但无法绑定本次脚本时返回 `unverified`、退出 2。`start` 拒绝任何已有 AHK 进程，使用 `Hidden + PassThru`，保存 run/PID/可执行路径/启动时间/脚本与可执行文件哈希。`stop` 持有并核对精确进程句柄后才停止它；不按名称杀进程，也不根据旧 PID 终止其他程序。脚本或身份变化时停下人工核验。

权限与身份记录仅存在本次私有输出目录，不是系统级防篡改证明，不能充当另一次运行或另一个用户的授权。工具不修复注册表、不伪造 DevToolsActivePort、不读取浏览器凭据、不安装扩展、不触碰每日任务。
