"""Private v3 assembly adapted from the user-owned Alibaba collector.
Projection and workbook checks reuse extract_research_view; no upload code.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

SCHEMA_VERSION = "nanyue.due-diligence-input.v3"
LOCAL_TZ = timezone(timedelta(hours=8))
PROFILE_LINK_KEYS = {"buyerprofilelink", "profileurl"}
FORBIDDEN_PRIVATE_SOURCE_KEYS = {
    "authorization",
    "cookie",
    "cookies",
    "credentials",
    "headers",
    "html",
    "password",
    "proxyauthorization",
    "rawhtml",
    "requestheaders",
    "secret",
    "session",
    "sessionid",
    "setcookie",
    "tbtoken",
    "token",
}
SENSITIVE_QUERY_KEYS = {
    "accesstoken",
    "accesskey",
    "apikey",
    "authorization",
    "auth",
    "cookie",
    "credential",
    "password",
    "refreshtoken",
    "secretkey",
    "session",
    "sessionid",
    "signature",
    "sig",
    "token",
}

def read_jsonl(path):
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in text.splitlines() if line.strip()] if text else []


def read_enriched_list_payload(path):
    root = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(root, dict) or not str(root.get("schemaVersion") or "").startswith("alicrm.customer-list.enriched."):
        raise ValueError(f"list input is not an enriched customer-list JSON: {path}")
    rows = root.get("records")
    if not isinstance(rows, list):
        raise ValueError(f"list input must contain records[]: {path}")
    return root


def read_enriched_list(path):
    return read_enriched_list_payload(path)["records"]


def identity(value):
    return str(value).strip() if value is not None else ""


def normalize_login_id(value):
    result = identity(value)
    return result[:-1].strip() if result.endswith(",") else result


def normalized_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def extract_source_date(list_payload):
    explicit = identity(list_payload.get("sourceDate"))
    source = list_payload.get("source") or {}
    request_body = source.get("requestBody") or {}
    raw_filters = request_body.get("jsonArray")
    try:
        filters = json.loads(raw_filters) if isinstance(raw_filters, str) else raw_filters
    except json.JSONDecodeError as exc:
        raise ValueError("enriched list source.requestBody.jsonArray is not valid JSON") from exc
    if not isinstance(filters, list):
        raise ValueError("enriched list source.requestBody.jsonArray must be an array")

    ranges = []
    for item in filters:
        if not isinstance(item, dict):
            continue
        start = item.get("gmt_create_start")
        end = item.get("gmt_create_end")
        if start in (None, "") or end in (None, ""):
            continue
        try:
            start_value = int(start)
            end_value = int(end)
        except (TypeError, ValueError) as exc:
            raise ValueError("CRM source date bounds must be Unix seconds") from exc
        ranges.append((start_value, end_value))
    if len(ranges) != 1:
        raise ValueError(f"expected exactly one CRM source date range, found {len(ranges)}")

    start_value, end_value = ranges[0]
    if end_value - start_value != 24 * 60 * 60:
        raise ValueError("CRM source date range must cover exactly one day")
    start_time = datetime.fromtimestamp(start_value, LOCAL_TZ)
    end_time = datetime.fromtimestamp(end_value, LOCAL_TZ)
    if start_time.time().isoformat() != "00:00:00" or end_time.time().isoformat() != "00:00:00":
        raise ValueError("CRM source date range must use local midnight bounds")
    derived = start_time.strftime("%Y-%m-%d")
    if explicit and explicit != derived:
        raise ValueError(f"enriched list sourceDate mismatch: {explicit!r} != {derived!r}")
    return derived


def query_free_url(value):
    raw = identity(value)
    if not raw:
        return raw
    candidate = raw if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I) else f"https://{raw}"
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return raw.split("?", 1)[0].split("#", 1)[0]
    if not parts.hostname:
        return raw.split("?", 1)[0].split("#", 1)[0]
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc, parts.path, "", ""))


def has_sensitive_url_material(value):
    if not isinstance(value, str) or not value.lower().startswith(("http://", "https://")):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return True
    for key, _ in parse_qsl(parts.query, keep_blank_values=True):
        normalized = normalized_key(key)
        if normalized in SENSITIVE_QUERY_KEYS or any(
            marker in normalized for marker in ("token", "credential", "signature", "session")
        ):
            return True
    return bool(parts.fragment and any(marker in parts.fragment.lower() for marker in ("token", "session", "signature")))


def sanitize_private_source(value, key=None):
    if isinstance(value, dict):
        result = {}
        for item_key, item in value.items():
            normalized = normalized_key(item_key)
            if normalized in FORBIDDEN_PRIVATE_SOURCE_KEYS:
                continue
            result[item_key] = sanitize_private_source(item, item_key)
        return result
    if isinstance(value, list):
        return [sanitize_private_source(item, key) for item in value]
    if isinstance(value, str):
        normalized = normalized_key(key) if key is not None else ""
        if normalized in PROFILE_LINK_KEYS:
            if any(marker in value.lower() for marker in ("<html", "<script", "<!doctype")):
                raise ValueError(f"{key} contains raw HTML")
            return value
        if has_sensitive_url_material(value):
            return query_free_url(value)
    return value


def index_by_customer_id(rows, label):
    result = {}
    for row_no, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"{label} row {row_no} must be an object")
        customer_id = identity(row.get("customerId"))
        if not customer_id:
            raise ValueError(f"{label} row {row_no} is missing customerId")
        if customer_id in result:
            raise ValueError(f"{label} contains duplicate customerId: {customer_id}")
        result[customer_id] = row
    return result


def list_identity_map(list_rows):
    indexed = index_by_customer_id(list_rows, "enriched list")
    result = {}
    seen_login_ids = set()
    normalization_applied_ids = set()
    for customer_id, row in indexed.items():
        customer = row.get("customer")
        if not isinstance(customer, dict):
            raise ValueError(f"enriched list customer {customer_id} is missing customer object")
        nested_customer_id = identity(customer.get("customerId"))
        if nested_customer_id and nested_customer_id != customer_id:
            raise ValueError(f"enriched list customerId mismatch: {customer_id} != {nested_customer_id}")
        raw_login_id = identity(customer.get("loginId"))
        login_id = normalize_login_id(customer.get("loginId"))
        if not login_id:
            raise ValueError(f"enriched list customer {customer_id} is missing customer.loginId")
        if login_id != raw_login_id:
            normalization_applied_ids.add(customer_id)
        if login_id in seen_login_ids:
            raise ValueError(f"enriched list contains duplicate customer.loginId: {login_id}")
        seen_login_ids.add(login_id)
        result[customer_id] = login_id
    return indexed, result, normalization_applied_ids


def build_payload(list_file, detail_file, profile_file, batch_size):
    list_payload = read_enriched_list_payload(list_file)
    list_rows = list_payload["records"]
    detail_rows = read_jsonl(detail_file)
    profile_rows = read_jsonl(profile_file) if profile_file else []
    list_records, buyer_login_ids, normalization_applied_ids = list_identity_map(list_rows)
    details = index_by_customer_id(detail_rows, "detail input")
    profiles = index_by_customer_id(profile_rows, "buyer profile input")

    list_ids = set(list_records)
    detail_ids = set(details)
    if detail_ids != list_ids:
        raise ValueError(
            f"detail/list customerId set mismatch: missing={len(list_ids - detail_ids)}, "
            f"extra={len(detail_ids - list_ids)}"
        )
    if not set(profiles).issubset(list_ids):
        raise ValueError(f"buyer profile input has {len(set(profiles) - list_ids)} customerId(s) absent from enriched list")

    profile_member_id_missing_count = 0
    profile_member_id_cross_checked_count = 0
    for customer_id, profile in profiles.items():
        buyer = profile.get("buyerProfile") or {}
        raw_member_id = identity(buyer.get("memberId")) if isinstance(buyer, dict) else ""
        member_id = normalize_login_id(buyer.get("memberId")) if isinstance(buyer, dict) else ""
        if not member_id:
            profile_member_id_missing_count += 1
            continue
        if member_id != raw_member_id:
            normalization_applied_ids.add(customer_id)
        if member_id != buyer_login_ids[customer_id]:
            raise ValueError(f"buyer identity mismatch for customerId {customer_id}: list loginId != profile memberId")
        profile_member_id_cross_checked_count += 1

    records = []
    for customer_id in list_records:
        detail = details[customer_id]
        records.append(
            {
                "crmCustomerId": customer_id,
                "buyerLoginId": buyer_login_ids[customer_id],
                "buyerList": sanitize_private_source(list_records[customer_id]),
                "customerDetail": sanitize_private_source(detail),
                "buyerHomepageStatus": (
                    "collected" if customer_id in profiles else "not_collected_no_profile_link"
                ),
                "buyerHomepage": (
                    sanitize_private_source(profiles[customer_id])
                    if customer_id in profiles
                    else None
                ),
            }
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceDate": extract_source_date(list_payload),
        "readerInstructions": [
            "这是完整私有买家来源文件；buyerList、customerDetail、buyerHomepage保留同批次已清洗采集数据。",
            "背调必须先通过脚本生成临时精简研究视图，不得人工复制或删减本文件。",
            "crmCustomerId与buyerLoginId仅用于本地精确身份核对，不上传。",
            "买家主页定位字段在本完整私有文件中原样保留；不得复制到背调上传结果、公开来源或PDF。Cookie、请求头和原始HTML不进入本文件。",
        ],
        "source": {
            "listFile": Path(list_file).name,
            "detailFile": Path(detail_file).name,
            "profileFile": Path(profile_file).name if profile_file else None,
            "listRows": len(list_rows),
            "detailRows": len(detail_rows),
            "profileRows": len(profile_rows),
            "profileMemberIdCrossCheckedCount": profile_member_id_cross_checked_count,
            "profileMemberIdMissingCount": profile_member_id_missing_count,
            "normalizationAppliedCount": len(normalization_applied_ids),
            "credentialSanitization": "raw credentials, request transport, and raw HTML removed; buyer profile URLs preserved exactly",
        },
        "records": records,
    }
