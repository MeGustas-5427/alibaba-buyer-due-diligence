# 研究与报告契约

## 阶段与恢复

`input_validated → research_prepared → research_recorded → identity_classified → analysis_validated → reports_validated → final_validated`。

`next/status` 只读；`advance` 验证当前阶段并原子保存证明。每个阶段校验实际文件，指纹包含输入、规则和证据。研究/引用/笔记改动使依赖阶段回退；不删除研究或自动恢复成成功。恢复时重复推进确定性校验。输入或规则变更需新 run。

运行级 OS 文件锁在进程退出后自动释放；`.lock` 文件存在不是锁仍被占用。不要删锁或手改状态。`pause/block/cancel/resume --run ... --reason ...` 记录真实生命周期；用户指令和客观外部阻塞不能被当作完成。脚本没有 `force`/跳步命令。

## research.json

根精确字段：schemaVersion=`alibaba.buyer-due-diligence.research.v1`、inputSha256、sourceDate、createdAt、records。骨架由脚本生成，pending/空白不能通过。全量记录顺序与原输入一致；不得改写四个展示身份 companyName/contactName/salesperson/countryRegion。

每条记录除展示身份和 sourceRecordIndex 外包含：

- matchStatus：matched/unconfirmed；identificationStrength：high/medium/low。matched 不允许 low。
- matchReason、identityEvidence、priority（P0/P1/P2）、publicSummary、riskJudgement、followup。
- sources、attempts、riskChecks、screening、dimensions、scores。

`identityEvidence[]` 为 `{field, sourceIndexes, reason}`。匹配至少有 companyName 和另一个在原始视图存在的独立身份锚点（国家、官网、地址或联系人），且各有来源支持；high 至少两个独立发布主体。公司与联系人同名不能自动归因。同名候选在观察池明确标为未确认。

## 实际检索与引用

`attempts[]` 精确字段：

```json
{
  "kind": "identity",
  "language": "en",
  "query": "safe public company query",
  "checkedAt": "2026-10-05T00:00:00+00:00",
  "status": "succeeded",
  "detail": "What was actually observed, with limitations",
  "evidenceFile": "evidence/record-0-identity.md",
  "evidenceSha256": "sha256-of-that-file"
}
```

kind 为 identity/legal/compliance/payment/reputation，每位客户五类都有实际尝试；status 仅 succeeded/limited。一次失败也保存工具错误或观察摘要，不能伪造成成功。观察文件为本次运行内 ≤256 KiB 的脱敏 Markdown，保存查询、工具/网页定位、看到的内容与失败情况，不保存整个网页/会话、凭据、邮箱电话。哈希通过 `Get-FileHash` 或标准库计算。缺工具未执行时不能写 attempted；用 block。

`sources[]` 为 `{url,title,publisher,publisherGroup,accessedAt,supports,attemptIndex}`。URL 必须公共 HTTP(S)，不带凭据/私有定位。supports 列出 identity、风险类别或九维键；引用下标从 0 开始。attemptIndex 绑定实际观察。同 URL 去查询参数/片段后视为同一来源，不重复计算。

publisherGroup 填真实共同编辑/所有权主体；镜像、转载、同一机构不同域名仍用相同组。程序对同主机/父子域名和同组保守去重，无法自动识别所有关联企业；需研究者判断，不以不同 URL 冒充独立来源。

## 风险与名单

riskChecks 精确包括 legal/compliance/payment/reputation，每项 `{status,summary,attemptIndexes,sourceIndexes}`。status=passed 表示这项定义范围内已查，不代表无风险；需成功尝试与来源。未能充分核验写 limited、具体限制与已尝试证据，不用空引用声称通过。

screening 为 `{status,reason,termsFile,receipt}`。status=passed 仅用于受保护扫描器完整执行；termsFile 是本 run 内 JSON 数组，包含当前公司名；receipt 指向实际 UK CSV 回执。独立示例：

```text
python -X utf8 scripts/screen_uk_sanctions.py --terms-file <run>/uk-terms.json --out <run>/uk-screening.json
```

命令只下载官方名单，不上传查询词；输入词本地比对。Windows Job Object 约束 256 MiB/120 秒/单 worker，UTF-8 严格解码、字节/行/身份/命中上限、完整 EOF 和来源/词 hash 均需通过。失败非零，不产成功回执；保留失败观察，用新路径重试。禁止自行取消保护或使用无限内存全量名单解析。

status=limited 必须 reason，termsFile/receipt 为 null，compliance 也必须 limited；说明实际尝试为什么未完成。完整精确名称零命中不等于别名、所有权、其他名单和所有司法辖区均已核查。候选命中须人工实体核对，不直接定罪或无条件放行。

## 九维与评分

dimensions 必须包含：companyFundamentals、regionAndMarket、procurementPreferences、procurementScale、decisionChain、riskSignals、supplyChain、communicationAndNegotiation、growthAndStrategy。

每项 `{summary,evidenceLevel,sourceIndexes}`，evidenceLevel 为 verified（至少两个独立发布主体）、single_source、inferred（有源但明确为推断）、insufficient。前两者是公开事实证据状态；后两者不能混写成已核实。双源覆盖分母固定全量客户 × 9，缺资料也在分母中。

unconfirmed 的 scores 必须 `{}`。matched 包含 match/potential/risk/profit/relationship/overall，每项 `{value,reason,sourceIndexes}`。value=null 或有限数值 0–10，非空分数须证据。null 必须说明无法评估的原因。风险高分更差；其他项高分更好。match 是卖方产品与采购需求适配，不是身份识别；没有卖方上下文写 null。利润、关系、采购量等没有依据就不推算。综合评分由研究者解释依据，不能靠程序平均掩盖缺项。

## 报告和 PDF 门禁

Markdown/匹配内容由验证后的 research 自动生成，并逐次比对；不要独立编辑报告导致事实漂移。全部客户在总报告和 matrix，观察池只含未确认；合格 matched 的 PDF 一位一详情页，首页最多七行并明示范围。完整依据在 Markdown/JSON，PDF 摘要可能截短。

PDF 先验证页数、768×1024 CSS 像素页面、文本、身份、元数据和内容哈希；缺 pypdf 不跳过。还需逐页视觉检查，保存本 run 的 `visual-review.json`：

```json
{
  "pdfSha256": "actual-current-pdf-sha256",
  "status": "passed",
  "pagesReviewed": [1, 2],
  "issues": [],
  "reviewer": "Actual reviewer or agent identity",
  "checkedAt": "2026-10-05T00:00:00+00:00",
  "notes": "Actual page-by-page observations: clipping, Chinese glyphs, identities, scores and footer"
}
```

页列表必须完整，示例中的两页不是固定页数。程序不能代替看图，也不能自动填通过。改 PDF 后旧回执作废。无匹配客户不得生成空 PDF，回执标 skipped_no_matched_customers。用户明确不需要 PDF 时在 init 提供 `--skip-pdf-reason` 和 `--skip-pdf-approval`（用户原话）；技术失败不允许转成跳过。

finalize 签发 validation.json（`alibaba.buyer-due-diligence.validation.v1`），状态 completed/completed_with_limitations。隐私、缺输入、结构、证据错配、PDF 失败为 failed_validation；不能打成 limited。检查覆盖不等于对事实真伪或法律结论的担保。真实业务文件仍只保存在本地，绝不进 Git。
