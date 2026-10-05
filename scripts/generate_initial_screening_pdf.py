#!/usr/bin/env python
"""Generate the matched-customer screening PDF from the fixed HTML template."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from babel import Locale


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = SKILL_DIR / "assets" / "customer_screening_report_template.html"
BAD_TEXT = ["[REPLACE_ME]", "买家 A", "买家 B"]
ZH_COUNTRY_NAMES = Locale.parse("zh_Hans").territories
COUNTRY_CODE_ALIASES = {"UK": "GB"}
METRIC_CLASSES = {
    "匹配度": "metric-match",
    "潜力": "metric-potential",
    "风险": "metric-risk",
    "利润率": "metric-profit",
    "关系": "metric-relationship",
    "综合": "metric-overall",
}


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def two_digits(value: object) -> str:
    return f"{int(value):02d}"


def shorten(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def risk_label(customer: dict) -> str:
    return (
        customer.get("风险短语")
        or str(customer.get("风险判断") or "").split("：", 1)[0][:4]
        or "关注"
    )


def company_name(customer: dict) -> object:
    return customer.get("公司名") or customer.get("companyName") or customer.get("买家名") or "—"


def buyer_name(customer: dict) -> object:
    return customer.get("买家名") or customer.get("contactName") or "—"


def country_name_zh(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    if not re.fullmatch(r"[A-Za-z]{2}", raw):
        return raw
    code = COUNTRY_CODE_ALIASES.get(raw.upper(), raw.upper())
    name = ZH_COUNTRY_NAMES.get(code)
    if not name:
        raise ValueError(f"Unknown ISO 3166-1 alpha-2 country/region code: {raw}")
    return str(name)


def score_parts(value: object) -> tuple[str, str]:
    if value is None:
        return "未评估", "0"
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 10:
        raise ValueError("Invalid score; expected null or finite 0..10")
    score = float(value)
    return f"{score:.1f}", f"{max(0, min(10, score)) / 10:.2f}"


def score_card_class(label: str, value: object) -> str:
    metric = METRIC_CLASSES[label]
    if label != "风险":
        return metric
    try:
        risk = float(value)
    except (TypeError, ValueError):
        return metric
    state = "risk-safe" if risk <= 3.9 else "risk-watch" if risk <= 6.5 else "risk-alert"
    return f"{metric} {state}"


def source_host(url: object) -> str:
    raw = str(url or "").strip()
    if not raw:
        return "—"
    host = (urlparse(raw).netloc or raw.split("/")[0]).replace("www.", "")
    return shorten(host, 24)


def split_sentences(text: object, limit: int = 3, width: int = 48) -> list[str]:
    parts = [
        item.strip(" 。；;")
        for item in re.split(r"[。；;\n]+", str(text or ""))
        if item.strip(" 。；;")
    ]
    return [shorten(item, width) for item in parts[:limit]]


def bullet_list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{esc(item)}</li>" for item in items[:3]) + "</ul>"


def filtered_customers(raw_customers: list[dict]) -> list[dict]:
    for customer in raw_customers:
        if customer.get("match") != "matched" or customer.get("identificationStrength") not in {"medium", "high"} or not customer.get("公开来源"):
            raise ValueError("PDF requires validated medium/high matched identities with sources")
        for key in METRIC_CLASSES:
            if key not in customer:
                raise ValueError("PDF score key missing")
            score_parts(customer[key])
    return raw_customers


def computed_summary(data: dict, customers: list[dict]) -> dict:
    summary = data.get("摘要") or {}
    priority = sum(
        1
        for customer in customers
        if customer.get("优先级原值") in {"P0", "P1"}
        or customer.get("跟进级别") in {"立即深耕", "重点跟进", "重点"}
    )
    return {
        "纳入客户数": len(customers),
        "高识别强度客户数": sum(1 for customer in customers if customer.get("识别强度") == "高"),
        "优先跟进客户数": priority,
        "主要风险提示": summary.get("主要风险提示") or "信息不足 / 主体需核验",
    }


def matrix_rows(customers: list[dict]) -> str:
    rows: list[str] = []
    for index, customer in enumerate(customers[:7], 1):
        values = [
            index,
            shorten(company_name(customer), 26),
            shorten(country_name_zh(customer.get("国家或地区")), 12),
            shorten(customer.get("经营类型"), 14),
            customer.get("识别强度"),
            risk_label(customer),
            customer.get("跟进级别"),
            customer.get("综合评级"),
        ]
        rows.append("<tr>" + "".join(f"<td>{esc(value)}</td>" for value in values) + "</tr>")
    return "".join(rows)


def score_description_html() -> str:
    return "".join(
        [
            '<div class="desc-card metric-match"><h3>匹配度</h3><span class="icon-badge"><svg class="icon"><use href="#icon-link" /></svg></span><p>采购需求与卖方产品的适配度，不等于主体识别强度。</p></div>',
            '<div class="desc-card metric-potential"><h3>潜力</h3><span class="icon-badge"><svg class="icon"><use href="#icon-bars" /></svg></span><p>评估市场潜力与增长空间的综合表现。</p></div>',
            '<div class="desc-card metric-risk risk-watch"><h3>风险</h3><span class="icon-badge"><svg class="icon"><use href="#icon-shield-outline" /></svg></span><p>分数越高风险越高。未评估不等于零风险。</p></div>',
            '<div class="desc-card metric-profit"><h3>利润率</h3><span class="icon-badge"><svg class="icon"><use href="#icon-coins" /></svg></span><p>评估交易盈利能力与利润空间水平。</p></div>',
            '<div class="desc-card metric-relationship"><h3>关系</h3><span class="icon-badge"><svg class="icon"><use href="#icon-handshake" /></svg></span><p>评估历史合作与关系黏性的综合表现。</p></div>',
        ]
    )


def first_page(data: dict, customers: list[dict], summary: dict) -> str:
    risk_text = esc(summary["主要风险提示"]).replace(" / ", "<br />")
    return f"""
<section class="page page-1">
  <header class="header"><h1>{esc(data.get("报告名称") or "客户背调初筛报告")}</h1><div class="subtitle">已匹配客户专项报告</div></header>
  <div class="green-rule"></div>
  <div class="info-box">
    <div class="left">
      <div class="info-row"><span class="label">报告日期：</span><span>{esc(data.get("报告日期"))}</span></div>
      <div class="info-row"><span class="label">纳入客户数量：</span><span>{two_digits(summary["纳入客户数"])}</span></div>
    </div>
    <div class="right">
      <div class="info-row"><span class="label">数据区间：</span><span>{esc(data.get("数据区间"))}</span></div>
      <div class="info-row"><span class="label">纳入规则：</span><span>仅展示公开来源已匹配客户，未确认客户不进入报告</span></div>
    </div>
  </div>
  <section class="section"><h2 class="section-title">执行摘要</h2><div class="summary-grid">
    <div class="summary-card summary-customers"><div class="card-title">纳入客户数</div><div class="card-content"><span class="icon-badge"><svg class="icon"><use href="#icon-users" /></svg></span><span class="number">{two_digits(summary["纳入客户数"])}</span></div></div>
    <div class="summary-card summary-confidence"><div class="card-title">高识别强度客户数</div><div class="card-content"><span class="icon-badge"><svg class="icon"><use href="#icon-shield-check" /></svg></span><span class="number">{two_digits(summary["高识别强度客户数"])}</span></div></div>
    <div class="summary-card summary-followup"><div class="card-title">重点跟进客户数</div><div class="card-content"><span class="icon-badge"><svg class="icon"><use href="#icon-target" /></svg></span><span class="number">{two_digits(summary["优先跟进客户数"])}</span></div></div>
    <div class="summary-card risk-card summary-risk"><div class="card-title">主要风险提示</div><div class="card-content"><span class="icon-badge"><svg class="icon"><use href="#icon-alert" /></svg></span><span class="risk-text">{risk_text}</span></div></div>
  </div></section>
  <section class="section matrix-section"><h2 class="section-title">批量客户矩阵</h2><table class="matrix"><colgroup><col/><col/><col/><col/><col/><col/><col/><col/></colgroup><thead><tr><th>序号</th><th>公司名</th><th>国家或地区</th><th>经营类型</th><th>识别强度</th><th>风险判断</th><th>跟进级别</th><th>综合评级</th></tr></thead><tbody>{matrix_rows(customers)}</tbody></table></section>
  <p class="scope-note">首页列示前 {min(7, len(customers))} 位，共 {len(customers)} 位；长名略写，完整名称及全部详情见后页，完整矩阵见 matrix.md。</p>
  <section class="section desc-section"><h2 class="section-title">五项评分说明</h2><div class="score-desc">{score_description_html()}</div></section>
    <div class="page-number">1</div>
</section>"""


def detail_page(customer: dict, page_no: int) -> str:
    company = company_name(customer)
    buyer = buyer_name(customer)
    salesperson = customer.get("负责业务员") or customer.get("salesperson") or "—"
    cards: list[str] = []
    for label in ["匹配度", "潜力", "风险", "利润率", "关系", "综合"]:
        text, fraction = score_parts(customer.get(label))
        emphasis = " emphasis" if label == "综合" else ""
        cards.append(
            f'<div class="score-card {score_card_class(label, customer.get(label))}{emphasis}"><h3>{label}</h3><div class="ring" style="--score: {fraction}"><span>{text}</span></div></div>'
        )

    sources = [source_host(item) for item in (customer.get("公开来源") or [])[:3]] + ["—"] * 3
    actions = split_sentences(customer.get("跟进建议")) or ["主动联系并确认采购需求"]
    notes = split_sentences(customer.get("风险判断")) or ["公开信息需二次核验"]
    needs = ["公司注册名与采购角色", "目标设备型号与预算", "资质、付款与交付要求"]

    return f"""
<section class="page page-2">
  <div class="green-rule tight"></div>
  <section class="section"><h2 class="section-title">客户识别信息</h2><table class="data-table client-table"><colgroup><col/><col/><col/><col/></colgroup><tbody>
    <tr><td class="field">公司名</td><td>{esc(company)}</td><td class="field">买家名</td><td>{esc(buyer)}</td></tr>
    <tr><td class="field">负责业务员</td><td>{esc(shorten(salesperson, 32))}</td><td class="field">识别强度</td><td>{esc(customer.get("识别强度"))}</td></tr>
    <tr><td class="field">国家或地区</td><td>{esc(country_name_zh(customer.get("国家或地区")))}</td><td class="field">经营类型</td><td>{esc(customer.get("经营类型"))}</td></tr>
    <tr><td class="field">阿里巴巴站内行为信号</td><td>{esc(customer.get("站内行为信号") or "—")}</td><td class="field">主要风险提示</td><td>{esc(risk_label(customer))}</td></tr>
  </tbody></table></section>
  <section class="section"><h2 class="section-title">初筛判断</h2><table class="data-table judgement-table"><colgroup><col/><col/></colgroup><tbody>
    <tr><td class="field">公开信息摘要</td><td>{esc(shorten(customer.get("公开信息摘要"), 118))}</td></tr>
    <tr><td class="field">风险判断</td><td>{esc(shorten(customer.get("风险判断"), 84))}</td></tr>
    <tr><td class="field">跟进建议</td><td>{esc(shorten(customer.get("跟进建议"), 92))}</td></tr>
  </tbody></table></section>
  <section class="section score-section"><h2 class="section-title">评分概览</h2><div class="score-grid">{"".join(cards)}</div></section>
  <section class="section source-section"><h2 class="section-title">公开来源</h2><div class="source-box">{"".join(f"<div><b></b>{esc(item)}</div>" for item in sources[:3])}</div></section>
  <section class="section"><h2 class="section-title">销售跟进建议</h2><table class="advice-table"><thead><tr><th>优先动作</th><th>需要补充确认的信息</th><th>注意事项</th></tr></thead><tbody><tr><td>{bullet_list(actions)}</td><td>{bullet_list(needs)}</td><td>{bullet_list(notes)}</td></tr></tbody></table></section>
  <div class="page-number">{page_no}</div>
</section>"""


def css_overrides() -> str:
    return """
    /* Real-data typography. Layout constraints live in the template. */
    .data-table td,.source-box,.advice-table{font-family:"Microsoft YaHei","SimSun",sans-serif}
    .scope-note{font-family:"Microsoft YaHei","SimSun",sans-serif;font-size:11px;line-height:1.3;margin:4px 0 12px}
    .page-1 .header h1{font-size:34px}
    .page-1 .green-rule{margin:24px 0 20px}
    .page-1 .info-box{margin-bottom:20px;padding:10px 20px}
    .page-1 .section-title{margin-bottom:18px}
    .page-1 .summary-grid{margin-bottom:20px}
    .page-1 .summary-card{min-height:90px;padding:12px 14px}
    .page-1 .summary-card .card-title{min-height:24px;font-size:12px}
    .page-1 .summary-card .card-content{min-height:40px}
    .page-1 .summary-card .number{font-size:28px}
    .page-1 .matrix{font-size:11px}
    .page-1 .matrix td{padding:5px 4px;line-height:1.25}
"""


def build_html(template_path: Path, data: dict) -> str:
    customers = filtered_customers(data.get("客户") or [])
    if not customers:
        raise ValueError("No matched customers found in input JSON.")
    summary = computed_summary(data, customers)
    main = (
        '<main class="report">'
        + first_page(data, customers, summary)
        + "".join(detail_page(customer, index + 2) for index, customer in enumerate(customers))
        + "</main>"
    )
    template = template_path.read_text(encoding="utf-8")
    template = template.replace("</style>", css_overrides() + "\n  </style>")
    return re.sub(r'<main class="report">.*?</main>', lambda _: main, template, flags=re.S)


def find_browser(explicit: str | None = None) -> Path:
    candidates = [
        explicit,
        os.environ.get("CHROME_PATH"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    raise FileNotFoundError("Chrome/Edge not found. Pass --chrome or set CHROME_PATH.")


def render_pdf(html_text: str, out_path: Path, browser: Path, content_hash: str) -> None:
    from pypdf import PdfReader, PdfWriter
    work_dir = Path(tempfile.mkdtemp(prefix="alibaba_screening_pdf_"))
    try:
        html_path = work_dir / "report.html"
        pdf_path = work_dir / "report.pdf"
        profile = work_dir / "profile"
        html_path.write_text(html_text, encoding="utf-8")
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
            f"--user-data-dir={profile}",
            "--allow-file-access-from-files",
            "--print-to-pdf-no-header",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ]
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        writer = PdfWriter()
        for page in PdfReader(pdf_path).pages:
            writer.add_page(page)
        writer.add_metadata({"/Title": "客户背调初筛报告", "/Producer": "Alibaba Buyer Due Diligence", "/ResearchContentSHA256": content_hash})
        fd, temporary = tempfile.mkstemp(prefix=".pdf-", dir=out_path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                writer.write(stream)
            os.replace(temporary, out_path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def validate_pdf(path: Path, expected_pages: int, content: dict, content_hash: str) -> None:
    from pypdf import PdfReader
    from extract_research_view import require, safe_text
    reader = PdfReader(str(path))
    if len(reader.pages) != expected_pages:
        raise ValueError(f"Unexpected page count: {len(reader.pages)} != {expected_pages}")
    require(reader.metadata.get("/ResearchContentSHA256") == content_hash, "PDF content binding missing")
    for page in reader.pages:
        require(abs(float(page.mediabox.width)-576) < 1 and abs(float(page.mediabox.height)-768) < 1, "PDF print size must be 768x1024 CSS pixels")
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    for bad in BAD_TEXT:
        if bad in text:
            raise ValueError(f"Unexpected template/demo text found: {bad}")
    if any(char in text for char in ["■", "□", "�"]):
        raise ValueError("Possible mojibake/square glyph found in PDF text.")
    safe_text(text, "PDF text")
    for key, value in reader.metadata.items():
        if key != "/ResearchContentSHA256":
            safe_text(str(value), "PDF metadata", phone=False)
    compact = lambda value: re.sub(r"\s+", "", value)
    for i, customer in enumerate(content["客户"], 1):
        page_text = compact(reader.pages[i].extract_text() or "")
        for key in ("公司名", "买家名", "负责业务员"):
            require(compact(str(customer.get(key) or "—")) in page_text, "PDF detail identity missing or clipped")
        require("销售跟进建议" in page_text and str(i+1) in page_text, "PDF detail/footer missing")
    require("完整矩阵见matrix.md" in compact(reader.pages[0].extract_text() or ""), "PDF summary scope notice missing")
    first_text = compact(reader.pages[0].extract_text() or "")
    require("五项评分说明" in first_text and "未评估不等于零风险" in first_text and "采购需求与卖方产品" in first_text, "PDF summary score explanation clipped")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path, help="Validated workflow run. No standalone bypass.")
    parser.add_argument("--chrome", type=str, help="Chrome/Edge executable path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    from workflow import advance, inspect, load_state, research_offset
    from extract_research_view import require, encode
    state = load_state(args.run)
    index, _ = inspect(args.run, state)
    require(index-research_offset(state) == 5, "PDF entry requires all analysis prerequisites; use workflow.py next")
    print(encode(advance(args.run, browser=args.chrome)))


if __name__ == "__main__":
    main()
