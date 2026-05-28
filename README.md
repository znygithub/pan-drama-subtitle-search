# pan-drama-subtitle-search

Cursor Agent Skill：帮语言学习者搜索网盘剧集视频，并为每个视频配对能对轴的字幕。

## 功能

- 多网盘搜索（PanSou 聚合：夸克 / 阿里 / 百度等）
- Query 泛化（中↔英、空格/点号变体）
- 字幕搜索（OpenSubtitles via [opensubtitles-scraper](https://github.com/LavX/opensubtitles-scraper)）
- 逐视频版本校对（SxxExx、1080p、BluRay/WEB-DL 等）

## 快速开始

```bash
# 字幕服务（可选，本地）
docker run -d -p 8000:8000 ghcr.io/lavx/opensubtitles-scraper:latest

# 一键：搜视频 + 配字幕
python3 scripts/episode_match.py match "绝命毒师" \
  --sub-query "Breaking Bad" --season 1 --episode 1 --top 10
```

## 文档

- [SKILL.md](SKILL.md) — Agent SOP（五步流程 + 输出规范）
- [know-how.md](know-how.md) — Query 泛化、对轴、常见坑

## 安装为 Cursor Skill

```bash
cp -r . ~/.cursor/skills/pan-drama-subtitle-search
```

## 免责声明

仅供语言学习使用。请支持正版流媒体。
