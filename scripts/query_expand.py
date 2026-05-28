"""Query expansion for pan + subtitle search (CN/EN, spacing variants, 美剧)."""

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


def latin_name_variants(name):
    """Better Call Saul → BetterCallSaul, Better.Call.Saul, …"""
    name = (name or "").strip()
    if not name:
        return []

    variants = [name]

    # normalize separators to spaces first
    core = re.sub(r"[._]+", " ", name)
    core = re.sub(r"\s+", " ", core).strip()
    if core and core not in variants:
        variants.append(core)

    if " " in core:
        nospace = core.replace(" ", "")
        variants.append(nospace)
        variants.append(core.replace(" ", "."))
        variants.append(core.replace(" ", "_"))

    if "." in name:
        variants.append(name.replace(".", ""))
        variants.append(name.replace(".", " "))

    return _dedupe(variants)


def expand_show_names(*names):
    """All alias variants for show names (中文原样 + 英文多种写法)."""
    out = []
    for n in names:
        n = (n or "").strip()
        if not n:
            continue
        if is_latin_name(n):
            out.extend(latin_name_variants(n))
        else:
            out.append(n)
    return _dedupe(out)


def season_episode_tag(season=None, episode=None):
    if season is None:
        return ""
    se = f"S{int(season):02d}"
    if episode is not None:
        se += f"E{int(episode):02d}"
    return se


def collect_show_aliases(primary, *extra):
    """Merge user input + alt names for CN/EN bidirectional expansion."""
    return _dedupe([primary, *extra])


def build_pan_keywords(show, season=None, episode=None, extra="", alt_names=None):
    """
    PanSou query variants: CN + EN aliases × spacing variants × broad/美剧/字幕.
    """
    names = expand_show_names(*collect_show_aliases(show, *(alt_names or [])))
    se = season_episode_tag(season, episode)
    variants = []

    if extra:
        variants.append(extra.strip())

    for name in names:
        base = f"{name} {se}".strip() if se else name
        # broad first
        if base != name:
            variants.append(base)
        variants.append(name)
        if se:
            variants.append(f"{name} {se}")

        # 美剧 / 字幕 — useful for CN queries and some EN
        if not is_latin_name(name) or len(name) > 3:
            variants.append(f"{name} 美剧")
            if se:
                variants.append(f"{name} {se} 美剧")
        if base:
            variants.extend([
                f"{base} 字幕",
                f"{name} 字幕 mkv",
                f"{name} 中英字幕",
            ])
        else:
            variants.extend([
                f"{name} 字幕 mkv",
                f"{name} 美剧 字幕",
                f"{name} 中英字幕",
            ])

        # English releases often use dots in filenames
        if is_latin_name(name) and " " in name:
            dotted = name.replace(" ", ".")
            if se:
                variants.append(f"{dotted}.{se}")
            variants.append(dotted)

    return _dedupe(variants)


def build_subtitle_queries(show, alt_names=None):
    """CN + EN aliases; Latin spacing variants first, then Chinese."""
    names = expand_show_names(*collect_show_aliases(show, *(alt_names or [])))
    # Latin names first for subtitle DB
    latin = [n for n in names if is_latin_name(n)]
    other = [n for n in names if not is_latin_name(n)]
    return _dedupe(latin + other)
