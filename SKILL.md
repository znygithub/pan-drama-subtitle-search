---
name: pan-drama-subtitle-search
description: >-
  Language-learning SOP: query expansion, pan_drama_search for videos,
  subtitle_search + episode_match for pairing. Learning use only.
  Trigger on 找剧/搜剧/带字幕/学英语/练听力.
---

# 带字幕剧集网盘搜索

帮学外语的人找「视频 + 能对轴字幕」。**仅用于语言学习**，不是观影推荐。

脚本目录：`~/.cursor/skills/pan-drama-subtitle-search/scripts/`

---

## 步骤 1：用户输入 + Query 泛化

**Agent 做，不跑脚本。**

从用户话里提取剧名、季、集。然后 **Query 泛化**（尽可能多）：

- 中文 ↔ 英文互补（用户只给一种，你补另一种）
- 英文变体：有空格 / 无空格 / 点号（`Breaking Bad` / `BreakingBad` / `Breaking.Bad`）
- 可加：季集 `S01E01`、`美剧`、`字幕`

泛化规则详见 [know-how.md § Query 泛化](know-how.md#query-泛化)。

传给后续脚本的参数：`show`（主名）+ `--sub-query`（别名）。

---

## 步骤 2：搜视频

**脚本：`pan_drama_search.py`**

```bash
python3 ~/.cursor/skills/pan-drama-subtitle-search/scripts/pan_drama_search.py search "绝命毒师" \
  --sub-query "Breaking Bad" \
  --season 1 --episode 1 \
  --top 10 --limit 80 \
  --no-require-subtitle
```

| 参数 | 说明 |
|------|------|
| `show` | 主剧名 |
| `--sub-query` | 别名（中/英都传，脚本自动泛化多种 query） |
| `--season` / `--episode` | 季集 |
| `--top` | 返回条数，默认 10 |
| `--limit` | 搜索候选池，默认 80 |
| `--drives` | 仅用户指定时才加，如 `quark,aliyun` |
| `--no-require-subtitle` | 先广搜视频，字幕在步骤 3 配 |

**做什么：** 调 PanSou 聚合搜夸克/阿里/百度等 → 多 query 合并去重 → **校验链接**（失效丢弃）。

**返回看：** `results[]`（含 `url`、`disk_type`、`note`、`validation`）、`keywords_tried`。

调试：`pan_drama_search.py preflight` 检查 PanSou 是否可用。

---

## 步骤 3：搜字幕 + 与视频配对校对

**脚本：`subtitle_search.py`（搜字幕池）+ `episode_match.py`（逐视频配对）**

### 3a 搜该集字幕

```bash
python3 ~/.cursor/skills/pan-drama-subtitle-search/scripts/subtitle_search.py find-episode "Breaking Bad" \
  --sub-query "绝命毒师" \
  --season 1 --episode 1 \
  --languages zh,en --top 50
```

依赖本地 `opensubtitles-scraper`（`:8000`）。先检查：

```bash
python3 ~/.cursor/skills/pan-drama-subtitle-search/scripts/subtitle_search.py preflight
```

**返回看：** `subtitles[]`（字幕候选池）、`query_used`、`queries_tried`。

### 3b 逐视频配对 + 版本校对

对每个步骤 2 的视频，在字幕池里用 `match_score` 选最能对轴的：

| 信号 | 加分 |
|------|------|
| SxxExx 一致 | +40 |
| 1080p 等分辨率一致 | +15 |
| WEB-DL / BluRay 一致 | +12 |
| 发布组一致 | +8 |
| 文件名重叠 | +20 |
| BluRay↔WEB 交叉 | **-8** |

同分享内已有 `.srt`/`.ass` 优先于 OpenSubtitles。

**生产用法（2+3+4 一步跑）：**

```bash
python3 ~/.cursor/skills/pan-drama-subtitle-search/scripts/episode_match.py match "绝命毒师" \
  --sub-query "Breaking Bad" --season 1 --episode 1 --top 10
```

内部依次调 `pan_drama_search.py search` → `subtitle_search.py find-episode` → 逐视频 `match_score` 配对。

**返回看：** `pair_rows[]`（每行视频+字幕+`match_confidence`+`match_reasons`）、`subtitle_service`（是否为 `ok`）。

---

## 步骤 4：整理输出（统一格式）

优先直接用 `episode_match.py` 返回的 `display_sections`；手工整理时必须遵守本规范。

### 4.1 结构（固定顺序）

```
标题（剧名 + 季集）
├── 搜索摘要（可选，1 行）
├── 一、视频 + 同分享字幕文件
├── 二、视频 + 配对字幕链接
├── 三、MKV 可能内封字幕
└── 学习提醒 + 正版指路（步骤 5）
```

某段无结果 → **整段省略**，不要留空表。

### 4.2 字段规范

| 规则 | 说明 |
|------|------|
| 链接 | 必须是可点击 URL，禁止只给文件名 |
| 网盘 | 链接文字用 `[夸克]` `[阿里]` `[百度]` 等，禁止只写「打开」 |
| 提取码 | **仅当存在时**加「提取码」列；无码不加列、不写「无」 |
| 字幕 | **一、二段每行必须有字幕链接**；三段 MKV 右侧也要有备用字幕链接 |
| 匹配度 | `high` / `medium` / `low`，low 须配一句「可能对不上」说明 |
| 说明 | 每行表格下方可跟 1 行 `匹配说明`，来自 `note` + `match_reasons` |

### 4.3 表格列（固定）

**一段（同分享字幕）：**

| # | 说明 | 网盘 | 字幕链接 | 匹配度 |

**二段（配对字幕）：**

| # | 说明 | 网盘 | 字幕下载 | 类型 | 匹配度 |

**三段（MKV 内封）：**

| # | 说明 | 网盘 | 如何自查 | 若无内封→下载字幕 | 匹配度 |

有提取码时，在「网盘」后插入 `| 提取码 |` 列。

### 4.4 完整 Case：《绝命毒师》S01E01

```markdown
## 《绝命毒师》Breaking Bad · S01E01

> 已搜字幕池 23 条 · 校验网盘 8 个 · 配对 4 行

### 一、视频 + 同分享字幕文件（最优先）

> 分享内已有 `.srt` / `.ass`，与视频同目录，对轴最可靠。

| # | 说明 | 网盘 | 字幕链接 | 匹配度 |
|---|------|------|----------|--------|
| 1 | 1080p WEB-DL 全五季 外挂中英 | [夸克](https://pan.quark.cn/s/xxxx) | [网盘内·中文](https://pan.quark.cn/s/xxxx) · [网盘内·英文](https://pan.quark.cn/s/xxxx) | high |

**#1 匹配说明：** 同分享内有 `Breaking.Bad.S01E01.1080p.WEB-DL.mkv` + 同名 `.zh.srt` / `.en.srt`，可直接外挂。

### 二、视频 + 配对字幕链接

> 视频与字幕经 OpenSubtitles 按文件名校对；下载视频后加载同行字幕。

| # | 说明 | 网盘 | 字幕下载 | 类型 | 匹配度 |
|---|------|------|----------|------|--------|
| 2 | 1080p BluRay x265 单集 | [阿里](https://www.alipan.com/s/yyyy) | [中文](https://www.opensubtitles.org/en/subtitles/123/zh) · [英文](https://www.opensubtitles.org/en/subtitles/456/en) | 中+英（分开） | medium |

**#2 匹配说明：** 视频 BluRay 1080p，字幕来自 WEB-DL 版本（`version-mismatch`），集数对，时间轴可能需 VLC 微调。

### 三、MKV 可能内封字幕（请你自查）

> 可能已有内封字幕。请先 [自查](know-how.md#自查方法)——**有内封不用下外挂**；没有再用右侧链接。

| # | 说明 | 网盘 | 如何自查 | 若无内封→下载字幕 | 匹配度 |
|---|------|------|----------|-------------------|--------|
| 3 | 1080p 全五季 内封简英 | [夸克](https://pan.quark.cn/s/zzzz) | [自查方法](know-how.md#自查方法) | [中文](https://www.opensubtitles.org/en/subtitles/123/zh) · [英文](https://www.opensubtitles.org/en/subtitles/456/en) | low |

**#3 匹配说明：** 分享标题写「内封简英」但未验证；备用字幕仅按 S01E01 匹配，版本可能对不上。

---

**📚 学习用途说明**

以上仅供**语言学习**（练听力、跟读、字幕对照），请勿传播。

若只是观影娱乐，建议正版：**国内** 暂无统一流媒体全集，可搜哔哩哔哩部分片段；**国际** Netflix / Apple TV+ 等。
```

### 4.5 禁止

- ❌ 只给视频列表、字幕另起一段让用户自己配
- ❌ 一段/二段某行没有字幕列
- ❌ 提取码写「无」
- ❌ 网盘链接不标注平台名
- ❌ 把 MKV 整季合集放最上却不给字幕

---

## 步骤 5：学习提醒 + 正版指路

输出末尾**必须加**：

> 以上仅供**语言学习**，请勿传播。纯观影请支持正版——  
> **国内：** {查到的平台，如腾讯/优酷/爱奇艺/B站，没有则说明}  
> **国际：** Netflix / Apple TV+ 等

Agent 主动查该剧国内是否有正版。

---

## 参考

[know-how.md](know-how.md) — Query 泛化细节、MKV 自查、常见坑
