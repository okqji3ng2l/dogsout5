"""
data_router.py — Dogsout 更新管線（CLI + FastAPI APIRouter）

  collect    刪除舊 shorts.db → 蒐集 #ai / #ai #girl / #ai #cat
  translate  Ollama gemma3 翻譯非中文欄位成繁體中文
  keywords   LangChain + GPT-5 mini 歸納關鍵詞彙（async 並行處理省時間）
  cluster    關鍵字 embedding 分群 → 命名 → cluster 欄位寫入 shorts.db
  build      生成 analysis_preview.html（呼叫 data/analysis_router.py 的 generate_file）

update_daily.bat 直接依序執行「python data_router.py <step>」（不經過
HTTP／不需要常駐伺服器；Telegram 訊息仍由 telegram/bot.py 的 /reload
指令直接執行 update_daily.bat，不經過本檔案）。

以下 APIRouter／端點目前沒有任何伺服器掛載，保留供未來若想手動起一個
管線 API 時直接複用。
"""

import sqlite3
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

# data/：讓 step_build() 可以直接 import analysis_router 取得 generate_file()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis_router import generate_file  # noqa: E402

DB_PATH = Path(__file__).parent.parent.parent / "database" / "shorts.db"

data_router = APIRouter()

# ── 單一步驟鎖：同一時間只允許一個步驟執行 ────────────────────────────────────

_lock = threading.Lock()
_current = {"step": None}


def _run_step(name: str, fn):
    """以鎖保護執行一個步驟；回傳 JSON 結果（busy 時回 409）。"""
    if not _lock.acquire(blocking=False):
        return JSONResponse(
            {"status": "busy", "running": _current["step"]}, status_code=409
        )
    _current["step"] = name
    t0 = time.time()
    try:
        detail = fn()
        result = {"status": "ok", "step": name,
                  "seconds": round(time.time() - t0, 1)}
        if isinstance(detail, dict):
            result.update(detail)
        return result
    except Exception as e:
        print(f"❌ 步驟 {name} 失敗：{e}")
        return JSONResponse(
            {"status": "error", "step": name, "error": str(e),
             "seconds": round(time.time() - t0, 1)},
            status_code=500,
        )
    finally:
        _current["step"] = None
        _lock.release()


# ── 各步驟實作（lazy import，缺套件不影響伺服器啟動）────────────────────────

def step_collect():
    """刪除舊 shorts.db 後依序蒐集三組標籤（各模組內 viral 優先）。"""
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"🗑️  已刪除舊資料庫：{DB_PATH.name}")
    import collect_ai
    import collect_ai_girls
    import collect_ai_cats
    collect_ai.main()
    collect_ai_girls.main()
    collect_ai_cats.main()


def step_translate():
    import translate
    translate.run_translate(
        host=translate.DEFAULT_HOST, model=translate.DEFAULT_MODEL, limit=None
    )


async def run_keywords_async(model: str | None = None,
                             limit: int | None = None,
                             concurrency: int = 10) -> dict:
    """用 LangChain async（abatch 並行呼叫）歸納全部列的關鍵詞彙。

    與 keywords.py 的逐筆同步版相比，同時發出多個 GPT-5 mini 請求，
    大幅縮短總耗時。每次執行覆蓋既有 keywords。
    """
    import keywords
    from langsmith_setup import enable_tracing
    model = model or keywords.DEFAULT_MODEL
    if not DB_PATH.exists():
        raise FileNotFoundError(f"找不到資料庫：{DB_PATH}")

    keywords.migrate_db()
    enable_tracing()
    chain = keywords.build_chain(model)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT id, title_zh, description_zh, tags_zh FROM shorts"
        if limit:
            query += f" LIMIT {int(limit)}"
        rows = conn.execute(query).fetchall()

    if not rows:
        return {"total": 0, "ok": 0, "fail": 0}

    inputs = [{
        "title_zh":       r["title_zh"] or "",
        "description_zh": r["description_zh"] or "",
        "tags_zh":        r["tags_zh"] or "[]",
    } for r in rows]

    print(f"🔑 共 {len(rows)} 筆並行歸納（模型：{model}，"
          f"max_concurrency={concurrency}）")
    responses = await chain.abatch(
        inputs,
        config={"max_concurrency": concurrency},
        return_exceptions=True,
    )

    updates: list[tuple[str, str]] = []
    fail = 0
    for row, resp in zip(rows, responses):
        if isinstance(resp, Exception):
            print(f"  ⚠️ {row['id']} 失敗：{resp}")
            fail += 1
            continue
        text = (resp.content or "").strip()
        for prefix in ("關鍵詞彙:", "關鍵詞彙：", "keywords:"):
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()
        if text:
            updates.append((text, row["id"]))
        else:
            fail += 1

    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            "UPDATE shorts SET keywords = ? WHERE id = ?", updates
        )
        conn.commit()

    print(f"✅ 歸納完成：成功 {len(updates)} | 失敗 {fail}")
    return {"total": len(rows), "ok": len(updates), "fail": fail}


def step_cluster():
    import cluster_keywords
    out = cluster_keywords.run_cluster()
    return {"chart": str(out)}


def step_build():
    generate_file()


# ── API 端點 ─────────────────────────────────────────────────────────────────

@data_router.get("/api/status")
def api_status():
    return {"status": "ok", "running": _current["step"], "db": str(DB_PATH),
            "db_exists": DB_PATH.exists()}


@data_router.post("/api/collect")
def api_collect():
    return _run_step("collect", step_collect)


@data_router.post("/api/translate")
def api_translate():
    return _run_step("translate", step_translate)


@data_router.post("/api/keywords")
async def api_keywords(limit: int | None = None, concurrency: int = 10):
    """LangChain async 並行歸納（?limit=N 測試用、?concurrency=N 調整並行數）。"""
    if not _lock.acquire(blocking=False):
        return JSONResponse(
            {"status": "busy", "running": _current["step"]}, status_code=409
        )
    _current["step"] = "keywords"
    t0 = time.time()
    try:
        detail = await run_keywords_async(limit=limit, concurrency=concurrency)
        return {"status": "ok", "step": "keywords",
                "seconds": round(time.time() - t0, 1), **detail}
    except Exception as e:
        print(f"❌ 步驟 keywords 失敗：{e}")
        return JSONResponse(
            {"status": "error", "step": "keywords", "error": str(e),
             "seconds": round(time.time() - t0, 1)},
            status_code=500,
        )
    finally:
        _current["step"] = None
        _lock.release()


@data_router.post("/api/cluster")
def api_cluster():
    return _run_step("cluster", step_cluster)


@data_router.post("/api/build")
def api_build():
    return _run_step("build", step_build)


# ── CLI（update_daily.bat 直接呼叫，不經過 HTTP）───────────────────────────

def main():
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Dogsout 數據分析管線（單一步驟）")
    parser.add_argument("step", choices=["collect", "translate", "keywords", "cluster", "build"])
    parser.add_argument("--limit", type=int, default=None, help="keywords 步驟：只處理前 N 筆（測試用）")
    parser.add_argument("--concurrency", type=int, default=10, help="keywords 步驟：LangChain 並行數")
    args = parser.parse_args()

    t0 = time.time()
    try:
        if args.step == "collect":
            step_collect()
        elif args.step == "translate":
            step_translate()
        elif args.step == "keywords":
            asyncio.run(run_keywords_async(limit=args.limit, concurrency=args.concurrency))
        elif args.step == "cluster":
            step_cluster()
        elif args.step == "build":
            step_build()
        print(f"✅ {args.step} 完成（{time.time() - t0:.1f}s）")
    except Exception as e:
        print(f"❌ {args.step} 失敗：{e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
