# v0.1.0-rc.1 本地验收记录

日期：2026-10-05。范围是本地候选版，不是公开发布或真实客户背调结论。

## 已验证

| 项目 | 结果 |
|---|---|
| 独立仓库 | 新建 Git 历史，无远端，无生产输入/历史上传包 |
| 技能格式 | skill-creator quick_validate 通过；入口保持 40 行，详细规则按引用读取 |
| 26 字段兼容 | 4 组合成批次与旧导出器逐字段相同；另对现有 8 条每日导出只读核对 JSON/XLSX/上游凭据通过 |
| 干净环境 | 全新 Python 3.13 venv 安装 requirements，21 项 unittest 通过 |
| 干净检出 | 从本地提交独立 clone，运行同样 21 项测试全部通过；无需旧技能、采集插件或数据库配置 |
| 名单扫描 | 25 项离线回归通过，含 UTF-8/截断/尾部坏行/内存/超时/子进程清理；干净检出再次通过 |
| 真实名单读取 | 仅虚构查询词、本地比对；官方 UK CSV 完整读取 58,502 行，50,000,189 字节，25.92 秒；Job 上限 256 MiB/120 秒，读回验证通过 |
| PDF | 9 位全虚构客户，8 位匹配、1 位观察；生成 9 页；768×1024 逐页视觉检查通过 |
| 完成回执 | 合成样例完整推进并 finalize，completed_with_limitations；临时视图已清理，源 JSON/XLSX 保留 |
| 隐私与 Git | 明确源码/模板/测试允许清单；初始 24 个受跟踪文件无 16 个来源身份值；运行目录、venv、Hooks 绑定和日志被忽略 |
| 安装 | 技能安装目录使用目录链接指向唯一 Git 源码；旧技能未修改，每日任务未切换 |

名单此次命中数为 0，仅代表虚构名称的精确匹配控制，不是任何实际客户的合规结论。下载源 SHA256：`819d6a92c7e9a08c431f490de5ef3c24deba9cc78a04da8bb78c2a8452079725`。原始名单由扫描器临时目录处理，执行后不保留。

合成样例 PDF SHA256：`afd866565c505c1c85eebb2575f77d92dcfcb3a1d0e664e7547e176a4dc4bba4`。最终 9 张页面图与实际逐页检查的版本逐文件哈希一致。该视觉检查是代理技术检查，不等于用户视觉批准。

## 回归中修复

- 缺失评分不再显示 0；缺匹配状态、低识别强度或无源不能进入 PDF。
- 首页只摘要前 7 人，明示范围；长名摘要略写，详情完整。看图发现的首页评分说明溢出已修正，并增加文本验收。
- 缺 pypdf、无浏览器、渲染超时、缺视觉回执、修改 PDF 都不能获得完成回执。
- Windows venv 重定向启动器与单进程 Job 冲突：扫描 worker 直接使用同安装的基础 Python；未放宽内存、时限或进程数。
- 越级 finalize、手填通过标志、删客户/观察/名单回执、改变词表或来源输入、并发推进均有拒绝检查。

## Hooks：处理器通过，宿主激活未验收

本机 Codex CLI 0.160.0，hooks 功能开关为 stable/true。已合并项目级四事件配置；没有修改全局 Hooks 或信任记录。

已通过直接载荷与 Windows 原生命令 stdin 测试：Shell/补丁识别、会话隔离、歧义拒绝、恢复提示、PostToolUse 不按退出码通过、Stop 两次上限、暂停放行、锁冲突、注册幂等与配置保留。

注册后的当前聊天探测（code-mode 内部 Shell/补丁调用）没有观察到宿主 Hook 事件。Shell 由 Python 自身拒绝越级完成；补丁并未被 Hook 拦截。因此**不能声称 Hooks 已生效**。探测运行已标 cancelled，未伪造成研究完成。

还需用户完成 [Codex 原生 Hook 信任](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)，在加载可信项目配置的会话内重新测 Shell、补丁、code-mode、SessionStart、PostToolUse 和 Stop。此候选不绕过信任，宿主未触发/故障时仍保留 Python 门禁。

Windows 命令形式依据 [官方 command runner](https://github.com/openai/codex/blob/main/codex-rs/hooks/src/engine/command_runner.rs) 并以 cmd/stdin 实测，不假定交互终端 PowerShell 就是 Hook 的默认 shell。

## 发布前待办

1. 完成上述原生信任与宿主事件实测，再标记 Hooks 验收完成。
2. 维护者确认旧自有材料可按 MIT 分发、最终版权署名、远程仓库归属和公开发布授权。
3. 发布前再次检查实际提交历史；Git 忽略规则不等于隐私审计。当前没有配置远端、推送或发布。
