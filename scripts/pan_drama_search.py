#!/usr/bin/env python3
"""Pan drama subtitle search — find US TV shows with subtitles across cloud drives."""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

MAX_WORKERS = 8
HEALTH_CACHE = Path.home() / ".cache" / "pan-drama-search" / "health.json"
HEALTH_TTL = 86400

PANSOU_BASE = os.environ.get("PANSOU_BASE", "https://s.panhunt.com/api").rstrip("/")
PANSOU_CHECK_BASE = os.environ.get("PANSOU_CHECK_BASE", "").rstrip("/")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

SUBTITLE_HINTS = (
    "字幕", "中英", "双语", "内封", "内嵌", "外挂", "srt", "ass", "ssa", "sub", "subtitle", "chs", "cht"
)
NEGATIVE_HINTS = ("无字幕", "no sub", "nosub", "无中字", "生肉", "raw")
VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".ts", ".m2ts")
SUB_EXTS = (".srt", ".ass", ".ssa", ".sub", ".sup")

DRIVE_URL_PATTERNS = {
    "quark": re.compile(r"pan\.quark\.cn/s/([a-zA-Z0-9]+)", re.I),
    "aliyun": re.compile(r"(?:alipan|aliyundrive)\.com/s/([a-zA-Z0-9]+)", re.I),
    "baidu": re.compile(r"pan\.baidu\.com/s/1([a-zA-Z0-9_-]+)", re.I),
    "xunlei": re.compile(r"pan\.xunlei\.com/s/([a-zA-Z0-9_-]+)", re.I),
    "115": re.compile(r"115(?:cdn)?\.com/s/([a-zA-Z0-9]+)", re.I),
    "123": re.compile(r"123pan\.com/s/([a-zA-Z0-9_-]+)", re.I),
    "tianyi": re.compile(r"cloud\.189\.cn/t/([a-zA-Z0-9_-]+)", re.I),
    "uc": re.compile(r"drive\.uc\.cn/s/([a-zA-Z0-9]+)", re.I),
}

QUARK_TOKEN = "https://drive-h.quark.cn/1/clouddrive/share/sharepage/token"
QUARK_DETAIL = "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail"
ALIYUN_SHARE = "https://api.aliyundrive.com/adrive/v3/share_link/get_share_by_anonymous"


def log(msg):
    print(msg, file=sys.stderr)


def ok(data, hint=""):
    out = {"ok": True, "data": data}
    if hint:
        out["hint"] = hint
    json.dump(out, sys.stdout, ensure_ascii=False)
    print()


def fail(error, hint="", code="error", recoverable=True):
    json.dump({"ok": False, "error": error, "hint": hint, "code": code, "recoverable": recoverable},
              sys.stdout, ensure_ascii=False)
    print()
    sys.exit(1 if recoverable else 2)


def http_get(url, headers=None, timeout=15):
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode()


def http_post_json(url, body, headers=None, timeout=15):
    hdrs = {"User-Agent": UA, "Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode()


def detect_drive(url):
    for drive, pattern in DRIVE_URL_PATTERNS.items():
        if pattern.search(url):
            return drive
    return "others"


def extract_password(item):
    pwd = item.get("password") or item.get("pwd") or ""
    if pwd:
        return pwd
    url = item.get("url", "")
    m = re.search(r"[?&](?:pwd|password|passcode)=([^&]+)", url, re.I)
    return urllib.parse.unquote(m.group(1)) if m else ""


def build_drama_keywords(show, season=None, episode=None, extra="", alt_names=None):
    from query_expand import build_pan_keywords
    return build_pan_keywords(show, season, episode, extra, alt_names)


def title_subtitle_score(note):
    text = (note or "").lower()
    score = 0
    reasons = []
    for kw in SUBTITLE_HINTS:
        if kw in text:
            score += 12
            reasons.append(f"title:{kw}")
    for kw in NEGATIVE_HINTS:
        if kw in text:
            score -= 40
            reasons.append(f"negative:{kw}")
    if ".mkv" in text or "mkv" in text:
        score += 8
        reasons.append("title:mkv")
    if re.search(r"s\d{1,2}e\d{1,2}", text, re.I):
        score += 6
        reasons.append("title:episode")
    return score, reasons


def analyze_files(file_names):
    names = [n for n in file_names if n]
    lower = [n.lower() for n in names]
    mkv = [n for n in names if n.lower().endswith(".mkv")]
    subs = [n for n in names if any(n.lower().endswith(ext) for ext in SUB_EXTS)]
    videos = [n for n in names if any(n.lower().endswith(ext) for ext in VIDEO_EXTS)]
    other_videos = [n for n in videos if not n.lower().endswith(".mkv")]

    score = 0
    reasons = []
    subtitle_mode = "unknown"

    # MKV: format hint only — does NOT confirm subtitles exist
    if mkv:
        score += 10
        reasons.append(f"files:mkv({len(mkv)})")
        subtitle_mode = "mkv_unverified"

    # Case 2: non-MKV video — only counts when paired with subtitle files
    if other_videos:
        score += 8
        reasons.append(f"files:other_video({len(other_videos)})")

    if subs:
        score += 35
        reasons.append(f"files:subtitle({len(subs)})")
        if subtitle_mode == "unknown":
            subtitle_mode = "external"

    basenames = {}
    for n in names:
        stem = re.sub(r"\.[^.]+$", "", n, flags=re.I).lower()
        basenames.setdefault(stem, []).append(n.lower())
    paired = 0
    mkv_paired_sub = 0
    other_paired_sub = 0
    for stem, files in basenames.items():
        has_mkv = any(f.endswith(".mkv") for f in files)
        has_other_video = any(f.endswith(ext) for ext in VIDEO_EXTS if ext != ".mkv" for f in files)
        has_sub = any(f.endswith(ext) for ext in SUB_EXTS for f in files)
        if has_sub and (has_mkv or has_other_video):
            paired += 1
            if has_other_video:
                other_paired_sub += 1
            elif has_mkv:
                mkv_paired_sub += 1
    if other_paired_sub:
        score += min(30, other_paired_sub * 12)
        reasons.append(f"files:other_video+subtitle({other_paired_sub})")
        subtitle_mode = "paired"
    elif mkv_paired_sub:
        score += min(15, mkv_paired_sub * 5)
        reasons.append(f"files:mkv+external_sub({mkv_paired_sub})")
        if subtitle_mode == "embedded_possible":
            subtitle_mode = "paired"

    for n in lower:
        if any(x in n for x in ("subs", "subtitle", "字幕")):
            score += 5
            reasons.append("files:subtitle_folder_or_name")
            break

    return {
        "score": score,
        "reasons": reasons,
        "subtitle_mode": subtitle_mode,
        "mkv_count": len(mkv),
        "other_video_count": len(other_videos),
        "subtitle_count": len(subs),
        "video_count": len(videos),
        "sample_files": names[:8],
    }


def matches_subtitle_criteria(analysis, title_score):
    """Subtitle evidence: title hints, local paired subs, or non-MKV + sub files. MKV alone is NOT enough."""
    if title_score >= 12:
        return True
    if not analysis:
        return False
    mode = analysis.get("subtitle_mode", "unknown")
    if mode in ("paired", "external"):
        if analysis.get("subtitle_count", 0) > 0:
            return True
    if analysis.get("other_video_count", 0) > 0 and analysis.get("subtitle_count", 0) > 0:
        return True
    return False


# ── PanSou ─────────────────────────────────────────────────────────────

def load_health_cache():
    if HEALTH_CACHE.exists():
        try:
            cached = json.loads(HEALTH_CACHE.read_text())
            if time.time() - cached.get("_ts", 0) < HEALTH_TTL:
                return cached
        except (json.JSONDecodeError, OSError):
            pass
    return None


def fetch_health(refresh=False):
    if not refresh:
        cached = load_health_cache()
        if cached:
            return cached
    raw = http_get(f"{PANSOU_BASE}/health", timeout=20)
    data = json.loads(raw, strict=False)
    data["_ts"] = time.time()
    HEALTH_CACHE.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_CACHE.write_text(json.dumps(data, ensure_ascii=False))
    return data


def search_pansou(keyword, page=1, limit=40, channels_csv="", plugins_csv=""):
    params = urllib.parse.urlencode({
        "kw": keyword, "res": "merge", "src": "all",
        "channels": channels_csv, "plugins": plugins_csv,
        "page": str(page), "limit": str(limit),
    })
    url = f"{PANSOU_BASE}/search?{params}"
    raw = http_get(url, timeout=45)
    return json.loads(raw, strict=False)


def flatten_merged(merged_by_type):
    items = []
    for drive, rows in (merged_by_type or {}).items():
        for row in rows or []:
            item = dict(row)
            item["disk_type"] = drive
            items.append(item)
    return items


# ── Validation ─────────────────────────────────────────────────────────

def validate_quark(url, password=""):
    m = DRIVE_URL_PATTERNS["quark"].search(url)
    if not m:
        return {"status": "error", "message": "invalid quark url"}
    pwd_id = m.group(1)
    try:
        raw = http_post_json(QUARK_TOKEN, {
            "pwd_id": pwd_id,
            "passcode": password or "",
            "support_visit_limit_private_share": True,
        }, headers={"origin": "https://pan.quark.cn", "referer": "https://pan.quark.cn/"})
        resp = json.loads(raw, strict=False)
        code = resp.get("code", -1)
        if code == 0:
            return {"status": "valid", "stoken": resp.get("data", {}).get("stoken", ""), "pwd_id": pwd_id}
        if code in (41004, 41006, 41010, 41011):
            return {"status": "expired", "code": code, "message": resp.get("message", "")}
        if code == 41008:
            return {"status": "locked", "code": code, "message": "需要提取码"}
        return {"status": "error", "code": code, "message": resp.get("message", "")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def validate_aliyun(url):
    m = DRIVE_URL_PATTERNS["aliyun"].search(url)
    if not m:
        return {"status": "error", "message": "invalid aliyun url"}
    share_id = m.group(1)
    try:
        raw = http_post_json(f"{ALIYUN_SHARE}?share_id={share_id}", {"share_id": share_id}, headers={
            "origin": "https://www.alipan.com",
            "referer": "https://www.alipan.com/",
            "x-canary": "client=web,app=share,version=v2.3.1",
        })
        resp = json.loads(raw, strict=False)
        if resp.get("share_name") or resp.get("share_title") or (resp.get("file_count") or 0) > 0:
            return {"status": "valid", "share_id": share_id}
        code = (resp.get("code") or "").lower()
        if any(x in code for x in ("notfound", "cancel", "expired", "forbidden")):
            return {"status": "expired", "message": resp.get("message", code)}
        return {"status": "error", "message": resp.get("message", "unknown")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def validate_baidu(url, password=""):
    m = DRIVE_URL_PATTERNS["baidu"].search(url)
    if not m:
        return {"status": "error", "message": "invalid baidu url"}
    short = m.group(1)
    try:
        if password:
            verify_url = f"https://pan.baidu.com/share/verify?surl={urllib.parse.quote(short)}&pwd={urllib.parse.quote(password)}"
            body = http_post_json(verify_url, {}, headers={
                "referer": url,
                "content-type": "application/x-www-form-urlencoded",
            })
            # verify endpoint expects form; fallback GET list without cookie
        list_url = (
            f"https://pan.baidu.com/share/list?web=1&page=1&num=5&order=time&desc=1"
            f"&showempty=0&shorturl={urllib.parse.quote(short)}&root=1&clienttype=0"
        )
        raw = http_get(list_url, headers={"referer": url})
        resp = json.loads(raw, strict=False)
        errno = resp.get("errno", -1)
        if errno == 0 and resp.get("list"):
            return {"status": "valid"}
        if errno in (-9, -12):
            return {"status": "locked", "message": "需要提取码"}
        if errno in (-7, 105, 115, 117, 145):
            return {"status": "expired", "message": resp.get("errmsg", "")}
        return {"status": "error", "message": resp.get("errmsg", f"errno={errno}")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def validate_123(url):
    m = DRIVE_URL_PATTERNS["123"].search(url)
    if not m:
        return {"status": "error", "message": "invalid 123 url"}
    share_key = m.group(1)
    try:
        api = f"https://www.123pan.com/api/share/info?shareKey={urllib.parse.quote(share_key)}"
        raw = http_get(api)
        resp = json.loads(raw, strict=False)
        if resp.get("code") == 0:
            return {"status": "valid"}
        if resp.get("data", {}).get("HasPwd"):
            return {"status": "locked", "message": "需要提取码"}
        return {"status": "expired", "message": resp.get("message", "")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def validate_via_pansou_check(items):
    if not PANSOU_CHECK_BASE:
        return None
    payload = {"items": items}
    try:
        raw = http_post_json(f"{PANSOU_CHECK_BASE}/check/links", payload, timeout=30)
        resp = json.loads(raw, strict=False)
        return resp.get("results", [])
    except Exception:
        return None


def validate_item(item):
    drive = item.get("disk_type") or detect_drive(item.get("url", ""))
    url = item.get("url", "")
    password = extract_password(item)
    result = {"disk_type": drive, "url": url, "password": password, "status": "uncertain"}

    check_items = [{"disk_type": drive, "url": url, "password": password}]
    remote = validate_via_pansou_check(check_items)
    if remote:
        row = remote[0]
        state = row.get("state", "uncertain")
        mapping = {"ok": "valid", "bad": "expired", "locked": "locked", "unsupported": "uncertain", "uncertain": "uncertain"}
        result["status"] = mapping.get(state, "uncertain")
        result["summary"] = row.get("summary", "")
        return result

    if drive == "quark":
        vr = validate_quark(url, password)
    elif drive == "aliyun":
        vr = validate_aliyun(url)
    elif drive == "baidu":
        vr = validate_baidu(url, password)
    elif drive == "123":
        vr = validate_123(url)
    else:
        # lightweight: title-only resources for unsupported drives
        result["status"] = "unchecked"
        result["summary"] = "当前脚本未对该网盘做深度校验，请手动打开确认"
        return result

    result.update(vr)
    return result


def validate_many(items):
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(validate_item, it): i for i, it in enumerate(items)}
        indexed = []
        for fut in as_completed(futs):
            indexed.append((futs[fut], fut.result()))
    indexed.sort(key=lambda x: x[0])
    return [r for _, r in indexed]


# ── File details ───────────────────────────────────────────────────────

def quark_list_files(pwd_id, stoken, pdir_fid="0", size=100):
    params = urllib.parse.urlencode({
        "pr": "ucpro", "fr": "pc", "ver": "2",
        "pwd_id": pwd_id, "stoken": stoken,
        "pdir_fid": pdir_fid, "force": "0",
        "_page": "1", "_size": str(size),
        "_sort": "file_type:asc,updated_at:desc",
    })
    raw = http_get(f"{QUARK_DETAIL}?{params}", headers={
        "origin": "https://pan.quark.cn", "referer": "https://pan.quark.cn/",
    })
    resp = json.loads(raw, strict=False)
    if resp.get("code", -1) != 0:
        return {"error": resp.get("message", "detail failed")}
    files = []
    for f in resp.get("data", {}).get("list", []):
        files.append({
            "file_name": f.get("file_name", ""),
            "dir": f.get("dir", False),
            "size": f.get("size", 0),
            "fid": f.get("fid", ""),
        })
    return {"files": files, "total": resp.get("metadata", {}).get("_total", len(files))}


def aliyun_list_files(share_id, limit=100):
    try:
        raw = http_post_json(f"{ALIYUN_SHARE}?share_id={share_id}", {"share_id": share_id}, headers={
            "origin": "https://www.alipan.com", "referer": "https://www.alipan.com/",
            "x-canary": "client=web,app=share,version=v2.3.1",
        })
        meta = json.loads(raw, strict=False)
        share_token = meta.get("share_token")
        if not share_token:
            return {"error": "missing share_token"}
        body = {
            "share_id": share_id,
            "share_token": share_token,
            "limit": limit,
            "order_by": "name",
            "order_direction": "ASC",
        }
        raw2 = http_post_json("https://api.aliyundrive.com/adrive/v2/file/list_by_share", body, headers={
            "origin": "https://www.alipan.com", "referer": "https://www.alipan.com/",
            "x-canary": "client=web,app=share,version=v2.3.1",
        })
        resp = json.loads(raw2, strict=False)
        files = []
        for f in resp.get("items", []):
            files.append({
                "file_name": f.get("name", ""),
                "dir": f.get("type") == "folder",
                "size": int(f.get("size") or 0),
                "file_id": f.get("file_id", ""),
            })
        return {"files": files, "total": len(files)}
    except Exception as e:
        return {"error": str(e)}


def fetch_file_detail(item, validation):
    drive = item.get("disk_type") or detect_drive(item.get("url", ""))
    url = item.get("url", "")

    if drive == "quark" and validation.get("status") == "valid":
        pwd_id = validation.get("pwd_id")
        stoken = validation.get("stoken")
        if not pwd_id:
            m = DRIVE_URL_PATTERNS["quark"].search(url)
            pwd_id = m.group(1) if m else None
        if pwd_id and stoken:
            detail = quark_list_files(pwd_id, stoken)
            if "files" in detail:
                names = [f["file_name"] for f in detail["files"] if not f.get("dir")]
                return {"detail": detail, "analysis": analyze_files(names)}
            return {"detail": detail}

    if drive == "aliyun" and validation.get("status") == "valid":
        m = DRIVE_URL_PATTERNS["aliyun"].search(url)
        if m:
            detail = aliyun_list_files(m.group(1))
            if "files" in detail:
                names = [f["file_name"] for f in detail["files"] if not f.get("dir")]
                return {"detail": detail, "analysis": analyze_files(names)}
            return {"detail": detail}

    return {}


def score_result(item, validation, file_info):
    title_score, title_reasons = title_subtitle_score(item.get("note", ""))
    file_score = file_info.get("analysis", {}).get("score", 0)
    file_reasons = file_info.get("analysis", {}).get("reasons", [])

    status = validation.get("status")
    status_bonus = {"valid": 20, "locked": 5, "unchecked": 0}.get(status, -100)

    total = title_score + file_score + status_bonus
    reasons = title_reasons + file_reasons
    if status == "valid":
        reasons.append("link:valid")
    elif status == "expired":
        reasons.append("link:expired")
    elif status == "locked":
        reasons.append("link:locked")

    subtitle_mode = file_info.get("analysis", {}).get("subtitle_mode", "title_only")
    if subtitle_mode == "unknown" and title_score >= 12:
        subtitle_mode = "title_hint"

    confidence = "high"
    if status != "valid":
        confidence = "low"
    elif file_info.get("analysis", {}).get("subtitle_mode") in ("paired", "external"):
        confidence = "high"
    elif file_info.get("analysis", {}).get("subtitle_mode") == "mkv_unverified":
        confidence = "medium" if title_score >= 12 else "low"
    elif title_score >= 12:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "total_score": total,
        "title_score": title_score,
        "file_score": file_score,
        "confidence": confidence,
        "subtitle_mode": subtitle_mode,
        "reasons": reasons,
    }


# ── Commands ───────────────────────────────────────────────────────────

def cmd_preflight(_args):
    data = {"ready": True, "services": {}, "config": {"pansou_base": PANSOU_BASE, "pansou_check_base": PANSOU_CHECK_BASE or None}}
    try:
        health = fetch_health(refresh=True)
        data["services"]["pansou"] = {
            "ok": True,
            "channels": len(health.get("channels", [])),
            "plugins": len(health.get("plugins", [])),
        }
    except Exception as e:
        data["services"]["pansou"] = {"ok": False, "error": str(e)}
        data["ready"] = False

    if PANSOU_CHECK_BASE:
        try:
            http_get(f"{PANSOU_CHECK_BASE}/health", timeout=10)
            data["services"]["pansou_check"] = {"ok": True}
        except Exception as e:
            data["services"]["pansou_check"] = {"ok": False, "error": str(e)}

    ok(data, hint="无需本地网盘客户端；只要 PanSou 可用即可搜索。")


def cmd_health(args):
    try:
        data = fetch_health(refresh=args.refresh)
        out = {k: v for k, v in data.items() if not k.startswith("_")}
        ok(out)
    except Exception as e:
        fail(str(e), hint="PanSou 健康检查失败", code="pansou_error")


def cmd_validate(args):
    items = []
    for target in args.targets:
        items.append({"url": target, "disk_type": detect_drive(target), "password": args.password or ""})
    log(f"Validating {len(items)} link(s)...")
    ok({"results": validate_many(items)})


def cmd_search(args):
    show = args.show
    alt = [x.strip() for x in (args.sub_query or "").split(",") if x.strip()]
    if args.alt_names:
        alt.extend(x.strip() for x in args.alt_names.split(",") if x.strip())
    keywords = build_drama_keywords(show, args.season, args.episode, args.keyword or "", alt_names=alt)
    if args.raw_keyword:
        keywords = [args.raw_keyword]

    log("Fetching PanSou health...")
    health = fetch_health()
    channels_csv = ",".join(health.get("channels", []))
    plugins_csv = ",".join(health.get("plugins", []))

    merged_all = {}
    total = 0
    used_keyword = keywords[0]

    for kw in keywords:
        log(f"Searching: {kw}")
        resp = search_pansou(kw, page=args.page, limit=args.limit, channels_csv=channels_csv, plugins_csv=plugins_csv)
        if resp.get("code", -1) != 0:
            fail(resp.get("message", "PanSou search error"), code="pansou_error")
        total = max(total, resp.get("data", {}).get("total", 0))
        merged = resp.get("data", {}).get("merged_by_type", {})
        if resp.get("data", {}).get("total", 0) > 0:
            used_keyword = kw
        for drive, rows in merged.items():
            merged_all.setdefault(drive, []).extend(rows or [])

    candidates = flatten_merged(merged_all)
    # dedupe by url
    seen_urls = set()
    deduped = []
    for c in candidates:
        u = c.get("url", "")
        if u and u not in seen_urls:
            seen_urls.add(u)
            deduped.append(c)

    if args.drives:
        allow = {d.strip().lower() for d in args.drives.split(",") if d.strip()}
        deduped = [c for c in deduped if (c.get("disk_type") or detect_drive(c.get("url", ""))) in allow]

    if args.strict:
        filtered = []
        for c in deduped:
            s, _ = title_subtitle_score(c.get("note", ""))
            if s >= 12 or re.search(r"\.(mkv|srt|ass)", (c.get("note") or ""), re.I):
                filtered.append(c)
        deduped = filtered

    deduped = deduped[: args.limit]
    if not deduped:
        ok({
            "show": show,
            "keyword_used": used_keyword,
            "keywords_tried": keywords,
            "total": total,
            "results": [],
        })
        return

    if args.no_validate:
        results = []
        for item in deduped[: args.top]:
            sc, reasons = title_subtitle_score(item.get("note", ""))
            results.append({**item, "validation": {"status": "skipped"}, "score": {"total_score": sc, "reasons": reasons}})
        ok({
            "show": show,
            "keyword_used": used_keyword,
            "keywords_tried": keywords,
            "name_variants": alt if alt else None,
            "total": total,
            "results": results,
        })
        return

    log(f"Validating {len(deduped)} candidates...")
    validations = validate_many(deduped)
    val_map = {v["url"]: v for v in validations}

    enriched = []
    for item in deduped:
        v = val_map.get(item.get("url", ""), {"status": "error"})
        if v.get("status") == "expired" and not args.include_expired:
            continue
        if v.get("status") == "error" and not args.include_errors:
            continue
        enriched.append((item, v))

    # fetch file details for valid quark/aliyun first
    detail_targets = [(it, v) for it, v in enriched if v.get("status") in ("valid", "locked", "unchecked")][: args.top * 2]
    file_map = {}
    if detail_targets:
        log(f"Fetching file details for {len(detail_targets)} result(s)...")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {pool.submit(fetch_file_detail, it, v): it.get("url") for it, v in detail_targets}
            for fut in as_completed(futs):
                file_map[futs[fut]] = fut.result()

    scored = []
    for item, validation in enriched:
        fi = file_map.get(item.get("url", {}), {})
        score = score_result(item, validation, fi)
        analysis = fi.get("analysis") or {}
        if args.require_subtitle and not matches_subtitle_criteria(analysis, score.get("title_score", 0)):
            continue
        scored.append({
            **item,
            "validation": validation,
            "files": fi.get("detail"),
            "subtitle_analysis": fi.get("analysis"),
            "score": score,
        })

    scored.sort(key=lambda r: r["score"]["total_score"], reverse=True)
    results = scored[: args.top]

    ok({
        "show": show,
        "season": args.season,
        "episode": args.episode,
        "keyword_used": used_keyword,
        "keywords_tried": keywords,
        "name_variants": alt if alt else None,
        "total": total,
        "candidate_count": len(deduped),
        "valid_count": sum(1 for _, v in enriched if v.get("status") == "valid"),
        "results": results,
    })


def main():
    parser = argparse.ArgumentParser(description="Search US TV drama resources with subtitles")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("preflight", help="Check PanSou availability")

    p_health = sub.add_parser("health", help="PanSou health/channels/plugins")
    p_health.add_argument("--refresh", action="store_true")

    p_val = sub.add_parser("validate", help="Validate share links")
    p_val.add_argument("targets", nargs="+")
    p_val.add_argument("--password", default="")

    p_search = sub.add_parser("search", help="Search drama resources with subtitles")
    p_search.add_argument("show", help="Show name, e.g. 风骚律师 or Better Call Saul")
    p_search.add_argument("--sub-query", default="", help="Alt name(s) for query expansion, comma-separated English/中文")
    p_search.add_argument("--alt-names", default="", help="More aliases, comma-separated")
    p_search.add_argument("--season", type=int, default=None)
    p_search.add_argument("--episode", type=int, default=None)
    p_search.add_argument("--keyword", default="", help="Custom keyword prefix")
    p_search.add_argument("--raw-keyword", default="", help="Use exact keyword only")
    p_search.add_argument("--top", type=int, default=10)
    p_search.add_argument("--limit", type=int, default=80)
    p_search.add_argument("--page", type=int, default=1)
    p_search.add_argument("--drives", default="", help="Comma-separated drive filter: quark,aliyun,baidu,xunlei,115,123")
    p_search.add_argument("--strict", action="store_true", help="Pre-filter title subtitle hints")
    p_search.add_argument("--require-subtitle", action="store_true", default=True)
    p_search.add_argument("--no-require-subtitle", action="store_false", dest="require_subtitle")
    p_search.add_argument("--no-validate", action="store_true")
    p_search.add_argument("--include-expired", action="store_true")
    p_search.add_argument("--include-errors", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help(sys.stderr)
        sys.exit(2)

    handlers = {
        "preflight": cmd_preflight,
        "health": cmd_health,
        "validate": cmd_validate,
        "search": cmd_search,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
