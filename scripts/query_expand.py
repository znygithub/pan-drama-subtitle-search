"""Query expansion: CN name + EN name + 字幕 (keep it small for speed)."""

import re

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _dedupe(items):
    seen = set()
    out = []
    for x in items:
        x = (x or "").strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def is_latin_name(name):
    s = (name or "").strip()
    if not s:
        return False
    if _CJK.search(s):
        return False
    return bool(re.search(r"[A-Za-z]", s))


def unique_show_names(show, alt_names=None):
    """中文名 + 英文名，去重，不做空格/点号变体。"""
    return _dedupe([show, *(alt_names or [])])


def season_episode_tag(season=None, episode=None):
    if season is None:
        return ""
    se = f"S{int(season):02d}"
    if episode is not None:
        se += f"E{int(episode):02d}"
    return se


def build_pan_keywords(show, season=None, episode=None, extra="", alt_names=None):
    """
    网盘搜索 query：每个剧名 ×「字幕」，最多 2–4 条。
    例：绝命毒师 字幕 · Breaking Bad 字幕 · 绝命毒师 S01E01 字幕
    """
    names = unique_show_names(show, alt_names)
    se = season_episode_tag(season, episode)
    variants = []

    if extra:
        variants.append(extra.strip())

    for name in names:
        variants.append(f"{name} 字幕")
        if se:
            variants.append(f"{name} {se} 字幕")

    return _dedupe(variants)


def build_subtitle_queries(show, alt_names=None):
    """字幕库搜索：英文优先，再中文。各试一次，不做变体。"""
    names = unique_show_names(show, alt_names)
    latin = [n for n in names if is_latin_name(n)]
    other = [n for n in names if not is_latin_name(n)]
    return _dedupe(latin + other)
