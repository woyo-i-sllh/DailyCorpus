# -*- coding: utf-8 -*-
"""
每日语料库：抓取各平台当日热点（大事）与财经消息，生成可量化的语料库。

数据源（全部匿名接口，无需登录）：
  综合大事:  抖音热榜 / 微博热搜 / 百度热搜 / 今日头条热榜 / B站热词
  社区内容:  B站全站热榜 / B站热门视频
  财经消息:  东方财富7x24 / 新浪财经7x24 / 同花顺快讯 / 华尔街见闻

用法：
    python daily_corpus.py                        # 抓今天全部
    python daily_corpus.py --cat 综合,财经          # 只抓综合大事 + 财经
    python daily_corpus.py --cat 财经 --top 20     # 只要财经，每源前20条
    python daily_corpus.py --skip bilibili         # 跳过某类源
    python daily_corpus.py --date 2026-08-30
    python daily_corpus.py --analyze-only corpus/2026-08-30   # 只重算统计

输出到 corpus/YYYY-MM-DD/：
    raw/           各接口原始 JSON（留档，可复现）
    hotwords.csv   热词榜（抖音/微博/百度/头条/B站热词 汇总）
    finance.csv    财经消息表（东财/新浪/同花顺/华尔街见闻）
    entries.csv    全部条目统一表（来源/分类/标题/热度/链接/时间）
    corpus.txt     纯文本语料（每行一条）
    wordfreq.csv   词频 Top100（有 jieba 自动用 jieba，否则 n-gram 兜底）
    stats.json     汇总统计（按分类的热词榜、词频、热度指标）

仅依赖 Python 标准库。
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

BILI_TZ = timezone(timedelta(hours=8))  # 东八区

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def setup_stdout():
    """让控制台正确显示中文（独立运行，不依赖其他模块）。"""
    try:
        if sys.stdout is not None and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            enc = sys.stdout.encoding or "utf-8"
            sys.stdout.reconfigure(encoding=enc, errors="replace")
            if sys.stderr is not None:
                sys.stderr.reconfigure(encoding=enc, errors="replace")
        else:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            if sys.stderr is not None:
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(msg: str):
    now = datetime.now(BILI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


VERSION = "2.0.0"
CORPUS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus.db")

UA_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

TAG_RE = re.compile(r"<[^>]+>")
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*")
STOP_CHARS = set("的了是在有我你他她它们这那和与就都而及或一个不也把被从到去说很上时吧吗呢啊哦呀")


def clean_text(s):
    if not s:
        return ""
    s = TAG_RE.sub("", str(s))
    return re.sub(r"\s+", " ", s).strip()


# ---------------- 基础抓取 ----------------

def http_get(url, referer, timeout=15):
    h = dict(UA_HEADERS)
    h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def fetch_with_retry(name, url, referer, attempts=3, base_delay=2.0, raw=False):
    """带退避重试；返回 (ok, data)。data 为 dict(json) 或原始文本。"""
    for i in range(1, attempts + 1):
        try:
            raw_text = http_get(url, referer)
            if raw:
                return True, raw_text
            data = json.loads(raw_text)
            raw_code = data.get("code") if isinstance(data, dict) else None
            try:
                code = int(raw_code)
            except (TypeError, ValueError):
                code = raw_code
            if code == -352:
                log(f"  ⚠ [{name}] 风控校验失败(-352)，跳过")
                return False, None
            if isinstance(data, dict) and code not in (None, 0, 200, 20000):
                log(f"  ⚠ [{name}] 接口 code={code} {data.get('message', '')}，跳过")
                return False, None
            return True, data
        except Exception as e:
            if i < attempts:
                time.sleep(base_delay * i)
            else:
                log(f"  ✗ [{name}] 抓取失败：{e}")
    return False, None


# ---------------- 各数据源 ----------------
# 每个 fetch 返回 [(rank, title, desc, heat, url, published), ...]

def fetch_bili_hotword():
    ok, d = fetch_with_retry("B站热词", "https://s.search.bilibili.com/main/hotword",
                             "https://www.bilibili.com/")
    if not ok:
        return []
    out = []
    for i, h in enumerate(d.get("list", []), 1):
        kw = clean_text(h.get("keyword") or h.get("show_name") or "")
        if kw:
            out.append((i, kw, "", h.get("heat_score") or 0, "", 0))
    return out


def fetch_bili_ranking():
    ok, d = fetch_with_retry("B站全站热榜", "https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all",
                             "https://www.bilibili.com/")
    if not ok:
        return []
    out = []
    for i, v in enumerate(d.get("data", {}).get("list", []), 1):
        st = v.get("stat") or {}
        out.append((i, clean_text(v.get("title", "")), clean_text(v.get("desc", "")),
                    st.get("view") or 0, v.get("short_link_v2") or f"https://www.bilibili.com/video/{v.get('bvid', '')}",
                    v.get("pubdate") or 0))
    return out


def fetch_bili_popular(pages=2, per_page=50):
    out = []
    for pn in range(1, pages + 1):
        ok, d = fetch_with_retry(f"B站热门视频 p{pn}",
                                 f"https://api.bilibili.com/x/web-interface/popular?ps={per_page}&pn={pn}",
                                 "https://www.bilibili.com/")
        if ok:
            for v in d.get("data", {}).get("list", []):
                st = v.get("stat") or {}
                out.append((len(out) + 1, clean_text(v.get("title", "")), clean_text(v.get("desc", "")),
                            st.get("view") or 0,
                            v.get("short_link_v2") or f"https://www.bilibili.com/video/{v.get('bvid', '')}",
                            v.get("pubdate") or 0))
        if pn < pages:
            time.sleep(0.8)
    return out


def fetch_douyin():
    ok, d = fetch_with_retry("抖音热榜", "https://www.douyin.com/aweme/v1/web/hot/search/list/",
                             "https://www.douyin.com/")
    if not ok:
        return []
    out = []
    for i, w in enumerate(d.get("data", {}).get("word_list", []), 1):
        word = clean_text(w.get("word", ""))
        if word:
            out.append((i, word, "", w.get("hot_value") or 0, "", w.get("event_time") or 0))
    return out


def fetch_weibo():
    ok, d = fetch_with_retry("微博热搜", "https://weibo.com/ajax/side/hotSearch", "https://weibo.com/")
    if not ok:
        return []
    out = []
    for i, w in enumerate(d.get("data", {}).get("realtime", []), 1):
        word = clean_text(w.get("word", ""))
        if word:
            out.append((i, word, "", w.get("num") or 0, w.get("word_scheme") or "", 0))
    return out


def fetch_baidu():
    ok, d = fetch_with_retry("百度热搜",
                             "https://top.baidu.com/api/board?platform=wise&tab=realtime",
                             "https://top.baidu.com/")
    if not ok:
        return []
    out = []
    i = 0
    for card in d.get("data", {}).get("cards", []):
        for group in card.get("content", []):
            items = group.get("content", []) if isinstance(group, dict) else [group]
            for it in items:
                if isinstance(it, dict) and it.get("word"):
                    i += 1
                    out.append((i, clean_text(it["word"]), "",
                                it.get("hotScore") or 0, it.get("url") or "", 0))
    return out


def fetch_toutiao():
    ok, d = fetch_with_retry("头条热榜",
                             "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
                             "https://www.toutiao.com/")
    if not ok:
        return []
    out = []
    for i, it in enumerate(d.get("data", []), 1):
        title = clean_text(it.get("Title", ""))
        if title:
            out.append((i, title, "", it.get("HotValue") or 0, it.get("Url") or "", 0))
    return out


def fetch_eastmoney(pages=3):
    out = []
    for page in range(1, pages + 1):
        ok, raw = fetch_with_retry(f"东方财富7x24 p{page}",
                                   f"https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_{page}_.html",
                                   "https://kuaixun.eastmoney.com/", raw=True)
        if not ok:
            continue
        m = re.search(r"var ajaxResult=(\{.*\})", raw, re.S)
        if not m:
            log("  ⚠ [东方财富7x24] 返回格式异常，跳过")
            continue
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for it in d.get("LivesList", []):
            title = clean_text(it.get("title", ""))
            if title:
                out.append((len(out) + 1, title, clean_text(it.get("digest", "")), 0,
                            it.get("url_w") or "", int(it.get("sort") or 0) // 1_000_000))
        if page < pages:
            time.sleep(0.6)
    return out


def fetch_sina(pages=2, per_page=50):
    out = []
    for page in range(1, pages + 1):
        ok, d = fetch_with_retry(f"新浪财经7x24 p{page}",
                                 f"https://zhibo.sina.com.cn/api/zhibo/feed?page={page}&page_size={per_page}&zhibo_id=152",
                                 "https://finance.sina.com.cn/7x24/")
        if ok:
            lst = d.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
            for it in lst:
                text = clean_text(it.get("rich_text", ""))
                if text:
                    ts = 0
                    try:
                        ts = int(datetime.strptime(it["create_time"], "%Y-%m-%d %H:%M:%S").timestamp())
                    except Exception:
                        pass
                    out.append((len(out) + 1, text, "", 0, it.get("docurl") or "", ts))
        if page < pages:
            time.sleep(0.6)
    return out


def fetch_ths(pages=2, per_page=50):
    out = []
    for page in range(1, pages + 1):
        ok, d = fetch_with_retry(f"同花顺快讯 p{page}",
                                 f"https://news.10jqka.com.cn/tapp/news/push/stock/?page={page}&tag=&track=website&pagesize={per_page}",
                                 "https://www.10jqka.com.cn/")
        if ok:
            for it in d.get("data", {}).get("list", []):
                title = clean_text(it.get("title", ""))
                if title:
                    out.append((len(out) + 1, title, clean_text(it.get("digest", "")), 0,
                                it.get("url") or "", int(it.get("ctime") or 0)))
        if page < pages:
            time.sleep(0.6)
    return out


def fetch_wallstreetcn():
    ok, d = fetch_with_retry("华尔街见闻",
                             "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=50",
                             "https://wallstreetcn.com/")
    if not ok:
        return []
    out = []
    for i, it in enumerate(d.get("data", {}).get("items", []), 1):
        title = clean_text(it.get("title") or it.get("content_text") or "")
        if title:
            uri = it.get("uri") or ""
            if uri and not uri.startswith("http"):
                uri = "https://wallstreetcn.com" + uri
            out.append((i, title, "", 0, uri, int(it.get("display_time") or 0)))
    return out


# 源注册表：key -> (名称, 分类, 是否进热词表, fetch 函数, 每源上限)
SOURCES = [
    ("douyin_hot",   "抖音热榜",     "综合", True,  fetch_douyin),
    ("weibo_hot",    "微博热搜",     "综合", True,  fetch_weibo),
    ("baidu_hot",    "百度热搜",     "综合", True,  fetch_baidu),
    ("toutiao_hot",  "头条热榜",     "综合", True,  fetch_toutiao),
    ("bili_hotword", "B站热词",      "综合", True,  fetch_bili_hotword),
    ("bili_ranking", "B站全站热榜",  "社区", False, fetch_bili_ranking),
    ("bili_popular", "B站热门视频",  "社区", False, fetch_bili_popular),
    ("em_kuaixun",   "东方财富7x24", "财经", False, fetch_eastmoney),
    ("sina_zhibo",   "新浪财经7x24", "财经", False, fetch_sina),
    ("ths_kuaixun",  "同花顺快讯",   "财经", False, fetch_ths),
    ("wallstreetcn", "华尔街见闻",   "财经", False, fetch_wallstreetcn),
]
CATS = {"综合", "社区", "财经"}


# ---------------- 词频 ----------------

def has_jieba():
    try:
        import jieba  # noqa: F401
        return True
    except Exception:
        return False


# 词频要过滤掉的"无意义"词（单字、新闻套话、财报万能词、平台口水词）
STOPWORDS = set("""的 了 是 在 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 自己 这 那 而 及 与 等 从 被 把 但 并 且 或 因为 所以 如果 然后 目前 今日 今日讯 记者 报道 新华社 中新社 消息 快讯 资讯 发布 相关 通过 进行 表示 提供 推出 显示 我们 你们 他们 这个 那个 这些 那些 什么 怎么 如何 为什么 以及 由于 年 月 日 时 中 上 下 后 前 内 外 大 小 新 三连 求三连 三连求 一键三连 UP主 视频 网友 内容 更多 关注 最新 今天 明天 昨天 本次 日前 近日 日电 讯 快报
公司 上市公司 股东 净利润 同比 亿元 万元 收入 营业 归属于 实现 报告 增长 下降 上半年 半年度 年度 一季度 二季度 三季度 四季度 公告 披露 公布 业绩 股份 有限公司 集团 有限 责任 董事会 监事会 中国 美国 国际 国内 全国 全球 根据 包括 同时 此外 关于 其中 主要 重要 正式 首次 再次 出现 开展 加强 继续 目前 方面 情况 问题 影响 市场 板块 行业 相关 领域 水平 数据 亿元 较上年 万元 归属于上市公司 本次 此次 日前 近日 今日 昨日 上周 本期 报告期
SH SZ 央视 新闻 发展 工作 持续 时间 开始 发生 当地 区域 同期 超过 国家 表示 指出 认为 提出 强调 记者 编辑 来源 综合 记者站 通讯员""".split())


def tokenize(text, use_jieba):
    tokens = []
    if use_jieba:
        import jieba
        for t in jieba.lcut(text):
            t = t.strip()
            if not t:
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]+", t):
                # 单字（年/月/日/中/为…）和停用词（新华社/记者/报道…）不要
                if len(t) >= 2 and t not in STOPWORDS:
                    tokens.append(t)
            elif re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]*", t) and len(t) >= 2:
                # 纯数字（2026/30/16…）和 SH/SZ 这类代码后缀不要，保留 AI/GPU/MiniLED 这类
                if not re.fullmatch(r"[\d.\-]+", t) and t not in STOPWORDS and t.lower() not in STOPWORDS:
                    tokens.append(t)
    else:
        for run in CJK_RE.findall(text):
            if len(run) == 1:
                if run not in STOP_CHARS:
                    tokens.append(run)
            else:
                for i in range(len(run) - 1):
                    bg = run[i:i + 2]
                    if not any(c in STOP_CHARS for c in bg):
                        tokens.append(bg)
        tokens.extend(t for t in LATIN_RE.findall(text) if len(t) >= 2)
    return tokens


def build_wordfreq(lines, top_n=100):
    use_jieba = has_jieba()
    if use_jieba:
        import jieba
        jieba.setLogLevel(60)
    c = Counter()
    for line in lines:
        for t in tokenize(line, use_jieba):
            c[t] += 1
    rows = [{"rank": i + 1, "word": w, "count": n,
             "type": "jieba" if use_jieba else ("bigram" if re.fullmatch(r"[\u4e00-\u9fff]{2}", w) else "other")}
            for i, (w, n) in enumerate(c.most_common(top_n))]
    return rows, use_jieba


# ---------------- 落盘与统计 ----------------

def write_csv(path, rows, fieldnames):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


ENTRY_FIELDS = ["rank", "cat", "source", "title", "desc", "heat", "url", "published"]
HOTWORD_FIELDS = ["rank", "source", "keyword", "heat"]


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2 if xs else 0


# ---------------- 数据库记录 ----------------

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    cat TEXT,
    source TEXT,
    rank INTEGER,
    title TEXT,
    desc TEXT,
    heat INTEGER,
    url TEXT,
    published INTEGER,
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date);
CREATE INDEX IF NOT EXISTS idx_entries_cat ON entries(cat);
CREATE TABLE IF NOT EXISTS hotwords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    source TEXT,
    rank INTEGER,
    keyword TEXT,
    heat INTEGER
);
CREATE INDEX IF NOT EXISTS idx_hotwords_date ON hotwords(date);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    fetched_at TEXT,
    total_entries INTEGER,
    note TEXT
);
"""


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(DB_SCHEMA)
    con.commit()
    return con


def save_db(date_str, entries, hotwords, fetched_at, note=""):
    """把当天的条目写入 SQLite（同一天重复运行会先删后插，保持幂等）。"""
    con = init_db()
    try:
        con.execute("DELETE FROM entries WHERE date=?", (date_str,))
        con.execute("DELETE FROM hotwords WHERE date=?", (date_str,))
        con.executemany(
            "INSERT INTO entries (date, cat, source, rank, title, desc, heat, url, published, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(date_str, e["cat"], e["source"], e["rank"], e["title"], e["desc"],
              e["heat"], e["url"], e["published"], fetched_at) for e in entries])
        con.executemany(
            "INSERT INTO hotwords (date, source, rank, keyword, heat) VALUES (?,?,?,?,?)",
            [(date_str, h["source"], h["rank"], h["keyword"], h["heat"]) for h in hotwords])
        con.execute("INSERT INTO runs (date, fetched_at, total_entries, note) VALUES (?,?,?,?)",
                    (date_str, fetched_at, len(entries), note))
        con.commit()
    finally:
        con.close()


def run(day_dir, date_str, top, cats, skip, popular_pages, analyze_only=False, write_db=True, since_hours=None):
    raw_dir = os.path.join(day_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    selected = [(k, name, cat, is_hot, fn)
                for k, name, cat, is_hot, fn in SOURCES
                if cat in cats and k not in skip]

    if not analyze_only:
        log(f"== 开始抓取 {date_str} 语料（{len(selected)} 个数据源）==")

    raw_files = {}
    for k, name, cat, is_hot, fn in selected:
        raw_path = os.path.join(raw_dir, k + ".json")
        if analyze_only and os.path.exists(raw_path):
            with open(raw_path, encoding="utf-8") as f:
                raw_files[k] = json.load(f)
        elif not analyze_only:
            entries = fn()
            if not entries:
                log(f"  - {name}：0 条")
                raw_files[k] = {"date": date_str, "count": 0, "list": []}
                continue
            log(f"  ✓ {name}：{len(entries)} 条")
            raw_files[k] = {"date": date_str, "fetched_at": datetime.now(BILI_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                            "list": [{"rank": r, "title": t, "desc": dsc, "heat": h, "url": u, "published": p}
                                     for r, t, dsc, h, u, p in entries]}
            save_json(raw_path, raw_files[k])
            time.sleep(0.4)

    # 汇总条目
    entries, hotwords = [], []
    for k, name, cat, is_hot, fn in selected:
        if k not in raw_files:
            continue
        lst = raw_files[k].get("list", [])
        for item in lst[:top]:
            entries.append({"rank": item.get("rank", ""), "cat": cat, "source": name,
                            "title": clean_text(item.get("title", "")),
                            "desc": clean_text(item.get("desc", "")),
                            "heat": int(item.get("heat") or 0),
                            "url": item.get("url", ""),
                            "published": int(item.get("published") or 0)})
            if is_hot:
                hotwords.append({"rank": item.get("rank", ""), "source": name,
                                 "keyword": clean_text(item.get("title", "")),
                                 "heat": int(item.get("heat") or 0)})

    # --since-hours：只对财经快讯按时间过滤（热词/榜单是即时快照，不受影响）
    if since_hours:
        cutoff = int(time.time()) - since_hours * 3600
        entries = [e for e in entries
                   if e["cat"] != "财经" or e["published"] == 0 or e["published"] >= cutoff]

    # 落盘表格
    write_csv(os.path.join(day_dir, "entries.csv"), entries, ENTRY_FIELDS)
    write_csv(os.path.join(day_dir, "hotwords.csv"), hotwords, HOTWORD_FIELDS)
    finance = [e for e in entries if e["cat"] == "财经"]
    if finance:
        write_csv(os.path.join(day_dir, "finance.csv"), finance, ENTRY_FIELDS)

    # 纯文本语料
    corpus_lines = []
    for e in entries:
        if e["title"]:
            corpus_lines.append(e["title"])
        if e["desc"]:
            corpus_lines.append(e["desc"])
    with open(os.path.join(day_dir, "corpus.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(corpus_lines) + ("\n" if corpus_lines else ""))

    # 词频
    wordfreq_rows, used_jieba = build_wordfreq(corpus_lines)
    write_csv(os.path.join(day_dir, "wordfreq.csv"), wordfreq_rows, ["rank", "word", "count", "type"])

    # 统计
    stats = {"date": date_str, "version": VERSION,
             "fetched_at": datetime.now(BILI_TZ).strftime("%Y-%m-%d %H:%M:%S"),
             "sources": {k: {"name": name, "cat": cat, "count": len(raw_files.get(k, {}).get("list", []))}
                         for k, name, cat, is_hot, fn in selected},
             "category_counts": dict(Counter(e["cat"] for e in entries)),
             "hotwords": hotwords,
             "finance": finance[:top],
             "wordfreq_top": wordfreq_rows}
    for cat in CATS:
        pool = [e for e in entries if e["cat"] == cat]
        if pool:
            top_items = sorted(pool, key=lambda e: e["heat"], reverse=True)[:10]
            stats[f"top_{cat}"] = [{"rank": i + 1, "source": e["source"], "title": e["title"][:80],
                                    "heat": e["heat"], "url": e["url"]} for i, e in enumerate(top_items)]
    save_json(os.path.join(day_dir, "stats.json"), stats)
    if write_db and entries:
        save_db(date_str, entries, hotwords, stats["fetched_at"],
                note="full" if not analyze_only else "analyze-only")
        log(f"数据库已更新：entries={len(entries)}，hotwords={len(hotwords)}（{DB_PATH}）")
    log(f"已保存到 {day_dir}/（{len(entries)} 条，{len(corpus_lines)} 行语料，词频{'jieba' if used_jieba else 'n-gram兜底'}）")
    print_summary(stats)
    return stats


def print_summary(stats):
    print("\n" + "=" * 66)
    print(f"📅 {stats['date']} 每日语料库速览（{stats['fetched_at']}）")
    print("=" * 66)
    for cat in ("综合", "社区", "财经"):
        key = f"top_{cat}"
        if key not in stats:
            continue
        print(f"\n{'🔥' if cat == '综合' else '📰' if cat == '财经' else '🎬'} {cat} Top10：")
        for it in stats[key]:
            heat = f"  heat={it['heat']:,}" if it["heat"] else ""
            print(f"  {it['rank']:>2}. [{it['source']}] {it['title'][:44]}{heat}")
    print("\n🧮 词频 Top15：")
    for r in stats["wordfreq_top"][:15]:
        print(f"  {r['rank']:>3}. {r['word']}  ×{r['count']}")
    print("=" * 66)


def main():
    setup_stdout()
    ap = argparse.ArgumentParser(description="每日语料库：平台热榜（大事）+ 财经快讯")
    ap.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认今天（东八区）")
    ap.add_argument("--cat", default="综合,社区,财经", help="要抓的分类，逗号分隔：综合,社区,财经")
    ap.add_argument("--skip", default="", help="要跳过的源 key，逗号分隔")
    ap.add_argument("--top", type=int, default=50, help="每个源保留的条目数（默认 50）")
    ap.add_argument("--popular-pages", type=int, default=2, help="B站热门视频页数（默认 2）")
    ap.add_argument("--since-hours", type=int, default=None,
                    help="只保留最近 N 小时的财经快讯（任务计划建议 24）")
    ap.add_argument("--analyze-only", default=None, metavar="DIR_OR_DATE", help="只根据 raw/ 重算")
    args = ap.parse_args()

    if args.analyze_only:
        if os.path.isdir(args.analyze_only):
            day_dir = args.analyze_only.rstrip("/\\")
            date_str = os.path.basename(day_dir)
        else:
            date_str = args.analyze_only
            day_dir = os.path.join(CORPUS_ROOT, date_str)
        cats = set(c.strip() for c in args.cat.split(",") if c.strip())
        skip = set(c.strip() for c in args.skip.split(",") if c.strip())
        write_db = "_" not in os.path.basename(day_dir)
        run(day_dir, date_str, args.top, cats, skip, args.popular_pages, analyze_only=True, write_db=write_db, since_hours=args.since_hours)
        return

    date_str = args.date or datetime.now(BILI_TZ).strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        raise SystemExit("日期格式应为 YYYY-MM-DD，如 2026-08-30")
    cats = set(c.strip() for c in args.cat.split(",") if c.strip())
    skip = set(c.strip() for c in args.skip.split(",") if c.strip())
    unknown = cats - CATS
    if unknown:
        raise SystemExit(f"未知分类：{unknown}，可选 {sorted(CATS)}")
    # 非默认全量时，输出到带标签的子目录，避免覆盖完整语料
    tag = ""
    if cats != CATS or skip:
        tag = "_" + "_".join(sorted(cats))
        if skip:
            tag += "_skip-" + "-".join(sorted(skip))
    day_dir = os.path.join(CORPUS_ROOT, date_str + tag)
    run(day_dir, date_str, args.top, cats, skip, args.popular_pages, write_db=(tag == ""), since_hours=args.since_hours)


if __name__ == "__main__":
    main()
