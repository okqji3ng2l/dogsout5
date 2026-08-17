"""
analysis_router.py — Dogsout 數據分析 圖表頁面（FastAPI APIRouter）

沿用 video/thumbnail_router.py、manage/upload_router.py 的寫法：只提供
APIRouter，由根目錄 server.py（port 8700）掛載，不單獨啟動伺服器。

  GET /api/analysis           分析頁面（每次請求即時從 database/shorts.db 渲染 HTML）
  GET /api/analysis/stats     兩區摘要統計 (JSON)
  GET /api/analysis/recently  Recently 區塊完整圖表資料 (JSON)
  GET /api/analysis/viral     Viral 區塊完整圖表資料 (JSON)

generate_file()（產生靜態 data/analysis_preview.html）由
data_analysis/data_router.py 的 step_build() 直接 import 呼叫（update_daily.bat
排程用），不透過 HTTP。
"""
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from datetime import datetime, timedelta

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

DB_PATH = Path(__file__).resolve().parent.parent / "database" / "shorts.db"
OUTPUT  = Path(__file__).resolve().parent / "analysis_preview.html"

R = ("recently #ai", "recently #ai #girl", "recently #ai #cat")
V = ("viral #ai",    "viral #ai #girl",    "viral #ai #cat")

LABEL_COLOR = {
    "recently #ai":       "#5b4bd6",
    "recently #ai #girl": "#c23e8f",
    "recently #ai #cat":  "#0c7d51",
    "viral #ai":          "#5b4bd6",
    "viral #ai #girl":    "#c23e8f",
    "viral #ai #cat":     "#0c7d51",
}
LABEL_SHORT = {
    "recently #ai":       "#ai",
    "recently #ai #girl": "#ai #girl",
    "recently #ai #cat":  "#ai #cat",
    "viral #ai":          "#ai",
    "viral #ai #girl":    "#ai #girl",
    "viral #ai #cat":     "#ai #cat",
}

STOPWORDS = {
    "the","a","an","and","or","in","on","at","to","for","of","with","is","was",
    "are","this","that","it","i","you","we","ai","shorts","youtube","video",
    "short","my","your","how","what","why","when","do","did","be","have","has",
    "not","but","so","if","by","from","as","up","out","about","just","more",
    "can","will","no","all","there","one","like","get","see","new","best","most",
    "using","make","made","use","her","his","its","she","he","they","them",
}

# ── DB helpers ────────────────────────────────────────────────

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def q(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]

def ph(labels):
    return ",".join("?" * len(labels))

# ── Data extractors ───────────────────────────────────────────

def get_stats(conn, labels):
    row = q(conn, f"""
        SELECT COUNT(*) AS total,
               COALESCE(AVG(views),0) AS avg_views,
               COALESCE(AVG(engagement_rate),0) AS avg_eng,
               COALESCE(MAX(views),0) AS max_views
        FROM shorts WHERE search_query IN ({ph(labels)})
    """, labels)[0]
    top = q(conn, f"""
        SELECT channel_title FROM shorts
        WHERE search_query IN ({ph(labels)})
        GROUP BY channel_title ORDER BY SUM(views) DESC LIMIT 1
    """, labels)
    row["top_channel"] = top[0]["channel_title"] if top else "—"
    return row

def get_top20(conn, labels):
    return q(conn, f"""
        SELECT title, views, search_query FROM shorts
        WHERE search_query IN ({ph(labels)})
        ORDER BY views DESC LIMIT 20
    """, labels)

def get_monthly(conn, labels):
    return q(conn, f"""
        SELECT strftime('%Y-%m', published_at) AS month,
               COUNT(*) AS cnt,
               AVG(views) AS avg_views
        FROM shorts
        WHERE search_query IN ({ph(labels)})
        GROUP BY month ORDER BY month
    """, labels)

def get_wordcloud(conn, labels, top_n=80):
    rows = q(conn, f"""
        SELECT title, tags FROM shorts
        WHERE search_query IN ({ph(labels)})
    """, labels)
    counter = Counter()
    for r in rows:
        for w in re.sub(r"[^a-zA-Z0-9一-鿿]+", " ", r["title"]).split():
            w = w.lower()
            if len(w) >= 3 and w not in STOPWORDS:
                counter[w] += 1
        try:
            for t in (json.loads(r["tags"] or "[]") or []):
                w = re.sub(r"^#+", "", t).lower().strip()
                if len(w) >= 3 and w not in STOPWORDS:
                    counter[w] += 1
        except Exception:
            pass
    return [{"name": w, "value": v} for w, v in counter.most_common(top_n)]

def get_scatter(conn, labels):
    return q(conn, f"""
        SELECT title, views, like_rate, engagement_rate
        FROM shorts
        WHERE search_query IN ({ph(labels)}) AND views > 0
    """, labels)

def get_quadrant(conn, labels):
    rows = q(conn, f"""
        SELECT title, views, engagement_rate, search_query
        FROM shorts WHERE search_query IN ({ph(labels)}) AND views > 0
    """, labels)
    if not rows:
        return [], 0, 0, 0, 0
    vlist = sorted(r["views"] for r in rows)
    elist = sorted(r["engagement_rate"] for r in rows)
    n = len(vlist)
    med_v = vlist[n // 2]
    med_e = elist[n // 2]
    # p90 cuts the long-tail outliers so the dense cluster fills the viewport
    p90v = vlist[min(int(n * 0.90), n - 1)]
    p90e = elist[min(int(n * 0.90), n - 1)]
    return rows, med_v, med_e, p90v, p90e

def get_breakdown(conn, labels):
    return q(conn, f"""
        SELECT search_query, COUNT(*) AS cnt, AVG(views) AS avg_views
        FROM shorts WHERE search_query IN ({ph(labels)})
        GROUP BY search_query ORDER BY cnt ASC
    """, labels)

def build_section(conn, labels, is_viral=False):
    stats     = get_stats(conn, labels)
    top20     = get_top20(conn, labels)
    wordcloud = get_wordcloud(conn, labels)
    scatter   = get_scatter(conn, labels)
    q_rows, med_v, med_e, p90v, p90e = get_quadrant(conn, labels)
    breakdown = get_breakdown(conn, labels)
    monthly   = [] if is_viral else get_monthly(conn, labels)

    top20_titles = [r["title"][:35] + "…" if len(r["title"]) > 35 else r["title"] for r in top20]
    top20_values = [{"value": r["views"], "itemStyle": {"color": LABEL_COLOR.get(r["search_query"], "#5b4bd6")}} for r in top20]

    scatter_data = [
        [round(r["views"]/1000, 1), round(r["like_rate"]*100, 3),
         round(r["engagement_rate"]*100, 3), r["title"][:40]]
        for r in scatter
    ]
    # x 軸用對數刻度，0 會壞掉 → 最小值鉗在 0.01K (10 views)
    quad_data = [
        [max(round(r["views"]/1000, 2), 0.01), round(r["engagement_rate"]*100, 3),
         r["title"][:40], LABEL_COLOR.get(r["search_query"], "#5b4bd6")]
        for r in q_rows
    ]
    bd_labels = [LABEL_SHORT.get(r["search_query"], r["search_query"]) for r in breakdown]
    bd_colors = [LABEL_COLOR.get(r["search_query"], "#5b4bd6") for r in breakdown]
    bd_cnt    = [r["cnt"] for r in breakdown]
    bd_avg    = [round(r["avg_views"]/1000, 1) for r in breakdown]
    m_months  = [r["month"] for r in monthly]
    m_cnt     = [r["cnt"] for r in monthly]
    m_avg     = [round(r["avg_views"]/1000, 1) for r in monthly]

    return {
        "stats":     stats,
        "top20":     {"titles": top20_titles, "values": top20_values},
        "wordcloud": wordcloud,
        "scatter":   scatter_data,
        "quadrant":  {
            "data":     quad_data,
            "med_v":    max(round(med_v/1000, 2), 0.01),
            "med_e":    round(med_e*100, 3),
            "ax_max_v": max(round(p90v/1000, 1), 0.1),
            "ax_max_e": round(p90e*100, 3),
        },
        "breakdown": {"labels": bd_labels, "colors": bd_colors, "cnt": bd_cnt, "avg": bd_avg},
        "monthly":   {"months": m_months, "cnt": m_cnt, "avg": m_avg},
    }

# ── Dog SVG ───────────────────────────────────────────────────

DOG_SVG = """<svg viewBox="0 0 160 150" xmlns="http://www.w3.org/2000/svg" style="width:100%%;max-height:140px;display:block;margin:0 auto">
  <path d="M36 88 C14 72,8 45,27 37 C43 30,50 53,44 63" stroke="#0c7d51" stroke-width="5" stroke-linecap="round" fill="none"/>
  <ellipse cx="90" cy="102" rx="49" ry="31" fill="#5b4bd6" opacity="0.16"/>
  <rect x="56" y="124" width="11" height="22" rx="5.5" fill="#5b4bd6" opacity="0.38"/>
  <rect x="73" y="126" width="11" height="20" rx="5.5" fill="#5b4bd6" opacity="0.38"/>
  <rect x="97" y="126" width="11" height="20" rx="5.5" fill="#5b4bd6" opacity="0.38"/>
  <rect x="114" y="124" width="11" height="22" rx="5.5" fill="#5b4bd6" opacity="0.38"/>
  <ellipse cx="124" cy="75" rx="17" ry="23" fill="#5b4bd6" opacity="0.24"/>
  <circle cx="124" cy="52" r="28" fill="#5b4bd6" opacity="0.28"/>
  <ellipse cx="104" cy="33" rx="9" ry="22" transform="rotate(-18 104 33)" fill="#c23e8f" opacity="0.55"/>
  <ellipse cx="144" cy="31" rx="7.5" ry="18" transform="rotate(14 144 31)" fill="#c23e8f" opacity="0.55"/>
  <circle cx="132" cy="46" r="5.5" fill="#f4f3fb"/>
  <circle cx="133" cy="45" r="2.8" fill="#17181c"/>
  <circle cx="134.5" cy="43.5" r="1" fill="white" opacity="0.9"/>
  <ellipse cx="138" cy="61" rx="6.5" ry="4.5" fill="#17181c"/>
  <path d="M132 68 Q139 74 145 68" stroke="#17181c" stroke-width="1.5" stroke-linecap="round" fill="none" opacity="0.4"/>
  <rect x="109" y="87" width="30" height="9" rx="4.5" fill="#9a6708" opacity="0.55"/>
  <circle cx="124" cy="96" r="3.5" fill="#9a6708" opacity="0.85"/>
</svg>"""

# ── HTML template ─────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dogsout — #AI Shorts</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts-wordcloud@2.1.0/dist/echarts-wordcloud.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  /* 與 manage/frontend/index.html 的淺色系共用同一組 surface/border/shadow，
     讓 iframe 內容與外層主控台無縫銜接；品牌色（purple/pink/green/amber/red）
     則收深一階，確保在白底上仍有足夠文字對比（已用 dataviz 驗證腳本檢查）。 */
  --bg:#f6f7f9;--surface:#ffffff;--border:#e6e8ec;
  --primary:#5b4bd6;--accent:#0c7d51;--warn:#9a6708;
  --danger:#dc2626;--muted:#8b93a1;--text:#17181c;--text-secondary:#5b6472;--girl:#c23e8f;
  --radius:16px;
  --shadow-xs:0 1px 2px rgba(20,24,34,.05);
  --shadow-btn:0 2px 8px rgba(20,24,34,.10);
  --shadow-lift:0 8px 24px rgba(20,24,34,.14);
  --font-display:'DM Serif Display',Georgia,serif;
  --font-ui:'Inter',system-ui,sans-serif;
}
body{font-family:var(--font-ui);background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased;padding:8px 20px 64px}
.page{max-width:1280px;margin:0 auto}

/* ── NAV（黏在頁面頂端，點擊捲動到對應區塊）── */
.toggle-bar{display:flex;justify-content:center;padding:4px 0 10px;position:sticky;top:0;z-index:20;background:linear-gradient(var(--bg) 78%,transparent)}
.pill-track{display:flex;background:var(--surface);border:1px solid var(--border);border-radius:999px;padding:4px;gap:2px;box-shadow:var(--shadow-xs)}
.pill-tab{padding:10px 36px;border-radius:999px;border:none;background:transparent;color:var(--text-secondary);font-family:var(--font-ui);font-size:.88rem;font-weight:500;cursor:pointer;transition:all .22s;display:flex;align-items:center;gap:7px;white-space:nowrap}
.pill-tab.active{background:var(--primary);color:#fff;box-shadow:0 4px 14px rgba(91,75,214,.32)}
.pill-tab:hover:not(.active){color:var(--text)}

/* ── SECTIONS（上下堆疊成長頁面，不再切換隱藏）── */
.tab-section{display:block;scroll-margin-top:64px}
.tab-section + .tab-section{margin-top:56px;padding-top:40px;border-top:1px solid var(--border)}

/* ── CARD ── */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow-xs);transition:border-color .2s,box-shadow .2s}
.card:hover{border-color:rgba(91,75,214,.35);box-shadow:var(--shadow-btn)}
.chart{width:100%%;display:block}

/* ── HERO ROW（標題上移，象限圖移出改為全寬）── */
.hero-row{display:grid;grid-template-columns:1fr 340px;gap:14px;margin-bottom:14px;align-items:stretch}

/* Title block */
.hero-title{padding:4px 4px 12px 0}
.hero-title .eyebrow{font-size:.68rem;font-weight:600;letter-spacing:.11em;text-transform:uppercase;color:var(--primary);margin-bottom:10px}
.hero-title h1{font-family:var(--font-display);font-size:3.8rem;font-weight:400;line-height:.95;letter-spacing:-.02em;color:var(--text);margin-bottom:14px}
.hero-title .tagline{font-size:.83rem;color:var(--text-secondary);line-height:1.55;margin-bottom:22px}
.date-badge{display:inline-flex;align-items:center;gap:6px;font-size:.71rem;font-weight:500;color:var(--accent);background:rgba(12,125,81,.08);border:1px solid rgba(12,125,81,.22);border-radius:999px;padding:5px 13px}
.date-badge.viral{color:var(--danger);background:rgba(220,38,38,.08);border-color:rgba(220,38,38,.22)}

/* Stats card */
.hero-stats{padding:22px 18px;display:flex;flex-direction:column;gap:14px}
.stats-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.big-stat{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.big-stat.full{grid-column:1/-1}
.big-stat .snum{font-size:1.75rem;font-weight:700;color:var(--primary);line-height:1.1}
.big-stat .slbl{font-size:.65rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-top:5px}
.dog-wrap{flex:1;display:flex;align-items:center;justify-content:center;padding:8px 12px;min-height:90px}
.top-ch{padding-top:12px;border-top:1px solid var(--border)}
.top-ch .tc-lbl{font-size:.65rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.top-ch .tc-val{font-size:.82rem;font-weight:500;color:var(--text);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ── QUADRANT ROW（全寬象限圖）── */
.quadrant-row{margin-bottom:14px}

/* ── CHARTS ROW ── */
.charts-row{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:14px}

/* ── BOTTOM ROW ── */
.bottom-row{display:grid;grid-template-columns:256px 1fr;gap:14px;align-items:start}

/* Info block */
.info-block{padding:22px 20px}
.info-block h3{font-size:.88rem;font-weight:600;margin-bottom:16px;color:var(--text)}
.info-list{list-style:none;display:flex;flex-direction:column;gap:10px;margin-bottom:20px}
.info-list li{font-size:.79rem;color:var(--muted);display:flex;align-items:center;gap:8px;line-height:1.4}
.info-list li strong{color:var(--text)}
.info-list .ico{width:22px;height:22px;border-radius:6px;background:var(--bg);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:.78rem;flex-shrink:0}
.label-dots{display:flex;flex-direction:column;gap:8px;padding-top:16px;border-top:1px solid var(--border)}
.ldr{display:flex;align-items:center;gap:8px;font-size:.76rem;color:var(--muted)}
.ldr .sw{width:10px;height:10px;border-radius:3px;flex-shrink:0}

/* ── LEGEND ROW ── */
.legend-row{display:flex;gap:18px;flex-wrap:wrap;margin-top:14px;padding:14px 18px;background:var(--surface);border:1px solid var(--border);border-radius:12px;align-items:center}
.leg{display:flex;align-items:center;gap:7px;font-size:.79rem;color:var(--muted)}
.leg .sw{width:10px;height:10px;border-radius:3px;flex-shrink:0}
.live-clock{font-variant-numeric:tabular-nums;font-size:.7rem;color:var(--muted)}

footer{margin-top:18px;text-align:center;font-size:.71rem;color:var(--muted)}
footer code{color:var(--accent)}

@media(max-width:1060px){
  .charts-row{grid-template-columns:1fr 1fr}
}
@media(max-width:720px){
  .hero-row,.charts-row,.bottom-row{grid-template-columns:1fr}
  .hero-title{padding-bottom:0}
}
</style>
</head>
<body>
<div class="page">

  <!-- PILL NAV（點擊捲動到對應區塊） -->
  <div class="toggle-bar">
    <div class="pill-track">
      <button class="pill-tab active" onclick="goSection('recently',this)">📅 Recently</button>
      <button class="pill-tab" onclick="goSection('viral',this)">🔥 Viral</button>
    </div>
  </div>

  <!-- ═══════════ RECENTLY ═══════════════════════════════════ -->
  <div class="tab-section" data-tab="recently" id="sec-recently">

    <div class="hero-row">
      <!-- Left: display title -->
      <div class="hero-title">
        <div class="eyebrow">YouTube Shorts · #AI Series</div>
        <h1>Dogsout</h1>
        <p class="tagline">#AI Shorts trend intelligence.<br>5 months of data, distilled.</p>
        <span class="date-badge">__R_DATE_RANGE__</span>
      </div>

      <!-- Right: stats + dog -->
      <div class="card hero-stats">
        <div class="stats-grid">
          <div class="big-stat">
            <div class="snum">__R_AVGV__</div>
            <div class="slbl">avg views</div>
          </div>
          <div class="big-stat">
            <div class="snum" style="color:var(--accent)">__R_AVGE__</div>
            <div class="slbl">engagement</div>
          </div>
          <div class="big-stat full">
            <div class="snum" style="color:var(--warn)">__R_TOTAL__</div>
            <div class="slbl">videos · 5 months</div>
          </div>
        </div>
        <div class="dog-wrap">__DOG_SVG__</div>
        <div class="top-ch">
          <div class="tc-lbl">top channel</div>
          <div class="tc-val">__R_TOPCH__</div>
        </div>
      </div>
    </div>

    <!-- 全寬象限圖（x 軸對數刻度，放大低瀏覽數區域） -->
    <div class="card quadrant-row">
      <div id="r-quadrant" class="chart" style="height:520px"></div>
    </div>

    <!-- Charts row: Breakdown | Monthly | Wordcloud -->
    <div class="charts-row">
      <div class="card">
        <div id="r-breakdown" class="chart" style="height:270px"></div>
      </div>
      <div class="card">
        <div id="r-monthly" class="chart" style="height:270px"></div>
      </div>
      <div class="card">
        <div id="r-wordcloud" class="chart" style="height:270px"></div>
      </div>
    </div>

    <!-- Bottom row: Info | Top20 (needs the wide space) -->
    <div class="bottom-row">
      <div class="card info-block">
        <h3>Dataset overview</h3>
        <ul class="info-list">
          <li><span class="ico">📊</span><span><strong>__R_TOTAL__</strong> videos collected</span></li>
          <li><span class="ico">📅</span><span><strong>Jan – Jun 2026</strong> · 5 months</span></li>
        </ul>
        <div class="label-dots">
          <div class="ldr"><span class="sw" style="background:#5b4bd6"></span>#ai — broad baseline</div>
          <div class="ldr"><span class="sw" style="background:#c23e8f"></span>#ai #girl</div>
          <div class="ldr"><span class="sw" style="background:#0c7d51"></span>#ai #cat</div>
        </div>
      </div>
      <div class="card">
        <div id="r-top20" class="chart" style="height:410px"></div>
      </div>
    </div>

  </div><!-- /recently -->

  <!-- ═══════════ VIRAL ════════════════════════════════════════ -->
  <div class="tab-section" data-tab="viral" id="sec-viral">

    <div class="hero-row">
      <div class="hero-title">
        <div class="eyebrow">YouTube Shorts · #AI Series</div>
        <h1>Dogsout</h1>
        <p class="tagline">Viral pulse — what's breaking through <em>right now.</em></p>
        <span class="date-badge viral">__V_DATE_RANGE__</span>
      </div>

      <div class="card hero-stats">
        <div class="stats-grid">
          <div class="big-stat">
            <div class="snum" style="color:var(--danger)">__V_AVGV__</div>
            <div class="slbl">avg views</div>
          </div>
          <div class="big-stat">
            <div class="snum" style="color:var(--accent)">__V_AVGE__</div>
            <div class="slbl">engagement</div>
          </div>
          <div class="big-stat full">
            <div class="snum" style="color:var(--danger)">__V_TOTAL__</div>
            <div class="slbl">viral videos · 2 wk</div>
          </div>
        </div>
        <div class="dog-wrap">__DOG_SVG__</div>
        <div class="top-ch">
          <div class="tc-lbl">top channel</div>
          <div class="tc-val">__V_TOPCH__</div>
        </div>
      </div>
    </div>

    <!-- 全寬象限圖（x 軸對數刻度，放大低瀏覽數區域） -->
    <div class="card quadrant-row">
      <div id="v-quadrant" class="chart" style="height:520px"></div>
    </div>

    <!-- Charts row: Breakdown | Scatter | Wordcloud -->
    <div class="charts-row">
      <div class="card">
        <div id="v-breakdown" class="chart" style="height:270px"></div>
      </div>
      <div class="card">
        <div id="v-scatter" class="chart" style="height:270px"></div>
      </div>
      <div class="card">
        <div id="v-wordcloud" class="chart" style="height:270px"></div>
      </div>
    </div>

    <!-- Bottom row: Info | Top20 -->
    <div class="bottom-row">
      <div class="card info-block">
        <h3>Viral window</h3>
        <ul class="info-list">
          <li><span class="ico">🔥</span><span><strong>__V_TOTAL__</strong> viral videos</span></li>
          <li><span class="ico">📅</span><span>Published <strong>last 14 days</strong></span></li>
          <li><span class="ico">📡</span><span>Captured <strong><span class="live-clock"></span></strong></span></li>
          <li><span class="ico">⏳</span><span>Next update in <strong><span class="next-update-countdown"></span></strong></span></li>
        </ul>
        <div class="label-dots">
          <div class="ldr"><span class="sw" style="background:#5b4bd6"></span>viral #ai</div>
          <div class="ldr"><span class="sw" style="background:#c23e8f"></span>viral #ai #girl</div>
          <div class="ldr"><span class="sw" style="background:#0c7d51"></span>viral #ai #cat</div>
        </div>
      </div>
      <div class="card">
        <div id="v-top20" class="chart" style="height:410px"></div>
      </div>
    </div>

  </div><!-- /viral -->

  <div class="legend-row">
    <div class="leg"><span class="sw" style="background:#5b4bd6"></span>#ai — broad baseline</div>
    <div class="leg"><span class="sw" style="background:#c23e8f"></span>#ai #girl</div>
    <div class="leg"><span class="sw" style="background:#0c7d51"></span>#ai #cat</div>
    <div style="margin-left:auto"><span class="live-clock"></span></div>
  </div>

  <footer>Served by <code>server.py</code> (FastAPI) · Source: <code>shorts.db</code></footer>
</div>

<script>
const R = __R_DATA__;
const V = __V_DATA__;

const C = {
  bg:'#f6f7f9',surface:'#ffffff',border:'#e6e8ec',
  primary:'#5b4bd6',accent:'#0c7d51',warn:'#9a6708',
  danger:'#dc2626',muted:'#8b93a1',text:'#17181c',girl:'#c23e8f'
};
const base = {
  backgroundColor:'transparent',
  textStyle:{color:C.text,fontFamily:'Inter,system-ui,sans-serif'},
  tooltip:{backgroundColor:C.surface,borderColor:C.border,textStyle:{color:C.text},
    extraCssText:'box-shadow:0 8px 24px rgba(20,24,34,.14);border-radius:8px;'},
  legend:{textStyle:{color:C.muted}},
};
function axis(name,opts={}){
  return{name,nameTextStyle:{color:C.muted,fontSize:10},
    axisLine:{lineStyle:{color:C.border}},axisTick:{lineStyle:{color:C.border}},
    axisLabel:{color:C.muted,fontSize:9},
    splitLine:{lineStyle:{color:C.border,type:'dashed'}},...opts};
}
function make(id,option){
  const el=document.getElementById(id);
  if(!el)return;
  echarts.init(el,null,{renderer:'svg'}).setOption({...base,...option});
}

// Real-time UTC+8 clock — updates every second
function updateClock(){
  const ts=Date.now()+8*3600000;
  const d=new Date(ts);
  const p=n=>String(n).padStart(2,'0');
  const s=`${d.getUTCFullYear()}-${p(d.getUTCMonth()+1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())} UTC+8`;
  document.querySelectorAll('.live-clock').forEach(el=>el.textContent=s);
}
updateClock();
setInterval(updateClock,1000);

// Countdown to next 06:00 UTC+8
function updateCountdown(){
  const now=new Date();
  const utc8=new Date(now.getTime()+8*3600000);
  const h=utc8.getUTCHours(),m=utc8.getUTCMinutes(),s=utc8.getUTCSeconds();
  const cur=h*3600+m*60+s, target=6*3600;
  const diff=cur<target ? target-cur : 86400-cur+target;
  const p=n=>String(n).padStart(2,'0');
  const str=`${p(Math.floor(diff/3600))}:${p(Math.floor(diff%%3600/60))}:${p(diff%%60)}`;
  document.querySelectorAll('.next-update-countdown').forEach(el=>el.textContent=str);
}
updateCountdown();
setInterval(updateCountdown,1000);

// 兩個區塊上下堆疊：pill 按鈕改為平滑捲動到對應區塊
function goSection(name,btn){
  document.querySelectorAll('.pill-tab').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('sec-'+name).scrollIntoView({behavior:'smooth',block:'start'});
}
// 捲動時同步 pill 高亮
window.addEventListener('scroll',()=>{
  const v=document.getElementById('sec-viral');
  const pills=document.querySelectorAll('.pill-tab');
  const inViral=v.getBoundingClientRect().top<window.innerHeight*0.4;
  pills[0].classList.toggle('active',!inViral);
  pills[1].classList.toggle('active',inViral);
},{passive:true});

function initRecently(){
  // Quadrant — 全寬 + x 軸對數刻度（低瀏覽數區域不按比例放大），outliers clipped at p90
  make('r-quadrant',{
    tooltip:{formatter:p=>`${p.data[2]}<br/>Views: ${p.data[0]}K<br/>Eng: ${p.data[1]}%%`},
    grid:{left:'5%%',right:'3%%',top:24,bottom:40,containLabel:true},
    xAxis:{type:'log',logBase:10,...axis('Views (K, log)',{nameLocation:'middle',nameGap:28}),min:'dataMin',max:R.quadrant.ax_max_v},
    yAxis:{type:'value',...axis('Eng (%%)',{nameLocation:'middle',nameGap:36}),min:0,max:R.quadrant.ax_max_e},
    series:[{type:'scatter',clip:true,
      data:R.quadrant.data.map(d=>[d[0],d[1],d[2],d[3]]),
      symbolSize:6,opacity:.75,itemStyle:{color:p=>R.quadrant.data[p.dataIndex][3]},
      markLine:{silent:true,lineStyle:{color:C.muted,type:'dashed',width:1},
        data:[{xAxis:R.quadrant.med_v,label:{show:false}},{yAxis:R.quadrant.med_e,label:{show:false}}]}}],
    graphic:[
      {type:'text',style:{text:'Viral Star',  fill:C.accent,  fontSize:9,fontWeight:'bold'},right:10,top:24},
      {type:'text',style:{text:'Rising Star', fill:C.warn,    fontSize:9,fontWeight:'bold'},left:10, top:24},
      {type:'text',style:{text:'High Traffic',fill:C.primary, fontSize:9,fontWeight:'bold'},right:10,bottom:60},
      {type:'text',style:{text:'Low Key',     fill:C.muted,   fontSize:9,fontWeight:'bold'},left:10, bottom:60}
    ]
  });
  // Top 20 — wide bottom slot, taller chart
  make('r-top20',{
    grid:{left:'30%%',right:'12%%',top:14,bottom:24,containLabel:true},
    xAxis:axis('Views'),
    yAxis:{type:'category',data:R.top20.titles,axisLabel:{color:C.muted,fontSize:8}},
    series:[{type:'bar',data:R.top20.values,barMaxWidth:18,
      label:{show:true,position:'right',color:C.muted,fontSize:7.5,
        formatter:v=>v.value>=1e6?(v.value/1e6).toFixed(1)+'M':v.value>=1e3?(v.value/1e3).toFixed(0)+'K':v.value}}]
  });
  // Monthly Trend
  make('r-monthly',{
    legend:{data:['Count','Avg (K)'],top:6,textStyle:{color:C.muted,fontSize:9}},
    grid:{left:'13%%',right:'13%%',top:30,bottom:30,containLabel:true},
    xAxis:{type:'category',data:R.monthly.months,...axis('Month')},
    yAxis:[
      {type:'value',...axis('Count'),   position:'left'},
      {type:'value',...axis('Avg (K)'), position:'right',splitLine:{show:false}}
    ],
    series:[
      {name:'Count',type:'bar',data:R.monthly.cnt,yAxisIndex:0,
        itemStyle:{color:C.primary},barMaxWidth:22},
      {name:'Avg (K)',type:'line',data:R.monthly.avg,yAxisIndex:1,
        itemStyle:{color:C.warn},lineStyle:{width:2.5},symbol:'circle',symbolSize:5,smooth:true}
    ]
  });
  // Word Cloud
  make('r-wordcloud',{series:[{type:'wordCloud',data:R.wordcloud,
    width:'94%%',height:'88%%',left:'center',top:'center',
    sizeRange:[10,44],rotationRange:[-30,30],gridSize:5,
    textStyle:{fontFamily:'Inter,system-ui',fontWeight:'bold',
      color(){const c=['#5b4bd6','#c23e8f','#0c7d51','#9a6708','#dc2626','#6d28d9','#047857','#4338ca'];
        return c[Math.floor(Math.random()*c.length)]}},
    emphasis:{textStyle:{shadowBlur:8,shadowColor:'rgba(20,24,34,.25)'}}}]
  });
  // Query Breakdown
  make('r-breakdown',{
    legend:{data:['Count','Avg (K)'],top:6,textStyle:{color:C.muted,fontSize:9}},
    grid:{left:'16%%',right:'12%%',top:28,bottom:28,containLabel:true},
    xAxis:{type:'category',data:R.breakdown.labels,axisLabel:{color:C.muted,fontSize:9}},
    yAxis:[
      {type:'value',...axis('Count'),   position:'left'},
      {type:'value',...axis('Avg (K)'), position:'right',splitLine:{show:false}}
    ],
    series:[
      {name:'Count',type:'bar',
        data:R.breakdown.cnt.map((v,i)=>({value:v,itemStyle:{color:R.breakdown.colors[i]}})),
        yAxisIndex:0,barMaxWidth:36},
      {name:'Avg (K)',type:'line',data:R.breakdown.avg,yAxisIndex:1,
        itemStyle:{color:C.warn},lineStyle:{width:2},symbol:'circle',symbolSize:5}
    ]
  });
}

function initViral(){
  // Quadrant — 全寬 + x 軸對數刻度，clipped at p90
  make('v-quadrant',{
    tooltip:{formatter:p=>`${p.data[2]}<br/>Views: ${p.data[0]}K<br/>Eng: ${p.data[1]}%%`},
    grid:{left:'5%%',right:'3%%',top:24,bottom:40,containLabel:true},
    xAxis:{type:'log',logBase:10,...axis('Views (K, log)',{nameLocation:'middle',nameGap:28}),min:'dataMin',max:V.quadrant.ax_max_v},
    yAxis:{type:'value',...axis('Eng (%%)'),  min:0,max:V.quadrant.ax_max_e},
    series:[{type:'scatter',clip:true,
      data:V.quadrant.data.map(d=>[d[0],d[1],d[2],d[3]]),
      symbolSize:7,opacity:.82,itemStyle:{color:p=>V.quadrant.data[p.dataIndex][3]},
      markLine:{silent:true,lineStyle:{color:C.muted,type:'dashed',width:1},
        data:[{xAxis:V.quadrant.med_v,label:{show:false}},{yAxis:V.quadrant.med_e,label:{show:false}}]}}],
    graphic:[
      {type:'text',style:{text:'Viral Star',  fill:C.accent,  fontSize:9,fontWeight:'bold'},right:10,top:24},
      {type:'text',style:{text:'Rising Star', fill:C.warn,    fontSize:9,fontWeight:'bold'},left:10, top:24},
      {type:'text',style:{text:'High Traffic',fill:C.primary, fontSize:9,fontWeight:'bold'},right:10,bottom:60},
      {type:'text',style:{text:'Low Key',     fill:C.muted,   fontSize:9,fontWeight:'bold'},left:10, bottom:60}
    ]
  });
  // Top 20 — wide bottom slot
  make('v-top20',{
    grid:{left:'30%%',right:'12%%',top:14,bottom:24,containLabel:true},
    xAxis:axis('Views'),
    yAxis:{type:'category',data:V.top20.titles,axisLabel:{color:C.muted,fontSize:8}},
    series:[{type:'bar',data:V.top20.values,barMaxWidth:18,
      label:{show:true,position:'right',color:C.muted,fontSize:7.5,
        formatter:v=>v.value>=1e6?(v.value/1e6).toFixed(1)+'M':v.value>=1e3?(v.value/1e3).toFixed(0)+'K':v.value}}]
  });
  // Scatter — views vs like rate, colored by engagement
  const mxE=V.scatter.length?Math.max(...V.scatter.map(d=>d[2])):1;
  make('v-scatter',{
    tooltip:{formatter:p=>`${p.data[3]}<br/>Views: ${p.data[0]}K<br/>Like: ${p.data[1].toFixed(2)}%%<br/>Eng: ${p.data[2].toFixed(2)}%%`},
    visualMap:{min:0,max:mxE,dimension:2,show:false,inRange:{color:[C.accent,C.warn,C.danger]}},
    grid:{left:'12%%',right:'12%%',top:16,bottom:34,containLabel:true},
    xAxis:{type:'value',...axis('Views (K)')},
    yAxis:{type:'value',...axis('Like Rate (%%)')},
    series:[{type:'scatter',data:V.scatter,symbolSize:7,opacity:.78}]
  });
  // Word Cloud
  make('v-wordcloud',{series:[{type:'wordCloud',data:V.wordcloud,
    width:'94%%',height:'88%%',left:'center',top:'center',
    sizeRange:[10,40],rotationRange:[-30,30],gridSize:5,
    textStyle:{fontFamily:'Inter,system-ui',fontWeight:'bold',
      color(){const c=['#dc2626','#c23e8f','#9a6708','#e11d48','#92400e','#be185d','#991b1b'];
        return c[Math.floor(Math.random()*c.length)]}},
    emphasis:{textStyle:{shadowBlur:8,shadowColor:'rgba(20,24,34,.25)'}}}]
  });
  // Query Breakdown
  make('v-breakdown',{
    legend:{data:['Count','Avg (K)'],top:6,textStyle:{color:C.muted,fontSize:9}},
    grid:{left:'16%%',right:'12%%',top:28,bottom:28,containLabel:true},
    xAxis:{type:'category',data:V.breakdown.labels,axisLabel:{color:C.muted,fontSize:9}},
    yAxis:[
      {type:'value',...axis('Count'),   position:'left'},
      {type:'value',...axis('Avg (K)'), position:'right',splitLine:{show:false}}
    ],
    series:[
      {name:'Count',type:'bar',
        data:V.breakdown.cnt.map((v,i)=>({value:v,itemStyle:{color:V.breakdown.colors[i]}})),
        yAxisIndex:0,barMaxWidth:36},
      {name:'Avg (K)',type:'line',data:V.breakdown.avg,yAxisIndex:1,
        itemStyle:{color:C.danger},lineStyle:{width:2},symbol:'circle',symbolSize:5}
    ]
  });
}

// Boot — 兩區同時初始化（長頁面同時可見）
initRecently();
initViral();

window.addEventListener('resize',()=>{
  ['r-quadrant','r-top20','r-monthly','r-wordcloud','r-breakdown',
   'v-quadrant','v-top20','v-scatter','v-wordcloud','v-breakdown']
  .forEach(id=>{const i=echarts.getInstanceByDom(document.getElementById(id));if(i)i.resize();});
});
</script>
</body>
</html>
"""

# ── Helpers ───────────────────────────────────────────────────

def fmt_views(v):
    if v >= 1e6: return f"{v/1e6:.1f}M"
    if v >= 1e3: return f"{v/1e3:.0f}K"
    return str(int(v))

# ── HTML 渲染 ─────────────────────────────────────────────────

def render_html() -> str:
    """從 shorts.db 即時渲染分析頁 HTML 字串。"""
    conn = connect()
    r_data = build_section(conn, list(R), is_viral=False)
    v_data = build_section(conn, list(V), is_viral=True)
    conn.close()

    rs = r_data["stats"]
    vs = v_data["stats"]

    # Compute dynamic date range labels (mirrors collect_utils logic)
    now = datetime.utcnow()
    m, y = now.month - 5, now.year
    if m <= 0:
        m += 12
        y -= 1
    r_date_range = f"{y}-{m:02d}-01 → {now.strftime('%Y-%m-%d')}"
    v_start = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    v_date_range = f"{v_start} → {now.strftime('%Y-%m-%d')}"

    html = HTML_TEMPLATE
    html = html.replace("__R_DATE_RANGE__", r_date_range)
    html = html.replace("__V_DATE_RANGE__", v_date_range)
    html = html.replace("__R_TOTAL__",  str(rs["total"]))
    html = html.replace("__R_AVGV__",   fmt_views(rs["avg_views"]))
    html = html.replace("__R_AVGE__",   f"{rs['avg_eng']*100:.2f}%%")
    html = html.replace("__R_TOPCH__",  str(rs["top_channel"])[:22])
    html = html.replace("__V_TOTAL__",  str(vs["total"]))
    html = html.replace("__V_AVGV__",   fmt_views(vs["avg_views"]))
    html = html.replace("__V_AVGE__",   f"{vs['avg_eng']*100:.2f}%%")
    html = html.replace("__V_TOPCH__",  str(vs["top_channel"])[:22])
    html = html.replace("__DOG_SVG__",  DOG_SVG)
    html = html.replace("__R_DATA__",   json.dumps(r_data, ensure_ascii=False))
    html = html.replace("__V_DATA__",   json.dumps(v_data, ensure_ascii=False))
    html = html.replace("%%", "%")
    return html


def generate_file() -> dict:
    """渲染並寫出靜態 data/analysis_preview.html（bat 排程與 data_router 的
    build 步驟都呼叫這個函式）。"""
    if not DB_PATH.exists():
        raise FileNotFoundError("shorts.db 不存在，請先執行蒐集（data_router.py collect）")
    html = render_html()
    OUTPUT.write_text(html, encoding="utf-8")
    conn = connect()
    r_total = q(conn, f"SELECT COUNT(*) AS c FROM shorts WHERE search_query IN ({ph(R)})", R)[0]["c"]
    v_total = q(conn, f"SELECT COUNT(*) AS c FROM shorts WHERE search_query IN ({ph(V)})", V)[0]["c"]
    conn.close()
    print(f"✅ 已生成 {OUTPUT}")
    print(f"   Recently: {r_total} videos | Viral: {v_total} videos")
    return {"output": str(OUTPUT), "recently": r_total, "viral": v_total}


# ── FastAPI router ──────────────────────────────────────────────

analysis_router = APIRouter()


@analysis_router.get("/api/analysis", response_class=HTMLResponse)
def analysis_page():
    """分析頁面 — 每次請求即時從 shorts.db 渲染。"""
    if not DB_PATH.exists():
        return HTMLResponse("<h1>shorts.db 不存在</h1><p>請先執行 data_router.py collect</p>",
                            status_code=503)
    return render_html()


@analysis_router.get("/api/analysis/stats")
def analysis_stats():
    """兩區摘要統計。"""
    conn = connect()
    r = get_stats(conn, list(R))
    v = get_stats(conn, list(V))
    conn.close()
    return {"recently": r, "viral": v}


@analysis_router.get("/api/analysis/recently")
def analysis_recently():
    """Recently 區塊完整圖表資料。"""
    conn = connect()
    data = build_section(conn, list(R), is_viral=False)
    conn.close()
    return data


@analysis_router.get("/api/analysis/viral")
def analysis_viral():
    """Viral 區塊完整圖表資料。"""
    conn = connect()
    data = build_section(conn, list(V), is_viral=True)
    conn.close()
    return data
