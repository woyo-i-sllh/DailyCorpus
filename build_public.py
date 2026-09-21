# -*- coding: utf-8 -*-
"""
导出可视化数据：把 corpus.db 整理成 public/data/*.json（供 GitHub Pages 静态看板使用）

用法：
  python build_public.py            # 导出到 public/data/
  python build_public.py --top 300  # 控制关键词索引/榜单规模

数据来源：
  entries          日常抓取的语料（2026-08-30 起，含热度/AI 精选）
  entries_archive  微博热搜历史存档（2020-11 起，backfill_weibo.py 回填）
  digest           AI 要闻精选

产出（public/data/）：
  meta.json      概览信息（日期范围、总量、生成时间）
  daily.json     每日热词数量（用于趋势曲线）
  top.json       热词榜：全时段 + 分年度
  keywords.json  关键词索引（出现 >=2 天的词：日期/最好排名/出现次数），支持搜索和时间线
  monthly.json   每月 Top15 热词
  digest.json    AI 要闻精选（全部日期）
  live.json      日常抓取的语料摘要（按天：各分类条数 + Top 条目）
"""

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "corpus.db")
OUT_DIR = os.path.join(BASE_DIR, "public", "data")
BILI_TZ = timezone(timedelta(hours=8))


def write_json(name, data, compact=False):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False)
    size = os.path.getsize(path)
    print(f"  {name:<16} {size / 1024:>8.1f} KB")


def main():
    ap = argparse.ArgumentParser(description="导出 public 可视化数据")
    ap.add_argument("--top", type=int, default=300, help="热词榜条数（默认 300）")
    ap.add_argument("--min-days", type=int, default=2, help="关键词索引收录门槛：至少出现在 N 天")
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    print("导出 public/data/ ...")

    # ---------- 历史存档：按天/关键词聚合 ----------
    arch = con.execute(
        "SELECT date, keyword, best_rank, appearances FROM entries_archive ORDER BY date").fetchall()
    by_day = defaultdict(list)
    by_kw = defaultdict(list)
    for r in arch:
        by_day[r["date"]].append(r)
        by_kw[r["keyword"]].append((r["date"], r["best_rank"], r["appearances"]))

    # ---------- 日常抓取 ----------
    live_by_day = defaultdict(int)
    for r in con.execute("SELECT date, COUNT(*) c FROM entries GROUP BY date").fetchall():
        live_by_day[r["date"]] = r["c"]

    all_dates = sorted(set(list(by_day.keys()) + list(live_by_day.keys())))
    if not all_dates:
        raise SystemExit("数据库里没有数据，先跑 daily_corpus.py 或 backfill_weibo.py")

    # meta
    uniq_kw = len(by_kw)
    meta = {
        "generated_at": datetime.now(BILI_TZ).strftime("%Y-%m-%d %H:%M"),
        "start": all_dates[0],
        "end": all_dates[-1],
        "days": len(all_dates),
        "archive_keywords": len(arch),
        "unique_keywords": uniq_kw,
        "live_entries": sum(live_by_day.values()),
        "live_days": len(live_by_day),
    }
    write_json("meta.json", meta)

    # daily：每日热词量（历史存档 + 当日抓取）
    daily = [{"d": d, "n": len(by_day.get(d, [])), "live": live_by_day.get(d, 0)} for d in all_dates]
    write_json("daily.json", daily, compact=True)

    # top：全时段 / 分年度
    def build_rank(dates_filter=None, limit=300):
        stat = []
        for kw, recs in by_kw.items():
            rs = [x for x in recs if not dates_filter or dates_filter(x[0])]
            if not rs:
                continue
            stat.append({
                "k": kw,
                "days": len(rs),
                "best": min(x[1] for x in rs),
                "first": min(x[0] for x in rs),
                "last": max(x[0] for x in rs),
            })
        stat.sort(key=lambda x: (-x["days"], x["best"]))
        return stat[:limit]

    years = sorted({d[:4] for d in all_dates})
    top = {"all": build_rank(None, args.top)}
    for y in years:
        top[y] = build_rank(lambda d, y=y: d.startswith(y), 100)
    write_json("top.json", top, compact=True)

    # keywords：索引（>= min_days 天），用于搜索 + 时间线
    idx = {kw: sorted(recs) for kw, recs in by_kw.items() if len(recs) >= args.min_days}
    # 按出现天数排序，控制体积
    idx_items = sorted(idx.items(), key=lambda x: -len(x[1]))[:15000]
    keywords = [[kw, [[d, r, a] for d, r, a in recs]] for kw, recs in idx_items]
    write_json("keywords.json", {"min_days": args.min_days, "items": keywords}, compact=True)

    # monthly：每月 Top15
    monthly = {}
    for d in all_dates:
        m = d[:7]
        monthly.setdefault(m, defaultdict(int))
        for r in by_day.get(d, []):
            monthly[m][r["keyword"]] += 1
    monthly_out = {m: sorted([{"k": k, "days": v} for k, v in c.items()],
                             key=lambda x: -x["days"])[:15] for m, c in sorted(monthly.items())}
    write_json("monthly.json", monthly_out, compact=True)

    # digest：AI 要闻精选
    digest_rows = con.execute(
        "SELECT date, summary, items_json, keywords_json FROM digest ORDER BY date").fetchall()
    digests = []
    for r in digest_rows:
        try:
            items = json.loads(r["items_json"] or "[]")
        except Exception:
            items = []
        try:
            kws = json.loads(r["keywords_json"] or "[]")
        except Exception:
            kws = []
        digests.append({"d": r["date"], "summary": r["summary"] or "", "items": items, "keywords": kws})
    write_json("digest.json", digests, compact=True)

    # live：日常抓取摘要（按天：分类条数 + 热度 Top）
    live_out = []
    for d, cnt in sorted(live_by_day.items()):
        rows = con.execute(
            "SELECT cat, source, title, heat, url, published FROM entries WHERE date=? ", (d,)).fetchall()
        cats = defaultdict(int)
        for r in rows:
            cats[r["cat"]] += 1
        tops = sorted([dict(r) for r in rows if r["cat"] == "综合"], key=lambda x: -(x["heat"] or 0))[:15]
        fin = sorted([dict(r) for r in rows if r["cat"] == "财经"], key=lambda x: -(x["published"] or 0))[:15]
        live_out.append({
            "d": d, "n": cnt, "cats": dict(cats),
            "top": [{"t": x["title"], "s": x["source"], "h": x["heat"], "u": x["url"]} for x in tops],
            "fin": [{"t": x["title"], "s": x["source"], "u": x["url"], "p": x["published"]} for x in fin],
        })
    write_json("live.json", live_out, compact=True)

    con.close()
    print(f"完成：{len(all_dates)} 天 / {uniq_kw} 个唯一关键词")


if __name__ == "__main__":
    main()
