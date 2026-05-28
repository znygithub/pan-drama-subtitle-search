# pan-drama-subtitle-search

## 这个工具是干嘛的

**出发点：学外语。**

帮你在网盘里找美剧/英剧，并且为每一集配好**能对轴的字幕**，方便：

- **练听力** — 开英文字幕跟读
- **对照理解** — 中英字幕切换，不懂就查
- **精听某一集** — 指定 S01E05，拿到视频 + 匹配字幕链接

工具会自动：搜多个网盘 → 搜字幕 → 按文件名校对版本（1080p、BluRay/WEB-DL 等）→ 整理成清单。

> **不是观影推荐工具。** 若只是随便看看，请用 Netflix、腾讯视频等正版平台。网盘资源仅供学习，请勿传播。

---

## 用户需要怎么做

### 在 Cursor 里用（推荐）

1. 安装 Skill：
   ```bash
   git clone https://github.com/znygithub/pan-drama-subtitle-search.git
   cp -r pan-drama-subtitle-search ~/.cursor/skills/
   ```

2. 在对话里直接说想看什么，例如：
   > 帮我找《绝命毒师》第一季第一集，带字幕，用来练听力

3. Agent 会自动搜资源、配字幕，按下面格式给你结果。

### 自己跑命令（可选）

**前置：** 字幕搜索需要本地 OpenSubtitles 服务（一次性启动）：

```bash
docker run -d -p 8000:8000 ghcr.io/lavx/opensubtitles-scraper:latest
```

**一条命令搞定搜视频 + 配字幕：**

```bash
python3 scripts/episode_match.py match "绝命毒师" \
  --sub-query "Breaking Bad" \
  --season 1 --episode 1 --top 10
```

你说中文剧名即可；有英文名可以一并告诉 Agent，命中率更高。

---

## 输出格式是怎样的

结果按**三段优先级**排列，每行都是**可点击链接**（不是文件名）：

| 优先级 | 内容 |
|--------|------|
| **一** | 视频 + **同分享内**的字幕文件（`.srt`/`.ass`，最可靠） |
| **二** | 视频 + **配对好的**外挂字幕下载链接 |
| **三** | MKV 可能内封字幕 → 请你先用播放器自查，没有再用外挂 |

网盘链接会标注 `[夸克]` `[阿里]` 等；**有提取码才显示**，没有就不写。

---

### Case 示例：《绝命毒师》S01E01

```markdown
## 《绝命毒师》Breaking Bad · S01E01

> 已搜字幕池 23 条 · 校验网盘 8 个 · 配对 4 行

### 一、视频 + 同分享字幕文件（最优先）

| # | 说明 | 网盘 | 字幕链接 | 匹配度 |
|---|------|------|----------|--------|
| 1 | 1080p WEB-DL 全五季 外挂中英 | [夸克](https://pan.quark.cn/s/xxxx) | [网盘内·中文](https://pan.quark.cn/s/xxxx) · [网盘内·英文](https://pan.quark.cn/s/xxxx) | high |

**#1 匹配说明：** 同分享内有同名 `.mkv` + `.zh.srt` / `.en.srt`，下载后可直接外挂。

### 二、视频 + 配对字幕链接

| # | 说明 | 网盘 | 字幕下载 | 类型 | 匹配度 |
|---|------|------|----------|------|--------|
| 2 | 1080p BluRay x265 单集 | [阿里](https://www.alipan.com/s/yyyy) | [中文](https://www.opensubtitles.org/en/subtitles/123/zh) · [英文](https://www.opensubtitles.org/en/subtitles/456/en) | 中+英（分开） | medium |

**#2 匹配说明：** 视频 BluRay，字幕来自 WEB-DL 版本，时间轴可能需 VLC 微调。

### 三、MKV 可能内封字幕（请你自查）

| # | 说明 | 网盘 | 如何自查 | 若无内封→下载字幕 | 匹配度 |
|---|------|------|----------|-------------------|--------|
| 3 | 1080p 全五季 内封简英 | [夸克](https://pan.quark.cn/s/zzzz) | VLC 字幕→子轨道 | [中文](…) · [英文](…) | low |

**#3 匹配说明：** 标题写「内封」但未验证；备用字幕可能对不上。

---

**📚 学习用途说明**

以上仅供语言学习。若只是观影，建议正版：Netflix / Apple TV+ 等。
```

---

## 更多文档

- [SKILL.md](SKILL.md) — Agent 完整 SOP
- [know-how.md](know-how.md) — 搜词技巧、MKV 自查、常见坑
