"""Research, identity, evidence, and deterministic local report contracts."""
from __future__ import annotations

import math
import re
from pathlib import Path
from urllib.parse import urlsplit

from extract_research_view import (DISPLAY, Invalid, digest, encode, exact, now,
    public_url, read_json, require, safe_text, safety_walk, timestamp)

SCHEMA = "alibaba.buyer-due-diligence.research.v1"
DIMENSIONS = "companyFundamentals regionAndMarket procurementPreferences procurementScale decisionChain riskSignals supplyChain communicationAndNegotiation growthAndStrategy".split()
SCORES = "match potential risk profit relationship overall".split()
SCORE_LABELS = dict(zip(SCORES, "匹配度 潜力 风险 利润率 关系 综合".split()))
RISKS = ("legal", "compliance", "payment", "reputation")
RECORD_KEYS = {*DISPLAY, "sourceRecordIndex", "matchStatus", "identificationStrength", "matchReason", "identityEvidence", "priority", "publicSummary", "riskJudgement", "followup", "scores", "dimensions", "sources", "attempts", "riskChecks", "screening"}


def nonempty(value, label):
    safe_text(value, label)
    require(bool(value.strip()), label + ": empty")


def local_file(run, name):
    require(isinstance(name, str) and name and not Path(name).is_absolute() and ".." not in Path(name).parts, "evidence path must stay inside run")
    path = Path(run) / name
    require(path.resolve().is_relative_to(Path(run).resolve()) and not path.is_symlink() and path.is_file(), "missing/unsafe evidence file")
    return path


def indexes(values, count, label, allow_empty=True):
    require(isinstance(values, list) and (values or allow_empty) and all(type(v) is int and 0 <= v < count for v in values) and len(values) == len(set(values)), label + ": invalid references")


def independent(sources, refs):
    # ponytail: conservative hostname + publisher grouping, not a corporate ownership database.
    groups, hosts = set(), []
    for i in refs:
        source = sources[i]
        host = urlsplit(source["url"]).hostname.removeprefix("www.")
        group = source["publisherGroup"].strip().casefold()
        if group not in groups and not any(host == h or host.endswith("."+h) or h.endswith("."+host) for h in hosts):
            groups.add(group)
            hosts.append(host)
    return len(hosts)


def eligible(record):
    return record.get("matchStatus") == "matched" and record.get("identificationStrength") in {"medium", "high"}


def draft(view, input_hash):
    rows = []
    for i, item in enumerate(view["records"]):
        rows.append({"sourceRecordIndex": i, **{k: item[k] for k in DISPLAY},
            "matchStatus": "pending", "identificationStrength": "pending", "matchReason": "", "identityEvidence": [],
            "priority": "P2", "publicSummary": "", "riskJudgement": "", "followup": "", "scores": {},
            "dimensions": {k: {"summary": "", "evidenceLevel": "insufficient", "sourceIndexes": []} for k in DIMENSIONS},
            "sources": [], "attempts": [], "riskChecks": {k: {"status": "pending", "summary": "", "attemptIndexes": [], "sourceIndexes": []} for k in RISKS},
            "screening": {"status": "pending", "reason": "", "termsFile": None, "receipt": None}})
    return {"schemaVersion": SCHEMA, "sourceDate": view["sourceDate"], "inputSha256": input_hash, "createdAt": now(), "records": rows}


def validate_research(data, view, input_hash, run, level=3):
    exact(data, {"schemaVersion", "sourceDate", "inputSha256", "createdAt", "records"}, "research")
    require(data["schemaVersion"] == SCHEMA and data["inputSha256"] == input_hash and data["sourceDate"] == view["sourceDate"], "research input binding mismatch")
    timestamp(data["createdAt"])
    safety_walk(data)
    records = data["records"]
    require(isinstance(records, list) and len(records) == len(view["records"]), "research must cover every customer")
    identities = {r[k] for r in view["records"] for k in ("crmCustomerId", "buyerLoginId")}
    def no_private_id(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k != "inputSha256":
                    no_private_id(v)
        elif isinstance(value, list):
            for v in value:
                no_private_id(v)
        elif isinstance(value, str):
            for identity in identities:
                require(not re.search(r"(?<![\w-])"+re.escape(identity)+r"(?![\w-])", value), "private source identity leaked into research text")
    no_private_id(data)
    limitations, double_count, dependencies = [], 0, {"research.json"}
    for i, (record, original) in enumerate(zip(records, view["records"])):
        label = f"record {i}"
        exact(record, RECORD_KEYS, label)
        require(type(record["sourceRecordIndex"]) is int and record["sourceRecordIndex"] == i, label + ": source order/index mismatch")
        require(all(record[k] == original[k] for k in DISPLAY), label + ": display identity changed")
        sources, attempts = record["sources"], record["attempts"]
        require(isinstance(sources, list) and isinstance(attempts, list) and attempts, label + ": actual research attempts required")
        urls = set()
        for source in sources:
            exact(source, {"url", "title", "publisher", "publisherGroup", "accessedAt", "supports", "attemptIndex"}, "source")
            url = public_url(source["url"])
            require(url not in urls, "duplicate source URL")
            urls.add(url)
            for field in ("title", "publisher", "publisherGroup"):
                nonempty(source[field], "source."+field)
            timestamp(source["accessedAt"])
            require(isinstance(source["supports"], list) and source["supports"] and set(source["supports"]) <= set(DIMENSIONS) | {"identity", *RISKS}, "invalid source supports")
            indexes([source["attemptIndex"]], len(attempts), "source attempt")
            require(attempts[source["attemptIndex"]].get("status") == "succeeded", "public source requires a successful observation")
        for attempt in attempts:
            exact(attempt, {"kind", "language", "query", "checkedAt", "status", "detail", "evidenceFile", "evidenceSha256"}, "attempt")
            require(attempt["kind"] in {"identity", *RISKS} and attempt["status"] in {"succeeded", "limited"}, "attempt not actually completed/limited")
            for key in ("query", "language", "detail"):
                nonempty(attempt[key], "attempt."+key)
            timestamp(attempt["checkedAt"])
            path = local_file(run, attempt["evidenceFile"])
            require(digest(path) == attempt["evidenceSha256"], "research observation hash mismatch")
            require(path.suffix == ".md" and path.stat().st_size <= 256*1024, "evidence must be a bounded sanitized Markdown observation")
            observation = path.read_text(encoding="utf-8")
            nonempty(observation, "research observation")
            dependencies.add(attempt["evidenceFile"])
            if attempt["status"] == "limited":
                limitations.append(f"record {i}: {attempt['kind']}: {attempt['detail']}")
        require({a["kind"] for a in attempts} >= {"identity", *RISKS}, label + ": identity/legal/compliance/payment/reputation attempts required")
        exact(record["riskChecks"], RISKS, "risk checks")
        for kind, check in record["riskChecks"].items():
            exact(check, {"status", "summary", "attemptIndexes", "sourceIndexes"}, "risk check")
            require(check["status"] in {"passed", "limited"}, kind + ": pending risk check")
            nonempty(check["summary"], kind)
            indexes(check["attemptIndexes"], len(attempts), kind, False)
            require(all(attempts[j]["kind"] == kind for j in check["attemptIndexes"]), "risk attempt category mismatch")
            indexes(check["sourceIndexes"], len(sources), kind)
            require(all(kind in sources[j]["supports"] for j in check["sourceIndexes"]), "risk source does not support category")
            if check["status"] == "passed":
                require(any(attempts[j]["status"] == "succeeded" for j in check["attemptIndexes"]) and check["sourceIndexes"], "passed risk check needs successful source-backed attempt")
            else:
                limitations.append(f"record {i}: {kind}: {check['summary']}")
        screening = record["screening"]
        exact(screening, {"status", "reason", "termsFile", "receipt"}, "screening")
        nonempty(screening["reason"], "screening scope and limits")
        require(screening["status"] in {"passed", "limited"}, "screening not performed")
        if screening["status"] == "passed":
            terms_path, receipt_path = local_file(run, screening["termsFile"]), local_file(run, screening["receipt"])
            terms, receipt = read_json(terms_path), read_json(receipt_path)
            require(isinstance(terms, list) and terms and all(isinstance(t, str) and t.strip() for t in terms), "invalid screening terms")
            require(original["companyName"] and original["companyName"].casefold() in {t.casefold() for t in terms}, "screening terms must include source company name")
            safety_walk(terms)
            require(receipt.get("schemaVersion") == "alibaba.uk-screening.v1" and receipt.get("complete") is True and receipt.get("eofReached") is True, "incomplete UK screening is not zero hits")
            require(receipt.get("termsSha256") == digest(terms_path) and receipt.get("rows", 0) >= 1000, "screening terms/row count mismatch")
            from screen_uk_sanctions import URL, MEMORY_BYTES, WALL_SECONDS, MAX_BYTES, normalize
            source, guard = receipt.get("source", {}), receipt.get("guard", {})
            require(source.get("url") == URL and re.fullmatch(r"[a-f0-9]{64}", source.get("sha256", "")) and 0 < source.get("bytes", 0) <= MAX_BYTES, "missing full-list provenance")
            require(guard.get("limitReadbackVerified") is True and guard.get("workerExitCode") == 0 and guard.get("memoryLimitBytes") == MEMORY_BYTES and guard.get("wallLimitSeconds") == WALL_SECONDS, "missing bounded scanner receipt")
            checked = timestamp(receipt.get("checkedAt"))
            require(checked.date().isoformat() >= view["sourceDate"], "screening receipt predates this batch")
            require(receipt.get("termCount") == len({normalize(t) for t in terms}) and receipt.get("matchedMethod") == "NFKC-casefold-whitespace-exact-name", "screening method/term count mismatch")
            require(isinstance(receipt.get("hits"), list) and type(receipt.get("hitCount")) is int and receipt["hitCount"] == len(receipt["hits"]), "invalid hit count")
            if receipt["hitCount"]:
                limitations.append(f"record {i}: UK exact-name candidate hits need manual identity review")
            dependencies.update((screening["termsFile"], screening["receipt"]))
        else:
            require(screening["termsFile"] is None and screening["receipt"] is None, "limited screening must not masquerade as complete evidence")
            require(record["riskChecks"]["compliance"]["status"] == "limited", "incomplete screening requires compliance limitation")
            limitations.append(f"record {i}: screening limited: {screening['reason']}")
        if level < 2:
            continue
        require(record["matchStatus"] in {"matched", "unconfirmed"} and record["identificationStrength"] in {"low", "medium", "high"}, "identity classification missing")
        nonempty(record["matchReason"], "identity reason")
        require(isinstance(record["identityEvidence"], list), "identity evidence must be a list")
        for evidence in record["identityEvidence"]:
            exact(evidence, {"field", "sourceIndexes", "reason"}, "identity evidence")
            require(evidence["field"] in {"companyName", "countryRegion", "officialWebsite", "enterpriseAddress", "contactName"} and original.get(evidence["field"]), "identity anchor not present in input")
            indexes(evidence["sourceIndexes"], len(sources), "identity", False)
            require(all("identity" in sources[j]["supports"] for j in evidence["sourceIndexes"]), "identity source lacks support")
            nonempty(evidence["reason"], "identity anchor reason")
        if record["matchStatus"] == "matched":
            require(eligible(record), "low-strength identities must remain unconfirmed")
            fields = {e["field"] for e in record["identityEvidence"]}
            require("companyName" in fields and len(fields) >= 2, "matched needs company plus a distinct identity anchor")
            if record["identificationStrength"] == "high":
                refs = {j for e in record["identityEvidence"] for j in e["sourceIndexes"]}
                require(independent(sources, refs) >= 2, "high identity needs independent publishers")
        else:
            require(record["scores"] == {}, "unconfirmed scores must be empty")
            limitations.append(f"record {i}: identity unconfirmed")
        if level < 3:
            continue
        require(record["priority"] in {"P0", "P1", "P2"}, "invalid priority")
        for key in ("publicSummary", "riskJudgement", "followup"):
            nonempty(record[key], key)
        exact(record["dimensions"], DIMENSIONS, "nine dimensions")
        for kind, dimension in record["dimensions"].items():
            exact(dimension, {"summary", "evidenceLevel", "sourceIndexes"}, "dimension")
            nonempty(dimension["summary"], kind)
            level_name, refs = dimension["evidenceLevel"], dimension["sourceIndexes"]
            require(level_name in {"verified", "single_source", "inferred", "insufficient"}, "invalid evidence level")
            indexes(refs, len(sources), kind)
            require(all(kind in sources[j]["supports"] for j in refs), "source does not support dimension")
            count = independent(sources, refs)
            double_count += int(count >= 2)
            if level_name == "verified":
                require(count >= 2, "verified dimension needs independent corroboration")
            elif level_name in {"single_source", "inferred"}:
                require(refs, "single-source/inferred claim needs supporting source")
            else:
                limitations.append(f"record {i}: {kind}: insufficient evidence")
        if eligible(record):
            exact(record["scores"], SCORES, "scores")
            for key, score in record["scores"].items():
                exact(score, {"value", "reason", "sourceIndexes"}, "score")
                value = score["value"]
                nonempty(score["reason"], "score reason")
                indexes(score["sourceIndexes"], len(sources), "score")
                require(value is None or (type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 10), "score must be null or finite 0..10")
                if value is not None:
                    require(score["sourceIndexes"], "numerical score needs evidence")
                else:
                    limitations.append(f"record {i}: {key}: not assessed")
    return {"limitations": sorted(set(limitations)), "doubleSourceDimensions": double_count,
            "dimensionDenominator": len(records)*len(DIMENSIONS), "dependencies": sorted(dependencies)}


def md(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\n", "<br>")


def reports(data, view, summary):
    records = data["records"]
    matrix = ["# 全量客户对比", "", "| 行 | 公司 | 联系人 | 业务员 | 国家/地区 | 主体识别 | 优先级 | 综合分 |", "|---|---|---|---|---|---|---|---|"]
    observation = ["# 观察池", "", f"未确认客户：{sum(not eligible(r) for r in records)}", ""]
    report = ["# Alibaba 每日客户背调", "", f"来源日期：{data['sourceDate']}；客户：{len(records)}；研究覆盖：{len(records)}/{len(view['records'])}。",
        f"九维双来源覆盖：{summary['doubleSourceDimensions']}/{summary['dimensionDenominator']}（分母为全体客户 × 9）。", "",
        "主体识别强度不等于采购匹配度。风险分越高风险越高；未评估不等于零。账号注册年份不等于公司成立年份。站内行为仅为内部信号。",
        "英国名单精确名称筛查不涵盖所有制裁、别名、所有权或司法辖区；零命中不构成合规放行。", ""]
    for i, r in enumerate(records):
        overall = r["scores"].get("overall", {}).get("value")
        matrix.append("| " + " | ".join(md(v) for v in [i, *[r[k] for k in DISPLAY], r["matchStatus"]+"/"+r["identificationStrength"], r["priority"], overall if overall is not None else "未评估"]) + " |")
        if not eligible(r):
            observation += [f"## {i}. {md(r['companyName']) or '主体未知'}", "", md(r["matchReason"]), "", "候选信息未归因："+md(r["publicSummary"]), "", md(r["followup"]), ""]
        report += [f"## {i}. {md(r['companyName']) or '主体未知'}", "", f"联系人：{md(r['contactName'])}；负责业务员：{md(r['salesperson'])}；国家/地区：{md(r['countryRegion'])}。",
                   f"识别：{r['matchStatus']}/{r['identificationStrength']}；优先级：{r['priority']}。", md(r["matchReason"]), "", md(r["publicSummary"]), "", md(r["riskJudgement"]), "", md(r["followup"]), ""]
        for kind, d in r["dimensions"].items():
            report += [f"- {kind} [{d['evidenceLevel']}]：{md(d['summary'])}；引用 {d['sourceIndexes']}"]
        report += [""]
        for kind, s in r["scores"].items():
            report += [f"- {SCORE_LABELS[kind]}：{s['value'] if s['value'] is not None else '未评估'}；{md(s['reason'])}；引用 {s['sourceIndexes']}"]
        report += ["", "风险覆盖：", ""]
        report += [f"- {k} [{v['status']}]：{md(v['summary'])}" for k, v in r["riskChecks"].items()]
        report += ["", "公开来源：", ""]
        report += [f"- [{j}] [{md(s['title'])}]({s['url']})；{md(s['publisher'])}；访问 {s['accessedAt']}" for j, s in enumerate(r["sources"])]
        report += [""]
    report += ["## 已记录的限制", "", *["- "+md(s) for s in summary["limitations"]], ""]
    return {"report.md": "\n".join(report), "matrix.md": "\n".join(matrix)+"\n", "observation-pool.md": "\n".join(observation)+"\n"}


def matched_content(data, view):
    customers = []
    for r, original in zip(data["records"], view["records"]):
        if not eligible(r):
            continue
        c = {"match": "matched", "identificationStrength": r["identificationStrength"], "公司名": r["companyName"], "买家名": r["contactName"], "负责业务员": r["salesperson"], "国家或地区": r["countryRegion"], "经营类型": original["businessType"],
             "识别强度": {"high": "高", "medium": "中"}[r["identificationStrength"]], "公开信息摘要": r["publicSummary"], "风险判断": r["riskJudgement"], "跟进建议": r["followup"],
             "公开来源": [s["url"] for s in r["sources"]], "优先级原值": r["priority"], "跟进级别": r["priority"], "综合评级": "未评估" if r["scores"]["overall"]["value"] is None else str(r["scores"]["overall"]["value"]),
             "风险短语": "未评估" if r["scores"]["risk"]["value"] is None else "风险 "+str(r["scores"]["risk"]["value"]),
             "站内行为信号": "账号注册："+str(original["registerYear"] or "未知")+"；90天登录："+str(original["activityLoginDays90d"] if original["activityLoginDays90d"] is not None else "未知")+"（内部信号）"}
        c.update({SCORE_LABELS[k]: r["scores"][k]["value"] for k in SCORES})
        customers.append(c)
    high_risk = any(c["风险"] is not None and c["风险"] >= 6.5 for c in customers)
    return {"报告名称": "客户背调初筛报告", "报告日期": data["sourceDate"], "数据区间": data["sourceDate"], "摘要": {"主要风险提示": "高风险 / 详见风险说明" if high_risk else "核验范围有限 / 详见风险说明"}, "客户": customers}
