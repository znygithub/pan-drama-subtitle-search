#!/usr/bin/env python3
"""Match cloud-drive episode videos with subtitle search results (one row per pair)."""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
PAN_SCRIPT = os.path.join(SCRIPT_DIR, "pan_drama_search.py")
SUB_SCRIPT = os.path.join(SCRIPT_DIR, "subtitle_search.py")

OSS_BASE = os.environ.get("OPENSUBTITLES_SCRAPER_BASE", "http://localhost:8000").rstrip("/")
QUARK_TOKEN = "https://drive-h.quark.cn/1/clouddrive/share/sharepage/token"
QUARK_DETAIL = "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail"
ALIYUN_SHARE = "https://api.aliyundrive.com/adrive/v3/share_link/get_share_by_anonymous"
UA = "PanDramaEpisodeMatch/1.0"

VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".ts", ".m2ts")
SUB_EXTS = (".srt", ".ass", ".ssa", ".sub", ".sup")
EMBED_HINTS = ("内封", "内嵌", "硬字幕", "双语字幕", "中英字幕", "简英", "简繁")


def log(msg):
    print(msg, file=sys.stderr)


def ok(data, hint=""):
    out = {"ok": True, "data": data}
    if hint:
        out["hint"] = hint
    json.dump(out, sys.stdout, ensure_ascii=False)
    print()


def fail(error, hint="", code="error"):
    json.dump({"ok": False, "error": error, "hint": hint, "code": code}, sys.stdout, ensure_ascii=False)
    print()
    sys.exit(1)


def run_json(script, args, timeout=180, soft=False):
    proc = subprocess.run(
        [sys.executable, script, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    raw = proc.stdout.strip()
    if not raw:
        if soft:
            return None
        fail(proc.stderr.strip() or "empty output", code="cli_error")
    start = raw.find("{")
    if start < 0:
        if soft:
            return None
        fail(raw[:300], code="cli_error")
    payload = json.loads(raw[start:])
    if not payload.get("ok"):
        if soft:
            return None
        fail(payload.get("error", "command failed"), hint=payload.get("hint", ""), code=payload.get("code", "cli_error"))
    return payload["data"]


def http_json(method, url, body=None, timeout=30):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def episode_pattern(season, episode):
    s, e = int(season), int(episode)
    parts = [
        rf"s{int(s):02d}e{int(e):02d}(?!\d)",
        rf"s{int(s):02d}\s*e{int(e):02d}(?!\d)",
        rf"(?<!\d){int(s)}x{int(e):02d}(?!\d)",
    ]
    if e < 10:
        parts.append(rf"s0?{s}e{e}(?!\d)")
    return re.compile("|".join(parts), re.I)


def sub_display_name(sub):
    raw = (sub.get("filename") or sub.get("release_name") or "").strip()
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in raw.split("\n") if ln.strip()]
    if not lines:
        return ""
    # prefer line that looks like a filename
    for ln in lines:
        if re.search(r"\.(srt|ass|ssa|sub)$", ln, re.I):
            return ln[:160]
    return max(lines, key=len)[:160]


def match_score(video, subtitle):
    v = video.lower()
    s = subtitle.lower()
    score = 0
    reasons = []
    if re.search(r"s\d{1,2}e\d{1,2}", v) and re.search(r"s\d{1,2}e\d{1,2}", s):
        score += 40
        reasons.append("SxxExx")
    for tag in ("2160p", "1080p", "720p", "480p"):
        if tag in v and tag in s:
            score += 15
            reasons.append(tag)
            break
    for tag in ("web-dl", "webdl", "webrip", "bluray", "blu-ray", "remux", "hdtv"):
        vt = tag.replace("-", "")
        if vt in v.replace("-", "") and vt in s.replace("-", ""):
            score += 12
            reasons.append(tag)
            break
    for tag in ("nf", "netflix", "amzn", "ctrlhd"):
        if tag in v and tag in s:
            score += 8
            reasons.append(tag)
    # penalize obvious version mismatch
    vblur = "bluray" in v.replace("-", "") or "blu-ray" in v
    sblur = "bluray" in s.replace("-", "") or "blu-ray" in s
    vweb = "webdl" in v.replace("-", "") or "webrip" in v.replace("-", "")
    sweb = "webdl" in s.replace("-", "") or "webrip" in s.replace("-", "")
    if vblur and sblur:
        score += 10
        reasons.append("bluray-match")
    elif vweb and sweb:
        score += 10
        reasons.append("web-match")
    elif (vblur and sweb) or (vweb and sblur):
        score -= 8
        reasons.append("version-mismatch")
    vc = re.sub(r"[^a-z0-9]+", "", re.sub(r"\.(mkv|mp4|srt|ass|ssa)$", "", v))
    sc = re.sub(r"[^a-z0-9]+", "", re.sub(r"\.(mkv|mp4|srt|ass|ssa)$", "", s))
    if len(vc) > 8 and len(sc) > 8 and (vc in sc or sc in vc):
        score += 20
        reasons.append("name-overlap")
    return score, reasons


BILINGUAL_HINTS = re.compile(
    r"中英|双语|bilingual|dual|chs.?eng|eng.?chs|chinese.?english|简英|zh.?en|en.?zh",
    re.I,
)


def opensubtitles_link(sub):
    url = (sub.get("download_url") or "").strip()
    if url:
        return url
    sid = sub.get("subtitle_id")
    if sid:
        return f"https://www.opensubtitles.org/en/subtitles/{sid}"
    return ""


def is_bilingual_name(name):
    return bool(BILINGUAL_HINTS.search(name or ""))


def sub_entry(sub, name, score, reasons, in_share=False, share_url=""):
    if in_share:
        link = share_url
    else:
        link = opensubtitles_link(sub)
    return {
        "label": name[:120] if name else "",
        "url": link,
        "subtitle_id": sub.get("subtitle_id"),
        "score": score,
        "reasons": reasons,
        "language": sub.get("language"),
        "in_share": in_share,
    }


def rank_subtitles(video, subtitles):
    ranked = []
    for s in subtitles:
        name = sub_display_name(s)
        if not name:
            continue
        sc, reasons = match_score(video, name)
        if sc <= 0:
            continue
        bi = is_bilingual_name(name)
        if bi:
            sc += 30
            reasons.append("bilingual")
        ranked.append((sc, bi, reasons, s, name))
    ranked.sort(key=lambda x: (-x[0], -len(x[4])))
    return ranked


def pick_subtitles_for_video(video, subtitles, local_subs, share_url):
    """Priority: bilingual > zh+en separate > zh only > en only (best effort)."""
    ranked = rank_subtitles(video, subtitles)
    out = {
        "mode": "none",
        "bilingual": None,
        "zh": None,
        "en": None,
        "links": [],
    }

    bi = [r for r in ranked if r[1]]
    if bi:
        sc, _, reasons, s, name = bi[0]
        entry = sub_entry(s, name, sc, reasons)
        out.update(mode="bilingual", bilingual=entry, links=[entry])
        return out

    local_zh = local_sub_for_video(video, local_subs, "zh")
    local_en = local_sub_for_video(video, local_subs, "en")
    local_bi = next((s for s in local_subs if is_bilingual_name(s)), None)
    if local_bi:
        entry = sub_entry({}, local_bi, 100, ["local-bilingual"], in_share=True, share_url=share_url)
        out.update(mode="bilingual", bilingual=entry, links=[entry])
        return out
    if local_zh and local_en:
        zh_e = sub_entry({}, local_zh, 100, ["local-pair"], in_share=True, share_url=share_url)
        en_e = sub_entry({}, local_en, 100, ["local-pair"], in_share=True, share_url=share_url)
        out.update(mode="zh_en", zh=zh_e, en=en_e, links=[zh_e, en_e])
        return out

    zh_ranked = [r for r in ranked if (r[3].get("language") or "").startswith("zh")]
    en_ranked = [r for r in ranked if (r[3].get("language") or "").startswith("en")]

    if local_zh:
        out["zh"] = sub_entry({}, local_zh, 100, ["local-zh"], in_share=True, share_url=share_url)
    elif zh_ranked:
        sc, _, reasons, s, name = zh_ranked[0]
        out["zh"] = sub_entry(s, name, sc, reasons)

    if local_en:
        out["en"] = sub_entry({}, local_en, 100, ["local-en"], in_share=True, share_url=share_url)
    elif en_ranked:
        sc, _, reasons, s, name = en_ranked[0]
        out["en"] = sub_entry(s, name, sc, reasons)

    links = [x for x in (out.get("zh"), out.get("en")) if x and x.get("url")]
    out["links"] = links
    if out["zh"] and out["en"]:
        out["mode"] = "zh_en"
    elif out["zh"]:
        out["mode"] = "zh_only"
    elif out["en"]:
        out["mode"] = "en_only"
    return out


def subtitle_mode_label(mode):
    return {
        "bilingual": "中英双语",
        "zh_en": "中+英（分开）",
        "zh_only": "仅中文",
        "en_only": "仅英文",
        "none": "未匹配",
    }.get(mode, mode)


def link_cell(entry, label=None):
    if not entry or not entry.get("url"):
        return "—"
    text = label or entry.get("language") or ("网盘内" if entry.get("in_share") else "字幕")
    return f"[{text}]({entry['url']})"


def video_category(video, local_subs, sub_pick):
    bi = sub_pick.get("bilingual") or {}
    zh = sub_pick.get("zh") or {}
    en = sub_pick.get("en") or {}
    has_local = bi.get("in_share") or zh.get("in_share") or en.get("in_share")
    if not has_local and local_subs and video:
        stem = re.sub(r"\.[^.]+$", "", video, flags=re.I).lower()
        for s in local_subs:
            sstem = re.sub(r"\.[^.]+$", "", s, flags=re.I).lower()
            if stem and (stem == sstem or stem in sstem or sstem in stem):
                has_local = True
                break
    if has_local:
        return "local_paired"
    if has_subtitle_links(sub_pick):
        return "external_paired"
    if (video or "").lower().endswith(".mkv"):
        return "mkv_embedded_only"
    return "external_paired"


MODE_RANK = {"bilingual": 3, "zh_en": 2, "zh_only": 1, "en_only": 1, "none": 0}

CATEGORY_RANK = {
    "local_paired": 0,
    "external_paired": 1,
    "mkv_embedded_only": 2,
}

DISK_LABELS = {
    "quark": "夸克",
    "aliyun": "阿里",
    "baidu": "百度",
    "123": "123盘",
    "xunlei": "迅雷",
    "115": "115",
    "tianyi": "天翼",
    "uc": "UC",
    "others": "网盘",
}


def disk_label(drive):
    d = (drive or "others").lower()
    return DISK_LABELS.get(d, d or "网盘")


def has_subtitle_links(sub_pick):
    if sub_pick.get("mode") == "none":
        return False
    for key in ("bilingual", "zh", "en"):
        e = sub_pick.get(key) or {}
        if e.get("url"):
            return True
    return False


CHECK_SUBTITLE_GUIDE = "know-how.md#自查方法"


def video_link_cell(r):
    label = r.get("video_disk_label") or disk_label(r.get("video_disk"))
    return f"[{label}]({r['video_url']})"


def pwd_cell(r):
    p = r.get("video_password")
    return str(p).strip() if p else ""


def subtitle_links_cell(r):
    parts = []
    bi = r.get("subtitle_bilingual") or {}
    zh = r.get("subtitle_zh") or {}
    en = r.get("subtitle_en") or {}
    if bi.get("url"):
        parts.append(link_cell(bi, "中英双语"))
    else:
        if zh.get("url"):
            parts.append(link_cell(zh, "中文"))
        if en.get("url"):
            parts.append(link_cell(en, "英文"))
    return " · ".join(parts) if parts else "—"


def build_display_sections(rows):
    tier1 = [r for r in rows if r.get("video_category") == "local_paired"]
    tier2 = [r for r in rows if r.get("video_category") == "external_paired"]
    tier3 = [r for r in rows if r.get("video_category") == "mkv_embedded_only"]

    any_pwd = any(pwd_cell(r) for r in rows)
    pwd_hdr = " | 提取码" if any_pwd else ""
    pwd_sep = " |" if any_pwd else ""

    sections = []

    if tier1:
        lines = [
            "### 一、视频 + 同分享字幕文件（最优先）",
            "",
            "> 网盘分享里已有配对的 `.srt` / `.ass`，与视频同目录，对轴最可靠。",
            "",
            f"| # | 说明 | 网盘{pwd_hdr} | 字幕链接 | 匹配度 |",
            f"|---|------|------{pwd_sep}----------|--------|",
        ]
        for r in tier1:
            desc = (r.get("share_title") or r.get("video_file_hint") or "")[:60]
            subs = subtitle_links_cell(r)
            row = f"| {r['rank']} | {desc} | {video_link_cell(r)}"
            if any_pwd:
                row += f" | {pwd_cell(r)}"
            row += f" | {subs} | {r.get('match_confidence', 'low')} |"
            lines.append(row)
        sections.append("\n".join(lines))

    if tier2:
        lines = [
            "### 二、视频 + 配对字幕链接",
            "",
            "> 视频与字幕来自 OpenSubtitles 等匹配；下载视频后加载同行字幕。",
            "",
            f"| # | 说明 | 网盘{pwd_hdr} | 字幕下载 | 类型 | 匹配度 |",
            f"|---|------|------{pwd_sep}----------|------|--------|",
        ]
        for r in tier2:
            desc = (r.get("share_title") or r.get("video_file_hint") or "")[:60]
            subs = subtitle_links_cell(r)
            row = f"| {r['rank']} | {desc} | {video_link_cell(r)}"
            if any_pwd:
                row += f" | {pwd_cell(r)}"
            row += f" | {subs} | {r.get('subtitle_type', '—')} | {r.get('match_confidence', 'low')} |"
            lines.append(row)
        sections.append("\n".join(lines))

    if tier3:
        lines = [
            "### 三、MKV 可能内封字幕（请你自查）",
            "",
            "> MKV **可能**已有内封字幕。请先按 [自查方法](know-how.md#自查方法) 检查——",
            "> **有内封就不用下载右侧外挂**；没有再用右侧配对字幕链接。",
            "",
            f"| # | 说明 | 网盘{pwd_hdr} | 如何自查 | 若无内封→下载字幕 | 匹配度 |",
            f"|---|------|------{pwd_sep}----------|-------------------|--------|",
        ]
        for r in tier3:
            desc = (r.get("share_title") or r.get("video_file_hint") or "")[:60]
            subs = subtitle_links_cell(r)
            row = f"| {r['rank']} | {desc} | {video_link_cell(r)}"
            if any_pwd:
                row += f" | {pwd_cell(r)}"
            row += f" | [自查方法](know-how.md#自查方法) | {subs} | {r.get('match_confidence', 'low')} |"
            lines.append(row)
        sections.append("\n".join(lines))

    return "\n\n".join(sections) if sections else "未找到可用资源。"


def build_display_table(rows):
    """Legacy single table; prefer build_display_sections."""
    return build_display_sections(rows)


def row_confidence(sub_pick, embed):
    if sub_pick["mode"] == "bilingual":
        return "high"
    if sub_pick["mode"] == "zh_en":
        best = max((x.get("score", 0) for x in sub_pick["links"]), default=0)
        return "high" if best >= 50 else "medium"
    if sub_pick["mode"] in ("zh_only", "en_only"):
        sc = sub_pick["links"][0].get("score", 0) if sub_pick["links"] else 0
        return "medium" if sc >= 40 or embed else "low"
    if embed:
        return "low"
    return "low"


def quark_list(pid, stoken, fid="0"):
    q = urllib.parse.urlencode({
        "pr": "ucpro", "fr": "pc", "ver": "2",
        "pwd_id": pid, "stoken": stoken, "pdir_fid": fid, "force": "0",
        "_page": "1", "_size": "100", "_sort": "file_type:asc,updated_at:desc",
    })
    return http_json("GET", f"{QUARK_DETAIL}?{q}", timeout=25)


def explore_quark_share(url, season, episode, max_depth=2):
    m = re.search(r"pan\.quark\.cn/s/([a-zA-Z0-9]+)", url)
    if not m:
        return [], []
    pid = m.group(1)
    tok = http_json("POST", QUARK_TOKEN, {
        "pwd_id": pid, "passcode": "", "support_visit_limit_private_share": True,
    }, timeout=20)
    if tok.get("code") != 0:
        return [], []
    stoken = tok["data"]["stoken"]
    ep_re = episode_pattern(season, episode)

    videos, subs = [], []
    stack = ["0"]
    seen = set()
    depth_map = {"0": 0}

    while stack:
        fid = stack.pop()
        if fid in seen:
            continue
        seen.add(fid)
        det = quark_list(pid, stoken, fid)
        if det.get("code") != 0:
            continue
        for f in det.get("data", {}).get("list", []):
            name = f.get("file_name", "")
            if f.get("dir"):
                if depth_map.get(fid, 0) < max_depth:
                    depth_map[f.get("fid")] = depth_map.get(fid, 0) + 1
                    stack.append(f.get("fid"))
            else:
                low = name.lower()
                if any(low.endswith(ext) for ext in VIDEO_EXTS) and ep_re.search(name):
                    videos.append(name)
                if any(low.endswith(ext) for ext in SUB_EXTS) and ep_re.search(name):
                    subs.append(name)
    return videos, subs


def explore_aliyun_share(url, season, episode):
    m = re.search(r"(?:alipan|aliyundrive)\.com/s/([a-zA-Z0-9]+)", url, re.I)
    if not m:
        return [], []
    share_id = m.group(1)
    ep_re = episode_pattern(season, episode)
    try:
        meta = http_json("POST", f"{ALIYUN_SHARE}?share_id={share_id}", {"share_id": share_id}, timeout=25)
        share_token = (meta.get("share_token") or "")
        if not share_token:
            return [], []
        body = {
            "share_id": share_id,
            "share_token": share_token,
            "limit": 200,
            "order_by": "name",
            "order_direction": "ASC",
        }
        resp = http_json("POST", "https://api.aliyundrive.com/adrive/v2/file/list_by_share", body, timeout=25)
    except Exception:
        return [], []

    videos, subs = [], []
    for f in resp.get("items", []):
        if f.get("type") == "folder":
            continue
        name = f.get("name", "")
        low = name.lower()
        if any(low.endswith(ext) for ext in VIDEO_EXTS) and ep_re.search(name):
            videos.append(name)
        if any(low.endswith(ext) for ext in SUB_EXTS) and ep_re.search(name):
            subs.append(name)
    return videos, subs


def explore_share_files(url, drive, season, episode):
    """List episode video/sub files inside a share (quark, aliyun, …)."""
    drive = (drive or "").lower()
    if "quark" in drive:
        return explore_quark_share(url, season, episode)
    if "aliyun" in drive:
        return explore_aliyun_share(url, season, episode)
    return [], []


def local_sub_for_video(video, subs, lang_hint):
    stem = re.sub(r"\.[^.]+$", "", video, flags=re.I).lower()
    best = None
    for s in subs:
        sstem = re.sub(r"\.[^.]+$", "", s, flags=re.I).lower()
        if stem != sstem and stem not in sstem and sstem not in stem:
            continue
        if lang_hint == "zh" and re.search(r"zh|chs|cht|hans|简体|繁体|中文", s, re.I):
            best = s
        elif lang_hint == "en" and re.search(r"\.en\.|english", s, re.I):
            best = s
    return best


def matching_video_hint(video, show, sub_query, season, episode):
    """Use real filename for scoring; synthesize SxxExx hint for season packs."""
    if video and re.search(r"\.(mkv|mp4|avi|ts|m2ts)$", video, re.I):
        return video
    base = (sub_query or show or "show").strip()
    dotted = re.sub(r"\s+", ".", base)
    return f"{dotted}.S{int(season):02d}E{int(episode):02d}.1080p"


def pick_match_reasons(sub_pick):
    reasons = []
    for key in ("bilingual", "zh", "en"):
        e = sub_pick.get(key) or {}
        if e.get("reasons"):
            reasons.extend(e["reasons"])
    return _dedupe_reasons(reasons) if reasons else []


def _dedupe_reasons(items):
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def embed_hint(share_note):
    for h in EMBED_HINTS:
        if h in (share_note or ""):
            return h
    return None


def cmd_match(args):
    season, episode = args.season, args.episode
    sub_query = args.sub_query or args.show

    log("Searching cloud drives...")
    pan_args = [
        "search", args.show,
        "--season", str(season), "--episode", str(episode),
        "--top", str(args.top), "--limit", str(args.pan_limit),
        "--no-require-subtitle",
    ]
    # Always pass alt name for CN↔EN query expansion
    if sub_query.strip():
        pan_args.extend(["--sub-query", sub_query.strip()])
    if args.drives:
        pan_args.extend(["--drives", args.drives])
    pan = run_json(PAN_SCRIPT, pan_args, timeout=240)

    shares = [r for r in pan.get("results", []) if r.get("validation", {}).get("status") == "valid"]
    if not shares and pan.get("results"):
        shares = [r for r in pan["results"] if r.get("validation", {}).get("status") in ("valid", "locked", "unchecked")]

    log("Searching subtitles (always, including for MKV)...")
    subtitles = []
    show_meta = {}
    sub_primary = sub_query if sub_query else args.show
    sub_alt = args.show if sub_query and sub_query.strip() != args.show.strip() else ""
    sub_args = [
        "find-episode", sub_primary,
        "--season", str(season), "--episode", str(episode),
        "--languages", args.languages, "--top", str(args.sub_top),
    ]
    if sub_alt:
        sub_args.extend(["--sub-query", sub_alt])
    sub = run_json(SUB_SCRIPT, sub_args, timeout=240, soft=True)
    subtitle_service = "ok" if sub else "unavailable"
    if sub:
        subtitles = sub.get("subtitles", [])
        show_meta = sub.get("show", {})
    else:
        log("Subtitle scraper unavailable; rows will list video only + manual site hint.")

    pair_rows = []
    rank = 0
    ep_re = episode_pattern(season, episode)

    for share in shares[: args.top]:
        url = share.get("url", "")
        note = share.get("note", "")
        drive = share.get("disk_type", "unknown")
        password = share.get("password") or extract_password(share)

        videos, local_subs = explore_share_files(url, drive, season, episode)

        if not videos:
            analysis = (share.get("subtitle_analysis") or {})
            sample = analysis.get("sample_files") or []
            videos = [f for f in sample if ep_re.search(f) and f.lower().endswith(VIDEO_EXTS)] if sample else []
            if not videos:
                videos = [f for f in sample if f.lower().endswith(VIDEO_EXTS)]
            local_subs = [f for f in sample if f.lower().endswith(SUB_EXTS)]
        if not videos:
            # 整季包：仍输出一行并配对该集字幕链接
            videos = [note[:120] or "整季合集"]
            local_subs = local_subs or [f for f in (sample or []) if f.lower().endswith(SUB_EXTS)]
        if not videos:
            continue

        hint = embed_hint(note)

        for video in videos:
            rank += 1
            local_zh = local_sub_for_video(video, local_subs, "zh")
            local_en = local_sub_for_video(video, local_subs, "en")
            hint_video = matching_video_hint(video, args.show, sub_query, season, episode)
            sub_pick = pick_subtitles_for_video(hint_video, subtitles, local_subs, url)
            conf = row_confidence(sub_pick, hint)
            match_reasons = pick_match_reasons(sub_pick)

            notes = []
            if sub_pick["mode"] == "bilingual":
                notes.append("已匹配中英双语字幕链接")
            elif sub_pick["mode"] == "zh_en":
                notes.append("无双语包，已分别给出中文字幕与英文字幕链接")
            elif sub_pick["mode"] == "zh_only":
                notes.append("仅找到中文字幕链接")
            elif sub_pick["mode"] == "en_only":
                notes.append("仅找到英文字幕链接")
            elif hint:
                notes.append(f"分享标注「{hint}」，请你下载 MKV 后自查内封字幕；没有再用外挂链接")
            else:
                notes.append("未匹配到字幕链接")

            if sub_pick.get("zh", {}).get("in_share") or sub_pick.get("en", {}).get("in_share"):
                notes.append(f"字幕在同分享内，选文件：{local_zh or ''} {local_en or ''}".strip())

            if match_reasons:
                notes.append(f"版本校对：{', '.join(match_reasons)}")

            cat = video_category(video, local_subs, sub_pick)

            pair_rows.append({
                "rank": rank,
                "video_url": url,
                "video_password": password or None,
                "video_disk": drive,
                "video_disk_label": disk_label(drive),
                "video_file_hint": video,
                "matching_hint": hint_video,
                "video_category": cat,
                "share_title": note,
                "subtitle_mode": sub_pick["mode"],
                "subtitle_type": subtitle_mode_label(sub_pick["mode"]),
                "subtitle_bilingual": sub_pick.get("bilingual"),
                "subtitle_zh": sub_pick.get("zh"),
                "subtitle_en": sub_pick.get("en"),
                "embedded_hint": hint,
                "match_confidence": conf,
                "match_reasons": match_reasons,
                "note": "；".join(notes),
            })

    pair_rows.sort(key=lambda r: (
        CATEGORY_RANK.get(r.get("video_category"), 9),
        {"high": 0, "medium": 1, "low": 2}[r["match_confidence"]],
        -MODE_RANK.get(r.get("subtitle_mode", "none"), 0),
    ))
    for i, r in enumerate(pair_rows, 1):
        r["rank"] = i

    ok({
        "show": args.show,
        "sub_query": sub_query,
        "season": season,
        "episode": episode,
        "imdb_id": show_meta.get("imdb_id"),
        "share_count": len(shares),
        "subtitle_service": subtitle_service,
        "subtitle_pool_size": len(subtitles),
        "subtitle_query_used": sub.get("query_used") if sub else None,
        "subtitle_queries_tried": sub.get("queries_tried") if sub else None,
        "pan_keywords_tried": pan.get("keywords_tried"),
        "pair_rows": pair_rows,
        "display_sections": build_display_sections(pair_rows),
        "display_table": build_display_sections(pair_rows),
        "check_subtitle_guide": CHECK_SUBTITLE_GUIDE,
        "pairing_rule": "一、同分享字幕文件 → 二、视频+配对字幕链接 → 三、MKV可能内封；网盘标注名称；有提取码才写。",
    }, hint="按 display_sections 三段输出；每行标注网盘名；一/二段必须有字幕链接。")


def extract_password(item):
    pwd = item.get("password") or item.get("pwd") or ""
    if pwd:
        return pwd
    url = item.get("url", "")
    m = re.search(r"[?&](?:pwd|password|passcode)=([^&]+)", url, re.I)
    return urllib.parse.unquote(m.group(1)) if m else ""


def main():
    parser = argparse.ArgumentParser(description="Match episode videos with subtitles (one row per pair)")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("match", help="Search video + subtitles and pair in rows")
    p.add_argument("show", help="Show name for cloud search (e.g. 风骚律师)")
    p.add_argument("--sub-query", default="", help="English name for subtitle search (e.g. Better Call Saul)")
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--episode", type=int, required=True)
    p.add_argument("--top", type=int, default=10, help="Max shares to explore")
    p.add_argument("--sub-top", type=int, default=50, help="Subtitle pool size")
    p.add_argument("--pan-limit", type=int, default=80, help="Max pan search candidates before validate")
    p.add_argument("--drives", default="", help="quark,aliyun,...")
    p.add_argument("--languages", default="zh,en")

    args = parser.parse_args()
    if args.command != "match":
        parser.print_help(sys.stderr)
        sys.exit(2)
    cmd_match(args)


if __name__ == "__main__":
    main()
