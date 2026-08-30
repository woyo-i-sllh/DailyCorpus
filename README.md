# DailyCorpus · 每日语料库（热词 + 财经，用于量化）

抓取**当天**各平台热点（大事）与财经消息，生成可量化的语料库，并写入 SQLite 数据库。
运行在自带虚拟环境里，匿名接口、无需登录；DeepSeek 负责每日要闻精选与简析。

## 数据源

| 分类 | 数据源 |
| --- | --- |
| 综合大事 | 抖音热榜、微博热搜、百度热搜、今日头条热榜、B站热词 |
| 社区内容 | B站全站热榜、B站热门视频 |
| 财经消息 | 东方财富7x24、新浪财经7x24、同花顺快讯、华尔街见闻 |

> 雪球（阿里云 WAF）、知乎热榜（需登录）、财联社（接口需签名）匿名抓不了，未接入。

## 首次准备（一次性）

双击 **`setup.bat`**：自动创建虚拟环境 `.venv` 并安装 jieba。

## 每天运行

双击 **`daily_corpus.bat`**（抓取 + AI 精选一起跑），或命令行：

```powershell
cd /d F:\life\DailyCorpus
".venv\Scripts\python.exe" daily_corpus.py                      # 抓今天全部
".venv\Scripts\python.exe" daily_corpus.py --cat 财经            # 只要财经（输出到 corpus/日期_财经/，不覆盖全量）
".venv\Scripts\python.exe" daily_corpus.py --cat 综合,财经       # 只要大事 + 财经
".venv\Scripts\python.exe" daily_corpus.py --skip bilibili       # 跳过某类源
".venv\Scripts\python.exe" daily_corpus.py --top 30              # 每源只保留前 30 条
".venv\Scripts\python.exe" daily_corpus.py --date 2026-08-30     # 指定日期
".venv\Scripts\python.exe" daily_corpus.py --since-hours 24      # 只保留最近24小时财经快讯（定时任务默认）
".venv\Scripts\python.exe" daily_corpus.py --analyze-only corpus/2026-08-30   # 只重算（不重新抓取）
".venv\Scripts\python.exe" ai_digest.py                # DeepSeek 精选要闻 + 简析（读当天数据）
".venv\Scripts\python.exe" ai_digest.py --max-items 10  # 最多精选条数
```

## 产出

### 文件（corpus/YYYY-MM-DD/）

| 文件 | 内容 |
| --- | --- |
| `hotwords.csv` | 热词榜：各平台热词 + 热度分（抖音/微博/百度/头条/B站热词汇总） |
| `finance.csv` | 财经消息表：东财 / 新浪 / 同花顺 / 华尔街见闻 |
| `entries.csv` | 全部条目统一表：分类 / 来源 / 标题 / 简介 / 热度 / 链接 / 时间 |
| `corpus.txt` | 纯文本语料：每行一条，可直接喂 NLP / 词频工具 |
| `wordfreq.csv` | 词频 Top100（jieba 兜底数据，已过滤无意义词） |
| `digest.json` | AI 要闻精选 + **AI 关键词**（DeepSeek 按规则提取约30个，如 吉隆/泥石流/房地产） |
| `stats.json` | 汇总统计：分类热榜 Top10、词频、各源条数 |
| `digest.json` | AI 要闻精选（DeepSeek 输出） |
| `raw/` | 各接口原始 JSON（留档，方便换算法重算） |

CSV 均为 UTF-8 with BOM，Excel 可直接打开不乱码。

### 数据库（corpus.db，SQLite）

每天全量抓取后自动写入（同一天重复运行会**先删后插**，保持幂等）：

| 表 | 内容 |
| --- | --- |
| `entries` | 全部条目：日期/分类/来源/标题/热度/链接/时间 |
| `hotwords` | 热词：日期/来源/关键词/热度 |
| `runs` | 运行记录：每次抓取的时间、条数 |
| `digest` | AI 要闻精选：日期/总结/精选条目（历史留存） |

查询示例（需要 Python 或任意 SQLite 工具）：

```sql
-- 某天财经消息
SELECT source, title, published FROM entries WHERE date='2026-08-30' AND cat='财经' ORDER BY published DESC;
-- 某天热词榜（按热度倒序）
SELECT source, keyword, heat FROM hotwords WHERE date='2026-08-30' ORDER BY heat DESC LIMIT 20;
-- 跨日热词次数统计
SELECT keyword, COUNT(DISTINCT date) AS days FROM hotwords GROUP BY keyword ORDER BY days DESC LIMIT 20;
```

> 说明：`--cat`/`--skip` 过滤运行时输出到带标签的子目录（如 corpus/2026-08-30_财经/），
> **不写入** corpus.db（数据库只保留每天的全量记录）。

## AI 要闻精选（DeepSeek）

用 DeepSeek 从当天语料里筛出**最重要的新闻 + 可能影响股票/市场的消息**，每条附简析
（为什么重要、影响哪些方向/板块），并给一段当日要点总结。**其余条目全部留在 corpus.db**，
作为数据统计基数，随时可在看板其他标签页查询。

- 配置：`config.json` 里填 `deepseek_api_key`（已填好，勿外传）
- 运行：`ai_digest.py`（每天定时任务自动跑，手动版 bat 也会跑）
- 产出：`corpus.db` 的 `digest` 表（含 `keywords_json`）+ `corpus/日期/digest.json`，看板 🤖 和 🧮 标签页读取
- **AI 关键词规则**：只输出有信息量的实义词（地点/人名/机构/板块/事件/公司），
  剔除通用词（公司/中国/同比/净利润/记者/报道/新华社…）、单字、纯数字、无意义缩写；
  约 30 个，按重要度排序，看板 🧮 关键词页优先展示 AI 结果（无 AI 时退回 jieba 词频）

> API Key 只存在 `config.json`，脚本/日志里不会打印。模型默认 `deepseek-chat`，可在 config.json 改 `deepseek_model`。

## 定时自动跑（已配置）

Windows 任务计划里已创建任务 **`DailyCorpus`**：每天 **20:00** 自动运行
`run_scheduled.bat`（用 venv + `--since-hours 24`，覆盖**昨天 20:00 以后**的数据 + AI 精选），
日志写在 `logs\run.log`。

```powershell
# 查看 / 手动触发 / 删除
schtasks /Query /TN DailyCorpus
schtasks /Run /TN DailyCorpus
schtasks /Delete /TN DailyCorpus /F
```

> `--since-hours 24` 的含义：财经快讯（东财/新浪/同花顺/华尔街见闻）只保留最近 24 小时内的条目，
> 这样每天 20:00 跑一次就能覆盖"昨天 20:00 → 今天 20:00"的完整一天；热词/热榜是即时快照，不受影响。

## 数据看板

本地网页，查询每天的大事/财经/热词/词频/AI 精选（读 corpus.db，零依赖）：

双击 **`dashboard.bat`**（自动开浏览器 http://127.0.0.1:8765），或：

```powershell
".venv\Scripts\python.exe" dashboard.py            # 默认 8765 端口
".venv\Scripts\python.exe" dashboard.py --port 9000
```

要闻精选是**每天 20:00 定时预计算好**存进 `digest` 表的，打开看板直接读取、秒开（服务端带 60 秒缓存）。

功能：默认打开 **🤖 要闻精选**（AI 筛出的当日要闻 + 简析 + 影响板块），另有
🔥大事 / 📰财经 / 🎬社区 / 🔑热词 / 🧮关键词（AI 提取）/ 📋全部 标签页、按标题搜索、热度自动格式化（万/亿）、点击标题直达原文。

## 小贴士

- 手动跑一次完整流程：双击 `daily_corpus.bat`（抓取 → 数据库 → AI 精选）
- 想看历史某天：看板里选日期，或 `ai_digest.py --date 2026-08-29`
- 想换分词器或重装环境：删除 `.venv` 后重新双击 `setup.bat`
