# -*- coding: utf-8 -*-
"""
AI 要闻精选：用 DeepSeek 从当天语料里筛出最重要 / 可能影响股票市场的消息，并给简析。
其余条目继续留在 corpus.db，作为数据统计基数。

用法：
  python ai_digest.py                  # 处理今天
  python ai_digest.py --date 2026-08-30
  python ai_digest.py --max-items 10   # 最多精选多少条

产出：
  - corpus.db 的 digest 表（历史留存，看板读取）
  - corpus/YYYY-MM-DD/digest.json
"""

import argparse
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "corpus.db")
CORPUS_ROOT = os.path.join(BASE_DIR, "corpus")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
BILI_TZ = timezone(timedelta(hours=8))


def setup_stdout():
    """控制台 UTF-8 兼容，避免中文/emoji 打印报错。"""
    import sys
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DIGEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS digest (
    date TEXT PRIMARY KEY,
    created_at TEXT,
    summary TEXT,
    items_json TEXT
);
"""


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_conn():
    con = sqlite3.connect(DB_PATH)
    con.executescript(DIGEST_SCHEMA)
    return con


def load_day(date_str):
    con = get_conn()
    rows = con.execute(
        "SELECT cat, source, title, desc, heat, published FROM entries WHERE date=? ORDER BY id",
        (date_str,)).fetchall()
    con.close()
    zong, fin = [], []
    for cat, source, title, desc, heat, published in rows:
        item = {"title": title, "source": source}
        if cat == "财经":
            item["desc"] = (desc or "")[:80]
            item["published"] = published or 0
            fin.append(item)
        elif cat == "综合":
            item["heat"] = heat or 0
            zong.append(item)
    zong.sort(key=lambda x: x.get("heat", 0), reverse=True)
    fin.sort(key=lambda x: x.get("published", 0), reverse=True)
    return zong, fin


def call_deepseek(cfg, date_str, zong, fin, max_items):
    url = cfg.get("deepseek_base_url", "https://api.deepseek.com") + "/chat/completions"
    model = cfg.get("deepseek_model", "deepseek-chat")
    cands = {
        "综合大事(按热度取前40)": [{"title": x["title"], "source": x["source"], "heat": x.get("heat", 0)} for x in zong[:40]],
        "财经快讯(按时间取前100)": [{"title": x["title"], "source": x["source"], "desc": x.get("desc", "")} for x in fin[:100]],
    }
    system = (
        "你是一名资深的财经新闻编辑和股市分析师。任务：从当天抓取的各平台新闻/热词/财经快讯中，"
        "筛选出真正重要、有信息增量、可能影响股票/行业/市场的消息，并给出简明分析。"
        "规则：1) 剔除娱乐八卦、日常琐事、纯广告/营销内容；2) 对每条给出1-2句分析，说明为什么重要、"
        "可能影响哪些方向/板块/资产；3) 最后给一段当日要点总结。"
        "另外还要提取当天的关键词（作热词/词频用），规则："
        "1) 只输出有信息量的实义词：地点（吉隆、西藏、塞浦路斯）、人名/机构（特朗普、NASA、欧盟）、"
        "板块行业（房地产、半导体、天然气）、事件产品（泥石流、望远镜、MiniLED）等专有名词；"
        "2) 坚决剔除放之四海皆准的通用词：公司、中国、实现、同比、净利润、增长、下降、上半年、亿元、"
        "记者、报道、新华社、央视、新闻、发布、市场、板块、行业、目前、相关、风险、项目、预计 等；"
        "3) 剔除单字、纯数字、无意义英文缩写（SH/SZ/OK），保留 AI/GPU/MiniLED 这类有意义缩写；"
        "4) 输出约 30 个关键词，按重要程度从高到低排列，不重复。"
    )
    user = (
        f"今天是 {date_str}，以下是当天抓取的条目：\n{json.dumps(cands, ensure_ascii=False)}\n"
        f"请最多选 {max_items} 条最重要的（尤其关注可能影响股票和市场的消息）。\n"
        '只输出一个 JSON 对象，格式：{"summary": "当日要点总结(2-4句)", '
        '"items": [{"title": "原标题", "source": "来源", "analysis": "分析(1-2句)", "impact": "可能影响的方向/板块"}], '
        '"keywords": [{"word": "关键词", "score": 0-100的重要度}]}。'
        "keywords 里放系统规则要求提取的关键词（约30个，按重要度排序）。"
        "不要输出任何解释或代码块标记。"
    )
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.3,
        "max_tokens": 2500,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + cfg["deepseek_api_key"],
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d["choices"][0]["message"]["content"]


def parse_digest(content):
    s = content.strip()
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        s = m.group(0)
    return json.loads(s)


def ensure_keywords_column(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info(digest)").fetchall()]
    if "keywords_json" not in cols:
        con.execute("ALTER TABLE digest ADD COLUMN keywords_json TEXT")


def save_digest(date_str, digest):
    con = get_conn()
    ensure_keywords_column(con)
    con.execute(
        "INSERT INTO digest(date, created_at, summary, items_json, keywords_json) VALUES(?,?,?,?,?) "
        "ON CONFLICT(date) DO UPDATE SET created_at=excluded.created_at, "
        "summary=excluded.summary, items_json=excluded.items_json, keywords_json=excluded.keywords_json",
        (date_str, datetime.now(BILI_TZ).isoformat(timespec="seconds"),
         digest.get("summary", ""), json.dumps(digest.get("items", []), ensure_ascii=False),
         json.dumps(digest.get("keywords", []), ensure_ascii=False)))
    con.commit()
    con.close()
    day_dir = os.path.join(CORPUS_ROOT, date_str)
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, "digest.json"), "w", encoding="utf-8") as f:
        json.dump(digest, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description="AI 要闻精选（DeepSeek）")
    ap.add_argument("--date", default=None, help="日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--max-items", type=int, default=12, help="最多精选条数（默认12）")
    args = ap.parse_args()
    setup_stdout()
    date_str = args.date or datetime.now(BILI_TZ).strftime("%Y-%m-%d")

    cfg = load_config()
    if not cfg.get("deepseek_api_key"):
        print(f"[{date_str}] 未配置 DeepSeek API Key（config.json），跳过 AI 精选", flush=True)
        return

    zong, fin = load_day(date_str)
    if not zong and not fin:
        print(f"[{date_str}] 数据库里没有该日数据，先跑 daily_corpus.py", flush=True)
        return
    print(f"[{date_str}] 待分析：综合{len(zong)}条 / 财经{len(fin)}条，调用 DeepSeek ...", flush=True)
    try:
        content = call_deepseek(cfg, date_str, zong, fin, args.max_items)
        digest = parse_digest(content)
    except Exception as e:
        print(f"[{date_str}] AI 调用失败：{type(e).__name__}: {e}", flush=True)
        return
    items = digest.get("items", [])
    kws = digest.get("keywords", [])
    save_digest(date_str, digest)
    print(f"[{date_str}] 精选完成：要闻{len(items)} 条，关键词{len(kws)} 个，已写入 digest 表 + digest.json", flush=True)
    if kws:
        print("  关键词：" + " / ".join(str(k.get("word", "")) for k in kws[:20]), flush=True)
    print("=" * 60, flush=True)
    print(f"📌 当日要点：{digest.get('summary', '')}", flush=True)
    for i, it in enumerate(items, 1):
        print(f"\n{i}. {it.get('title', '')}（{it.get('source', '')}）", flush=True)
        print(f"   分析：{it.get('analysis', '')}", flush=True)
        if it.get("impact"):
            print(f"   影响：{it.get('impact', '')}", flush=True)


if __name__ == "__main__":
    main()
