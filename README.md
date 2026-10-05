# Alibaba Buyer Due Diligence

本地 Alibaba 每日客户背调 Skill，`0.1.0-rc.1`。输入为既有每日 v3 JSON + 对应 XLSX；输出是全量研究、总报告、矩阵、观察池和合格匹配客户 PDF。**没有数据上传或数据库连接功能。**

Python 校验阶段顺序与产物；Codex 负责公开检索和判断；可选 Hooks 提醒并拦截能够识别的越级调用。没有模型运行框架、爬虫服务或任务队列。

## 安装和运行

验收平台：Windows，Python 3.13（首版只声明这个实测组合）。需要已安装 Chrome/Edge 及中文字体。依赖版本锁定，不打包浏览器和字体。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -X utf8 scripts\workflow.py init --input <daily.json> --xlsx <daily.xlsx> --workspace <working-directory>
.venv\Scripts\python -X utf8 scripts\workflow.py next --run <run-directory>
.venv\Scripts\python -X utf8 scripts\workflow.py advance --run <run-directory>
.venv\Scripts\python -X utf8 scripts\workflow.py finalize --run <run-directory>
```

`advance` 一次只推进一个阶段；缺证据返回非零。缺视觉验收时先生成 PDF 并停留，查看后补回执再执行。所有路径相对脚本或显式参数，不依赖旧插件或开发者机器。

将仓库根目录安装/链接为 Codex 的 `skills/alibaba-buyer-due-diligence`，以 `$alibaba-buyer-due-diligence` 调用。开发时建议目录链接到唯一源码，不维护两份副本。发现行为以当前 Codex 版本为准。不要改变旧技能或每日任务，切换需显式选用新技能。

默认产物位于工作区 `outputs/<sourceDate>/<runId>/`。`--out` 仅改变基础目录，日期和唯一运行 ID 仍会追加。原始文件只读；私有真实产物禁止提交。详细规则见 [输入契约](references/input-contract.md) 和 [研究契约](references/research-report-contract.md)。

## Codex Hooks

```powershell
.venv\Scripts\python -X utf8 hooks\codex_guard.py --install <working-directory> --python <absolute-python.exe>
```

合并工作区 `.codex/hooks.json`，保留其他 Hooks；不更改全局配置、不写信任记录。`hooks/codex_hooks.example.json` 只是跨平台示意，安装命令生成实际路径。更新 Python/仓库路径或处理器定义后重新检查。

随后使用 Codex 原生 `/hooks` 检查并信任精确的定义；项目配置层也必须被信任。没有原生信任和实测，不称“已启用”。[官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks) 说明了信任、工具覆盖、code-mode 嵌套调用和失败行为。

初始化读取真实 `CODEX_THREAD_ID`，或显式给 `--session-id`。绑定同时核对会话、规范化工作区和 runId；其他会话/无绑定直接返回。多个活动候选会报告歧义，不选最近一个。

四种事件：SessionStart 提示恢复位置；PreToolUse 对明确指向当前运行的过早 PDF/finalize 或修改完成状态的补丁拒绝执行；PostToolUse 重新验证产物；Stop 最多自动补做同一缺口两次。用户暂停/取消/外部阻塞允许结束。PostToolUse 无法撤销已经发生的写入。

支持工具的直接载荷单测不等于宿主触发：正式启用需在隔离合成运行分别验证 Shell、apply_patch、code-mode 内部调用的拒绝、PostToolUse 失效处理、SessionStart 恢复、Stop 续跑、并发及无绑定隔离。记录实际事件；未测的标为未测。宿主不调用、未信任、超时或损坏输出可能失败开放，但 Python `finalize` 仍会拒绝缺证据完成。

这是过程与产物校验，不是对可任意写文件的攻击者的防篡改系统，也不能从一份手写检索笔记证明现实事实真实。不能承诺“AI 永不漏步”。

## 测试与全虚构样例

```powershell
python -X utf8 -m unittest discover -s tests -p "test_*.py" -v
python -X utf8 tests/test_screen_uk_sanctions.py
python -X utf8 examples/synthetic_demo.py --out outputs/synthetic-demo
```

所有样例从零构造，使用显式 SYNTHETIC 标记和示例域名，不是脱敏真实客户。演示到视觉门禁为止，不伪造人工看图通过。测试默认离线；PDF 检查使用本机浏览器。英国名单 CLI 要求 Windows Job Object，其他平台明确拒绝不受保护的执行。

## 来源、许可证和发布边界

- 新写的独立映射、Python 工作流与 Hooks；26 字段按既有导出契约实现，没有动态加载或复制采集器插件。
- PDF 生成器、固定样式及英国名单扫描器/回归检查源于用户维护的 `nanyue-deep-due-diligence` 基线 `fbbe26b0df7ee972dc6e13466109ca5e37656746`，本地迁移时去掉旧演示数据和上传相关功能；原仓库未附根许可证，外部发布前维护者应确认这些自有材料的可授权性及版权署名。当前 MIT 是拟发布许可，不能替代该确认。
- 内嵌 Phosphor 图标保留 [第三方 MIT 声明](assets/THIRD_PARTY_NOTICES.md)。依赖由包管理器安装，不随仓库重新分发；未复制商业字体。
- 公开发布前检查 `git ls-files`、暂存差异、完整提交历史、忽略规则和来源许可；确认仓库归属及发布权限。真实运行目录、会话/绑定文件不进 Git。当前候选版本不自动推送或创建公开仓库。
