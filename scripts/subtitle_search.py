#!/usr/bin/env python3
"""Subtitle search helper — wraps opensubtitles-scraper when available."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

OSS_BASE = os.environ.get("OPENSUBTITLES_SCRAPER_BASE", "http://localhost:8000").rstrip("/")
UA = "PanDramaSubtitleSkill/1.0"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from query_expand import build_subtitle_queries


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


def http_json(method, url, body=None, timeout=120):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def preflight(_args):
    data = {"ready": False, "service": OSS_BASE, "manual_sites": [
        {"name": "SubHD", "url": "https://subhdtw.com"},
        {"name": "SubDL", "url": "https://subdl.com/"},
        {"name": "Podnapisi", "url": "https://www.podnapisi.net/"},
    ]}
    try:
        health = http_json("GET", f"{OSS_BASE}/api/v1/health", timeout=15)
        data["scraper"] = health
        data["ready"] = health.get("status") == "healthy"
        ok(data, hint="opensubtitles-scraper 可用。" if data["ready"] else "scraper 未就绪。")
    except Exception as e:
        data["error"] = str(e)
        ok(data, hint="未检测到 opensubtitles-scraper。可 Docker 部署或给用户手动字幕站链接。见 know-how.md")


def search_tv(args):
    body = {"query": args.query}
    if args.year:
        body["year"] = args.year
    if args.imdb:
        body["imdb_id"] = args.imdb
    try:
        resp = http_json("POST", f"{OSS_BASE}/api/v1/search/tv", body)
    except urllib.error.URLError as e:
        fail(str(e), hint="opensubtitles-scraper 未运行。见 know-how.md", code="scraper_unreachable")
    results = resp.get("results", [])
    if args.pick_first and results:
        results = results[:1]
    ok({"query": args.query, "total": resp.get("total", len(results)), "results": results})


def list_subtitles(args):
    if not args.movie_url:
        fail("movie_url required", hint="先 search-tv 拿到 url 字段")
    langs = [x.strip() for x in (args.languages or "zh,en").split(",") if x.strip()]
    body = {
        "movie_url": args.movie_url,
        "languages": langs,
    }
    if args.season is not None:
        body["season"] = args.season
    if args.episode is not None:
        body["episode"] = args.episode
    try:
        resp = http_json("POST", f"{OSS_BASE}/api/v1/subtitles", body)
    except urllib.error.URLError as e:
        fail(str(e), code="scraper_unreachable")
    subs = resp.get("subtitles", [])
    if args.match:
        key = args.match.lower()
        subs = [s for s in subs if key in (s.get("filename") or "").lower() or key in (s.get("release_name") or "").lower()]
    ok({
        "movie_url": args.movie_url,
        "season": args.season,
        "episode": args.episode,
        "total": resp.get("total", len(subs)),
        "subtitles": subs[: args.top],
    })


def find_episode(args):
    """One-shot: search TV (with query expansion) -> list subtitles for SxxExx."""
    alt = [x.strip() for x in (getattr(args, "sub_query", "") or "").split(",") if x.strip()]
    queries = build_subtitle_queries(args.query, alt_names=alt)
    results = []
    used_query = queries[0] if queries else args.query

    for q in queries:
        try:
            search = http_json("POST", f"{OSS_BASE}/api/v1/search/tv", {"query": q})
        except urllib.error.URLError as e:
            fail(str(e), code="scraper_unreachable", hint="部署 opensubtitles-scraper 或使用手动字幕站")
        results = search.get("results", [])
        if results:
            used_query = q
            break

    if not results:
        fail(
            "No TV show found",
            code="not_found",
            hint=f"已尝试 query 泛化: {queries}。换剧名或提供 --imdb",
        )

    pick = results[0]
    if args.imdb:
        for r in results:
            if r.get("imdb_id") == args.imdb:
                pick = r
                break

    langs = [x.strip() for x in (args.languages or "zh,en").split(",") if x.strip()]
    body = {
        "movie_url": pick["url"],
        "languages": langs,
        "season": args.season,
        "episode": args.episode,
    }
    sub_resp = http_json("POST", f"{OSS_BASE}/api/v1/subtitles", body)
    subs = sub_resp.get("subtitles", [])
    if args.match:
        key = args.match.lower()
        matched = [s for s in subs if key in (s.get("filename") or "").lower()]
        if matched:
            subs = matched
    ok({
        "show": pick,
        "query_used": used_query,
        "queries_tried": queries,
        "season": args.season,
        "episode": args.episode,
        "subtitle_total": sub_resp.get("total", len(subs)),
        "subtitles": subs[: args.top],
        "hint_align": "选与网盘视频发布组/分辨率/季集标记最一致的字幕文件名，时间轴才容易对齐。",
    })


def main():
    parser = argparse.ArgumentParser(description="Subtitle search via opensubtitles-scraper")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("preflight", help="Check opensubtitles-scraper availability")

    p_tv = sub.add_parser("search-tv", help="Search TV show on OpenSubtitles.org via scraper")
    p_tv.add_argument("query")
    p_tv.add_argument("--year", type=int)
    p_tv.add_argument("--imdb")
    p_tv.add_argument("--pick-first", action="store_true")

    p_ls = sub.add_parser("list", help="List subtitles for a show URL")
    p_ls.add_argument("--movie-url", required=True)
    p_ls.add_argument("--season", type=int)
    p_ls.add_argument("--episode", type=int)
    p_ls.add_argument("--languages", default="zh,en")
    p_ls.add_argument("--match", default="", help="Filter filenames containing this string")
    p_ls.add_argument("--top", type=int, default=10)

    p_find = sub.add_parser("find-episode", help="Search show + list subtitles for one episode")
    p_find.add_argument("query", help="Primary show name (English preferred for subtitles)")
    p_find.add_argument("--sub-query", default="", help="Alt names for query expansion, comma-separated")
    p_find.add_argument("--season", type=int, required=True)
    p_find.add_argument("--episode", type=int, required=True)
    p_find.add_argument("--imdb")
    p_find.add_argument("--languages", default="zh,en")
    p_find.add_argument("--match", default="", help="Prefer filenames matching video release, e.g. 720p.BluRay")
    p_find.add_argument("--top", type=int, default=10)

    args = parser.parse_args()
    if not args.command:
        parser.print_help(sys.stderr)
        sys.exit(2)

    {"preflight": preflight, "search-tv": search_tv, "list": list_subtitles, "find-episode": find_episode}[args.command](args)


if __name__ == "__main__":
    main()
