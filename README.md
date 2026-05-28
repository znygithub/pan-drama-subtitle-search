# pan-drama-subtitle-search

## 这个工具是干嘛的

**出发点：学外语。**

帮你在网盘里找美剧/英剧，并且为每一集配好**能对轴的字幕**，方便练听力、跟读、中英对照。

> 不是观影推荐。若只是随便看看，请用 Netflix、腾讯视频等正版平台。

---

## 用户需要怎么做

**在 Cursor 里（推荐）：** 安装 Skill 后直接说：

> 帮我找《绝命毒师》第一季第一集，带字幕，练听力

**自己跑命令（可选）：**

```bash
docker run -d -p 8000:8000 ghcr.io/lavx/opensubtitles-scraper:latest

python3 scripts/episode_match.py match "绝命毒师" \
  --sub-query "Breaking Bad" --season 1 --episode 1 --top 10
```

---

## 输出格式（Case：《绝命毒师》S01E01）

每行必须是**可点击链接**。网盘标 `[夸克]`/`[阿里]`；**有提取码才写**。

### 一、视频 + 同分享字幕文件

| # | 说明 | 网盘 | 字幕链接 | 匹配度 |
|:--:|------|:----:|----------|:------:|
| 1 | 1080p WEB-DL 全五季，分享内外挂中英 | [夸克](https://pan.quark.cn/s/xxxx) | [中文](https://pan.quark.cn/s/xxxx) · [英文](https://pan.quark.cn/s/xxxx) | high |

> **#1** 同分享内同名 `.mkv` + `.zh.srt` / `.en.srt`，可直接外挂。

### 二、视频 + 配对字幕链接

| # | 说明 | 网盘 | 字幕下载 | 类型 | 匹配度 |
|:--:|------|:----:|----------|------|:------:|
| 2 | 1080p BluRay x265 单集 | [阿里](https://www.alipan.com/s/yyyy) | [中文](https://www.opensubtitles.org/en/subtitles/123/zh) · [英文](https://www.opensubtitles.org/en/subtitles/456/en) | 中+英 | medium |

> **#2** 视频 BluRay，字幕来自 WEB-DL，时间轴可能需 VLC 微调。

### 三、MKV 可能内封（请你自查）

| # | 说明 | 网盘 | 若无内封→下载字幕 | 匹配度 |
|:--:|------|:----:|-------------------|:------:|
| 3 | 1080p 全五季，标题写内封简英 | [夸克](https://pan.quark.cn/s/zzzz) | [中文](https://www.opensubtitles.org/en/subtitles/123/zh) · [英文](https://www.opensubtitles.org/en/subtitles/456/en) | low |

> **#3** 请用 VLC 查看字幕轨；有内封则不用下外挂。备用字幕可能对不上。

---

**📚 学习用途说明** — 以上仅供语言学习，请勿传播。观影请支持正版（Netflix / Apple TV+ 等）。

---

[SKILL.md](SKILL.md) · [know-how.md](know-how.md)
