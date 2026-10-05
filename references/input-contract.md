# 输入契约

只接受 `nanyue.due-diligence-input.v3` 与同次导出的 XLSX，不支持临时 CSV/通用 CRM 适配。只读 `records[]` 当前日期，不混入历史。

JSON 的 `generatedAt` 必须带时区，`sourceDate` 是真实 YYYY-MM-DD 日期。记录精确字段：`crmCustomerId`、`buyerLoginId`、`buyerList`、`customerDetail`、`buyerHomepageStatus`、`buyerHomepage`。根身份唯一且非空，login 不带尾逗号；存在的嵌套 customerId/loginId/memberId 必须一致。嵌套 login 兼容旧格式的首尾空白和一个英文尾逗号，根身份不再做自动修复。

主页状态仅允许 `collected + object` 或 `not_collected_no_profile_link + null`；缺主页不丢客户。传输凭据/raw HTML 拒绝；源文件中的私有主页定位值只允许留在源内，不投射到研究视图。若公开字段夹带邮箱、电话、私有 URL 或凭据，校验停止，由上游产生合格输入，不能私改源文件。

## 固定 26 列

默认每批 10 条，以 XLSX `生成说明.batchSize` 为准（正整数）。主表 `背调输入` 和每个 `批次01` 等表都核对；额外工作表、公式、缺行、多行、换序或字段差异拒绝。元数据 schema 为旧兼容值 `nanyue.due-diligence-research-view.v1`，日期及批次数一致。

| 字段 | Excel 列 | 来源/含义 |
|---|---|---|
| batchNo | 批次 | 顺序号按 batchSize 分批 |
| rowNo | 序号 | 从 1 开始的来源次序 |
| crmCustomerId | CRM客户ID | 根身份，仅私有核对 |
| buyerLoginId | 买家登录ID | 根身份，仅私有核对 |
| companyName | 公司名称 | detail.companyName → homepage.companyName → enterprise.companyName |
| contactName | 联系人 | 首位 contacts.contactName → buyerProfile.displayName |
| salesperson | 负责业务员 | fieldsMissingFromList.ownerName；非客户本人 |
| countryRegion | 国家/地区 | 注册地区 → 联系地址国家 → 企业地区 → detail 地址国家 |
| registerYear | Alibaba账号注册年份 | buyerProfile.registerYear；不是公司成立年 |
| officialWebsite | 官网 | enterprise.officialWebsite → detail website |
| miniSiteUrl | 阿里MiniSite | enterprise.miniSiteUrl；仅公共线索 |
| enterpriseAddress | 企业地址 | enterprise.address.addressText → detail addressText |
| businessType | 经营类型 | businessTypeText → businessTypes |
| position | 职位 | enterprise.position → 首位 contact.position |
| industryPreference | 行业偏好 | purchasePreference.industryPreference |
| activityLoginDays90d | 90天登录天数 | activity90d.loginDays |
| activityProductViews90d | 90天商品浏览量 | activity90d.productViewCount |
| activitySearches90d | 90天搜索次数 | activity90d.searchCount |
| validInquiryCount90d | 90天有效询盘 | activity90d.validInquiryCount |
| repliedInquiryCount90d | 90天已回复询盘 | activity90d.repliedInquiryCount |
| validRfqCount90d | 90天RFQ | activity90d.validRfqCount |
| quotationReceivedCount90d | 90天收到报价 | activity90d.quotationReceivedCount |
| quotationReadCount90d | 90天已读报价 | activity90d.quotationReadCount |
| onlineTradeOrderCount | 在线交易数量 | onlineTrade.totalOrderCount |
| onlineTradeVolumeUsd | 在线交易金额USD | onlineTrade.totalOrderVolumeUsd |
| searchHints | 检索线索 | 公司、国家、官网、MiniSite、经营类型非空项用 `; ` 连接 |

数值保持数值；`None` 与 Excel 空单元格对应空串；布尔投射为「是/否」；列表非空项以 `, ` 连接；字典顺序取 label/name/displayName/displayText/other/id/code/mcmsKey，再退回紧凑 JSON；字符串按旧导出规则 trim。XLSX 比对阶段不再随意 trim、转数值或大小写折叠。

经营类型字典：2000/2006 线上零售商，2001 工厂，2002 贸易公司，2003 批发/分销商，2004 线下零售商，2005 买办/采购代理，2007 散客，2008 其他。`businessType@manufacturerOrFactory/tradingCompany/distributorWholesaler/retailer/buyerOffice/onlineShop/soho/other` 同义映射；未知标签原样保留，不猜行业。

站内活跃、询盘、报价、交易信号是内部信息；不能计入公开双来源证明。

## 可选上游凭据

显式提供 `--phase-status` 时，核对 currentPhase/lastCheckedPhase=`final_validated`、lastStatus/status=`complete`、errors 为空；从 `phases.final_validated.summary` 核对 sourceDate、nanyueRecords、nanyueJson 和 nanyueWorkbook。没有该文件不阻塞独立使用，但最终记录 `not_provided`，仍完整校验 JSON/XLSX。

输入 hash 在 init 后锁定；若源文件或规则变化，新建 run，不回写源。临时 `research-view.tmp.json` 保留 26 字段以便一致性复核；它是私有本地文件。公共报告使用 `(inputSha256, sourceRecordIndex)`，索引从 0 开始，不能用作 CRM ID。
