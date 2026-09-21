# -*- coding: utf-8 -*-
"""
历史数据回填：微博热搜存档（2020-11 至今，近 6 年）

数据源：github.com/justjavac/weibo-trending-hot-search
        raw/YYYY-MM-DD.json —— 每天多个时间点的热搜快照

用法：
  python backfill_weibo.py                        # 默认回填最近 3 年
  python backfill_weibo.py --start 2023-09-01 --end 2026-09-21
  python backfill_weibo.py --days 90              # 只回填最近 90 天

产出：
  - corpus.db 的 entries_archive 表（历史热词：日期/关键词/最好排名/出现次数）
  - data/weibo_archive/YYYY-MM-DD.json 本地缓存（避免重复下载）
"""

import argparse
import concurrent.futures
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "corpus.db")
CACHE_DIR = os.path.join(BASE_DIR, "data", "weibo_archive")
BILI_TZ = timezone(timedelta(hours=8))

RAW_URL = "https://raw.githubusercontent.com/justjavac/weibo-trending-hot-search/master/raw/{date}.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}

ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries_archive (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    source      TEXT NOT NULL,
    keyword     TEXT NOT NULL,
    best_rank   INTEGER,
    appearances INTEGER,
    url         TEXT,
    UNIQUE(date, source, keyword)
);
CREATE INDEX IF NOT EXISTS idx_arch_date ON entries_archive(date);
CREATE INDEX IF NOT EXISTS idx_arch_keyword ON entries_archive(keyword);
"""


def log(msg):
    now = datetime.now(BILI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(enc, "replace").decode(enc, "replace"), flush=True)


def get_conn():
    con = sqlite3.connect(DB_PATH)
    con.executescript(ARCHIVE_SCHEMA)
    return con


def fetch_day(date_str, retries=3):
    """下载某天的存档 JSON；已缓存则直接读缓存。返回 (date, list) 或 (date, None)。"""
    cache_path = os.path.join(CACHE_DIR, date_str + ".json")
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 2:
        try:
            with open(cache_path, encoding="utf-8") as f:
                return date_str, json.load(f)
        except Exception:
            pass
    url = RAW_URL.format(date=date_str)
    for i in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8")
            data = json.loads(raw)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(raw)
            return date_str, data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return date_str, None      # 该日无存档
            if i == retries:
                return date_str, None
            time.sleep(2 * i)
        except Exception:
            if i == retries:
                return date_str, None
            time.sleep(2 * i)
    return date_str, None


def parse_day(items):
    """把一天多个快照合并成 [{keyword, best_rank, appearances, url}]"""
    agg = {}
    for it in items or []:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        url = it.get("url") or ""
        m = re.search(r"band_rank=(\d+)", url)
        rank = int(m.group(1)) if m else 999
        rec = agg.get(title)
        if rec is None:
            agg[title] = {"keyword": title, "best_rank": rank, "appearances": 1, "url": url}
        else:
            rec["appearances"] += 1
            if rank < rec["best_rank"]:
                rec["best_rank"] = rank
                rec["url"] = url
    return list(agg.values())


def save_day(con, date_str, rows):
    con.execute("DELETE FROM entries_archive WHERE date=? AND source=?", (date_str, "微博热搜(存档)"))
    con.executemany(
        "INSERT OR REPLACE INTO entries_archive (date, source, keyword, best_rank, appearances, url) "
        "VALUES (?,?,?,?,?,?)",
        [(date_str, "微博热搜(存档)", r["keyword"], r["best_rank"], r["appearances"], r["url"]) for r in rows])
    con.commit()


def main():
    ap = argparse.ArgumentParser(description="回填微博热搜历史存档")
    ap.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD（默认昨天）")
    ap.add_argument("--days", type=int, default=None, help="只回填最近 N 天（与 --start 二选一）")
    ap.add_argument("--workers", type=int, default=8, help="并发下载线程数（默认 8）")
    ap.add_argument("--skip-existing", action="store_true", default=True, help="跳过库里已有数据的日期")
    args = ap.parse_args()

    today = datetime.now(BILI_TZ).date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else today - timedelta(days=1)
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    elif args.days:
        start = end - timedelta(days=args.days - 1)
    else:
        start = end - timedelta(days=365 * 3)      # 默认近三年

    dates = []
    d = start
    while d <= end:
        dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)

    con = get_conn()
    if args.skip_existing:
        done = {r[0] for r in con.execute(
            "SELECT DISTINCT date FROM entries_archive WHERE source='微博热搜(存档)'").fetchall()}
        todo = [x for x in dates if x not in done]
        log(f"区间 {start} ~ {end}，共 {len(dates)} 天，其中 {len(todo)} 天待回填（已有 {len(dates) - len(todo)} 天）")
    else:
        todo = dates
        log(f"区间 {start} ~ {end}，共 {len(todo)} 天全部重跑")

    if not todo:
        log("无需回填")
        return

    t0 = time.time()
    done_cnt = empty_cnt = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_day, x): x for x in todo}
        for fut in concurrent.futures.as_completed(futures):
            date_str, items = fut.result()
            if not items:
                empty_cnt += 1
                continue
            rows = parse_day(items)
            if rows:
                save_day(con, date_str, rows)
                done_cnt += 1
            else:
                empty_cnt += 1
            if (done_cnt + empty_cnt) % 100 == 0:
                elapsed = time.time() - t0
                log(f"进度 {done_cnt + empty_cnt}/{len(todo)}（入库 {done_cnt} 天，空/失败 {empty_cnt} 天，{elapsed:.0f}s）")

    total = con.execute("SELECT COUNT(*), COUNT(DISTINCT date) FROM entries_archive").fetchone()
    con.close()
    log(f"完成：本次入库 {done_cnt} 天，跳过/无数据 {empty_cnt} 天，耗时 {time.time() - t0:.0f}s")
    log(f"entries_archive 现有 {total[0]} 条热词，覆盖 {total[1]} 天")


if __name__ == "__main__":
    main()
