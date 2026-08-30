# -*- coding: utf-8 -*-
"""
DailyCorpus 数据看板：本地网页，查询每天的大事 / 财经 / 热词 / 词频。
直接读 corpus.db（SQLite），纯标准库，零依赖。

用法：
  python dashboard.py              # 启动 http://127.0.0.1:8765
  python dashboard.py --port 9000  # 换端口
"""

import argparse
import csv
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "corpus.db")
CORPUS_ROOT = os.path.join(BASE_DIR, "corpus")
BILI_TZ = timezone(timedelta(hours=8))

# 简单内存缓存：看板重复打开/切页不用重复算
_CACHE = {}
_CACHE_TTL = 60


def cached(key, fn):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def get_conn():
    return sqlite3.connect(DB_PATH)


def list_dates():
    if os.path.exists(DB_PATH):
        con = get_conn()
        try:
            rows = con.execute("SELECT DISTINCT date FROM entries ORDER BY date DESC").fetchall()
        except sqlite3.Error:
            rows = []
        con.close()
        dates = [r[0] for r in rows]
        if dates:
            return dates
    if os.path.isdir(CORPUS_ROOT):
        return sorted((d for d in os.listdir(CORPUS_ROOT)
                       if len(d) == 10 and d[4] == "-" and d[7] == "-"), reverse=True)
    return []


def fmt_time(ts):
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), BILI_TZ).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def fmt_heat(n):
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "—"
    if n >= 100000000:
        return f"{n / 100000000:.2f}亿"
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return f"{n:,.0f}"


def day_data(date_str):
    con = get_conn()
    entries = []
    hotwords = []
    runs = []
    try:
        entries = con.execute(
            "SELECT cat, source, rank, title, desc, heat, url, published "
            "FROM entries WHERE date=? ORDER BY id", (date_str,)).fetchall()
        hotwords = con.execute(
            "SELECT source, rank, keyword, heat FROM hotwords WHERE date=? ORDER BY rank",
            (date_str,)).fetchall()
        runs = con.execute(
            "SELECT fetched_at, total_entries, note FROM runs WHERE date=? ORDER BY id DESC LIMIT 1",
            (date_str,)).fetchall()
    except sqlite3.Error:
        pass
    con.close()

    cats = {"综合": [], "社区": [], "财经": []}
    for cat, source, rank, title, desc, heat, url, published in entries:
        cats.setdefault(cat, []).append({
            "cat": cat, "rank": rank, "source": source, "title": title,
            "heat": heat or 0, "url": url, "published": published or 0,
            "desc": (desc or "")[:100],
            "time": fmt_time(published),
        })
    for k in cats:
        if k == "财经":
            cats[k].sort(key=lambda e: e["published"], reverse=True)
        else:
            cats[k].sort(key=lambda e: e["heat"], reverse=True)

    wf = []
    wf_path = os.path.join(CORPUS_ROOT, date_str, "wordfreq.csv")
    if os.path.exists(wf_path):
        with open(wf_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                wf.append({"rank": row.get("rank"), "word": row.get("word"), "count": row.get("count")})

    return {
        "date": date_str,
        "run": {"fetched_at": runs[0][0] if runs else "", "total": runs[0][1] if runs else len(entries),
                "note": runs[0][2] if runs else ""},
        "cats": cats,
        "hotwords": [{"source": s, "rank": r, "keyword": k, "heat": h or 0} for s, r, k, h in hotwords],
        "wordfreq": wf,
        "fmt_heat": None,
    }


def digest_data(date_str):
    con = get_conn()
    try:
        row = con.execute("SELECT summary, items_json, keywords_json FROM digest WHERE date=?", (date_str,)).fetchone()
    except sqlite3.Error:
        row = None
    con.close()
    if not row:
        return {"summary": "", "items": [], "keywords": []}
    try:
        items = json.loads(row[1])
    except Exception:
        items = []
    kws = []
    try:
        if row[2]:
            kws = json.loads(row[2])
    except Exception:
        pass
    return {"summary": row[0] or "", "items": items, "keywords": kws}


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DailyCorpus 数据看板</title>
<style>
  :root{ --bg:#f5f6fa; --card:#fff; --line:#e5e8ef; --text:#1f2430; --muted:#7a8194;
         --accent:#2f6fed; --hot:#ff5a3c; --fin:#0f9d58; --com:#9b59b6; }
  *{ box-sizing:border-box; }
  body{ margin:0; font-family:"Microsoft YaHei", system-ui, sans-serif; background:var(--bg); color:var(--text); }
  header{ background:linear-gradient(135deg,#1f3b8f,#2f6fed); color:#fff; padding:18px 28px; }
  header h1{ margin:0; font-size:22px; }
  header .sub{ opacity:.85; font-size:13px; margin-top:4px; }
  .wrap{ max-width:1180px; margin:0 auto; padding:18px; }
  .toolbar{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:14px; }
  .toolbar input[type=date]{ padding:8px 10px; border:1px solid var(--line); border-radius:8px; font-size:14px; }
  .search{ flex:1; min-width:220px; padding:8px 12px; border:1px solid var(--line); border-radius:8px; font-size:14px; }
  .tag{ color:var(--muted); font-size:12px; }
  .cards{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:16px; }
  .card{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
  .card .num{ font-size:26px; font-weight:700; }
  .card .lab{ font-size:12px; color:var(--muted); margin-top:2px; }
  .tabs{ display:flex; gap:6px; margin-bottom:14px; flex-wrap:wrap; }
  .tab{ padding:8px 16px; border-radius:999px; border:1px solid var(--line); background:var(--card); cursor:pointer; font-size:14px; user-select:none; }
  .tab.on{ background:var(--accent); color:#fff; border-color:var(--accent); }
  .panel{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:8px 0; }
  table{ width:100%; border-collapse:collapse; font-size:13.5px; }
  th,td{ text-align:left; padding:9px 14px; border-bottom:1px solid var(--line); vertical-align:top; }
  th{ color:var(--muted); font-weight:600; background:#fafbfd; position:sticky; top:0; }
  tr:hover td{ background:#f7f9ff; }
  .badge{ display:inline-block; padding:2px 8px; border-radius:6px; font-size:12px; color:#fff; white-space:nowrap; }
  .b-综合{ background:var(--hot); } .b-财经{ background:var(--fin); } .b-社区{ background:var(--com); }
  .src{ color:var(--muted); font-size:12px; }
  a{ color:var(--accent); text-decoration:none; } a:hover{ text-decoration:underline; }
  .heat{ font-variant-numeric:tabular-nums; color:#c0392b; font-weight:600; white-space:nowrap; }
  .time{ color:var(--muted); font-size:12px; white-space:nowrap; }
  .empty{ padding:30px; text-align:center; color:var(--muted); }
</style>
</head>
<body>
<header>
  <h1>📊 DailyCorpus 数据看板</h1>
  <div class="sub">每天的大事 · 财经 · 热词 · 词频（数据来自 corpus.db）</div>
</header>
<div class="wrap">
  <div class="toolbar">
    <input type="date" id="datePick">
    <span class="tag" id="runInfo"></span>
    <input class="search" id="searchBox" placeholder="🔍 搜索当前列表…">
  </div>
  <div class="cards">
    <div class="card"><div class="num" id="cTotal">—</div><div class="lab">当日总条目</div></div>
    <div class="card"><div class="num" id="cHot">—</div><div class="lab">热词数</div></div>
    <div class="card"><div class="num" id="cZong">—</div><div class="lab">综合大事</div></div>
    <div class="card"><div class="num" id="cFin">—</div><div class="lab">财经消息</div></div>
  </div>
  <div class="tabs">
    <div class="tab on" data-tab="digest">🤖 要闻精选</div>
    <div class="tab" data-tab="zonghe">🔥 大事</div>
    <div class="tab" data-tab="finance">📰 财经</div>
    <div class="tab" data-tab="community">🎬 社区</div>
    <div class="tab" data-tab="hotwords">🔑 热词</div>
    <div class="tab" data-tab="wordfreq">🧮 关键词</div>
    <div class="tab" data-tab="all">📋 全部</div>
  </div>
  <div class="panel"><div id="tableWrap"><div class="empty">加载中…</div></div></div>
</div>
<script>
let curTab = 'digest', curData = null;
async function api(url){ const r = await fetch(url); return r.json(); }
function fmtHeat(n){ n = +n||0; if(n>=1e8) return (n/1e8).toFixed(2)+'亿'; if(n>=1e4) return (n/1e4).toFixed(1)+'万'; return n.toLocaleString(); }
function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function rowHtml(badge, title, src, extra, heat, time, url){
  const t = url ? '<a href="'+esc(url)+'" target="_blank" rel="noopener">'+esc(title)+'</a>' : esc(title);
  return '<tr><td>'+badge+'</td><td>'+t+'</td><td class="src">'+esc(src)+'</td><td class="src">'+esc(extra)+'</td><td class="heat">'+(heat>0?fmtHeat(heat):'')+'</td><td class="time">'+esc(time)+'</td></tr>';
}
function render(){
  if(!curData) return;
  const d = curData, kw = (document.getElementById('searchBox').value||'').trim();
  const has = kw.length>0;
  const filt = arr => (arr||[]).filter(r => !has || (r.title||r.keyword||r.word||'').toLowerCase().includes(kw.toLowerCase()));
  if(curTab==='digest'){
    const dg = d.digest || {summary:'', items:[]};
    const sum = dg.summary ? '<div style="margin:14px;padding:14px;border-left:4px solid var(--accent);background:#f2f6ff;border-radius:8px;line-height:1.7">📌 <b>当日要点</b><br>'+esc(dg.summary)+'</div>' : '';
    const list = filt(dg.items).map(it=>'<tr><td>'+esc(it.title)+'</td><td class="src">'+esc(it.source)+'</td><td>'+esc(it.analysis)+'</td><td class="src">'+esc(it.impact)+'</td></tr>').join('');
    const head = '<table><thead><tr><th>标题</th><th>来源</th><th>分析</th><th>可能影响</th></tr></thead><tbody>';
    document.getElementById('tableWrap').innerHTML = sum + (list ? head+list+'</tbody></table>' : '<div class="empty">今天还没有 AI 精选，先跑 ai_digest.py</div>');
    return;
  }
  let rows = '';
  if(curTab==='zonghe'){
    rows = filt(d.cats['综合']).slice(0,60).map((e,i)=>rowHtml('<span class="badge b-综合">综合</span>', e.title, e.source, e.desc||'', e.heat, e.time, e.url)).join('');
  } else if(curTab==='finance'){
    rows = filt(d.cats['财经']).map((e,i)=>rowHtml('<span class="badge b-财经">财经</span>', e.title, e.source, e.desc||'', e.heat, e.time, e.url)).join('');
  } else if(curTab==='community'){
    rows = filt(d.cats['社区']).slice(0,40).map((e,i)=>rowHtml('<span class="badge b-社区">社区</span>', e.title, e.source, e.desc||'', e.heat, e.time, e.url)).join('');
  } else if(curTab==='hotwords'){
    rows = filt(d.hotwords).map(h=>'<tr><td><span class="badge b-综合">热词</span></td><td>'+esc(h.keyword)+'</td><td class="src">'+esc(h.source)+'</td><td></td><td class="heat">'+(h.heat?fmtHeat(h.heat):'')+'</td><td></td></tr>').join('');
  } else if(curTab==='wordfreq'){
    const kws = (d.digest && d.digest.keywords && d.digest.keywords.length) ? d.digest.keywords : null;
    if(kws){
      const head = '<table><thead><tr><th>排名</th><th>关键词</th><th></th><th></th><th>重要度</th><th></th></tr></thead><tbody>';
      const list = filt(kws).map((k,i)=>{ const sc = +k.score||0; return '<tr><td class="src">#'+(i+1)+'</td><td>'+esc(k.word)+'</td><td></td><td></td><td class="heat">'+(sc?'★'+sc:'')+'</td><td></td></tr>'; }).join('');
      rows = list ? head+list+'</tbody></table>' : '<div class="empty">没有数据</div>';
    } else {
      rows = (d.wordfreq||[]).map(w=>'<tr><td class="src">#'+esc(w.rank)+'</td><td>'+esc(w.word)+'</td><td></td><td></td><td class="heat">×'+esc(w.count)+'</td><td></td></tr>').join('');
    }
  } else {
    rows = filt((d.cats['综合']||[]).concat(d.cats['社区']||[]).concat(d.cats['财经']||[])).map(e=>rowHtml('<span class="badge b-'+esc(e.cat)+'">'+esc(e.cat)+'</span>', e.title, e.source, e.desc||'', e.heat, e.time, e.url)).join('');
  }
  const head = '<table><thead><tr><th>类别</th><th>标题</th><th>来源</th><th>摘要</th><th>热度</th><th>时间</th></tr></thead><tbody>';
  document.getElementById('tableWrap').innerHTML = rows ? head+rows+'</tbody></table>' : '<div class="empty">没有数据</div>';
}
async function load(date){
  const [day, dg] = await Promise.all([
    api('/api/day?date='+encodeURIComponent(date)),
    api('/api/digest?date='+encodeURIComponent(date))
  ]);
  curData = day; curData.digest = dg;
  const d = curData;
  document.getElementById('cTotal').textContent = (d.cats['综合']||[]).length+(d.cats['社区']||[]).length+(d.cats['财经']||[]).length;
  document.getElementById('cHot').textContent = (d.hotwords||[]).length;
  document.getElementById('cZong').textContent = (d.cats['综合']||[]).length;
  document.getElementById('cFin').textContent = (d.cats['财经']||[]).length;
  document.getElementById('runInfo').textContent = d.run.fetched_at ? ('抓取于 '+d.run.fetched_at+' · 共 '+d.run.total+' 条') : '';
  render();
}
async function init(){
  const {dates} = await api('/api/dates');
  const pick = document.getElementById('datePick');
  if(!dates.length){ document.getElementById('tableWrap').innerHTML = '<div class="empty">还没有数据，先跑 daily_corpus.bat</div>'; return; }
  pick.value = dates[0]; pick.max = dates[0]; pick.min = dates[dates.length-1];
  pick.addEventListener('change', ()=> load(pick.value));
  document.querySelectorAll('.tab').forEach(t=> t.addEventListener('click', ()=>{
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
    t.classList.add('on'); curTab = t.dataset.tab; render();
  }));
  document.getElementById('searchBox').addEventListener('input', render);
  await load(dates[0]);
}
init();
</script>
</body>
</html>
"""


class NoReuseServer(ThreadingHTTPServer):
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_body(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/dates":
            self.send_body(json.dumps({"dates": list_dates()}, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
        elif u.path == "/api/day":
            d = (parse_qs(u.query).get("date") or [""])[0]
            self.send_body(json.dumps(cached(("day", d), lambda: day_data(d)), ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
        elif u.path == "/api/digest":
            d = (parse_qs(u.query).get("date") or [""])[0]
            self.send_body(json.dumps(cached(("digest", d), lambda: digest_data(d)), ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
        elif u.path == "/":
            self.send_body(HTML.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self.send_error(404)


def main():
    ap = argparse.ArgumentParser(description="DailyCorpus 数据看板")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    try:
        srv = NoReuseServer(("127.0.0.1", args.port), Handler)
    except OSError:
        print(f"看板已经在运行：http://127.0.0.1:{args.port}", flush=True)
        return
    print(f"看板已启动：http://127.0.0.1:{args.port}  （Ctrl+C 停止）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
