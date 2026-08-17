"""
translate.py — 用 Ollama Gemma 3 12B 將 shorts.db 內非中文欄位翻譯成繁體中文

新增欄位（首次執行自動 ALTER TABLE）：
  title_zh       TEXT   — 標題中文譯文
  description_zh TEXT   — 描述中文譯文
  tags_zh        TEXT   — 標籤中文譯文（JSON array）

斷點續翻：只處理 title_zh IS NULL 的列，可安全重複執行。

執行：
  python translate.py              # 翻譯全部未翻譯列
  python translate.py --limit 50   # 只翻前 50 筆（測試用）
  python translate.py --model gemma3:12b --host http://localhost:11434
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama

from langsmith_setup import enable_tracing

DB_PATH      = Path(__file__).parent.parent.parent / "database" / "shorts.db"
DEFAULT_HOST  = "http://localhost:11434"
DEFAULT_MODEL = "gemma3:12b"

# ── 中文偵測（CJK Unified Ideographs 範圍）────────────────────────────────────
_CJK = re.compile(r'[一-鿿㐀-䶿]')


def is_mostly_chinese(text: str, threshold: float = 0.25) -> bool:
    """文字中 CJK 字元佔比超過 threshold 就視為已是中文，不需翻譯。"""
    if not text or not text.strip():
        return True   # 空白跳過
    cjk_chars = len(_CJK.findall(text))
    return (cjk_chars / len(text.replace(" ", "") or "1")) >= threshold


# ── DB 遷移：新增翻譯欄位 ─────────────────────────────────────────────────────

def migrate_db():
    """若翻譯欄位不存在則 ALTER TABLE 新增。"""
    with sqlite3.connect(DB_PATH) as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(shorts)")}
        for col in ("title_zh", "description_zh", "tags_zh"):
            if col not in existing:
                conn.execute(f"ALTER TABLE shorts ADD COLUMN {col} TEXT")
                print(f"  ✅ 新增欄位：{col}")
        conn.commit()


# ── Ollama 呼叫 ───────────────────────────────────────────────────────────────

PROMPT_TMPL = """\
Translate the following YouTube Shorts metadata into Traditional Chinese (繁體中文).
Return ONLY a valid JSON object with exactly these three keys:
  "title_zh"       — translated title (string)
  "description_zh" — translated description (string, keep empty if original is empty)
  "tags_zh"        — translated tags (array of strings)

Rules:
- If a field is already in Chinese, copy it unchanged.
- If a field is empty, return an empty string or empty array accordingly.
- Output nothing except the JSON object. No markdown, no explanation.

title: {title}
description: {description}
tags: {tags}
"""


def build_chain(host: str, model: str):
    """LangChain runnable：ChatOllama（format=json）搭配翻譯 prompt。"""
    llm = ChatOllama(
        base_url=host,
        model=model,
        temperature=0.1,   # 翻譯用低溫，減少亂創
        num_predict=512,
        format="json",
    )
    prompt = ChatPromptTemplate.from_messages([("user", PROMPT_TMPL)])
    return prompt | llm


def call_ollama(chain, host: str, title: str, description: str, tags_list: list) -> dict | None:
    """
    透過 LangChain ChatOllama 呼叫，要求回傳 JSON。
    失敗回傳 None；成功回傳 {"title_zh": ..., "description_zh": ..., "tags_zh": [...]}。
    """
    tags_str = ", ".join(tags_list) if tags_list else ""
    try:
        resp = chain.invoke({
            "title": title or "",
            "description": description or "",
            "tags": tags_str,
        })
    except Exception as e:
        if "connect" in str(e).lower():
            print(f"\n  ❌ 無法連線 Ollama（{host}），請確認服務已啟動")
            sys.exit(1)
        print(f"\n  ⚠️ Ollama 呼叫失敗：{e}")
        return None

    raw = (resp.content or "{}").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"\n  ⚠️ JSON 解析失敗：{e}")
        return None


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run_translate(host: str, model: str, limit: int | None):
    if not DB_PATH.exists():
        print(f"❌ 找不到資料庫：{DB_PATH}")
        sys.exit(1)

    migrate_db()
    enable_tracing()
    chain = build_chain(host, model)

    # 抓取所有尚未翻譯的列
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT id, title, description, tags FROM shorts WHERE title_zh IS NULL"
        if limit:
            query += f" LIMIT {limit}"
        rows = conn.execute(query).fetchall()

    total = len(rows)
    if total == 0:
        print("✅ 所有影片都已翻譯完成，無需處理。")
        return

    print(f"\n🌐 共 {total} 筆待翻譯（模型：{model}）\n")

    ok = skip = fail = 0
    t0 = time.time()

    for idx, row in enumerate(rows, 1):
        vid_id      = row["id"]
        title       = row["title"] or ""
        description = row["description"] or ""
        tags_raw    = row["tags"] or "[]"

        try:
            tags_list = json.loads(tags_raw)
        except json.JSONDecodeError:
            tags_list = []

        # 若標題已是中文且描述與 tags 也是中文，直接複製跳過 Ollama
        if (is_mostly_chinese(title)
                and is_mostly_chinese(description)
                and all(is_mostly_chinese(t) for t in tags_list)):
            _save(vid_id, title, description, tags_list)
            skip += 1
            _progress(idx, total, ok, skip, fail, t0, vid_id, "(已是中文，跳過 LLM)")
            continue

        result = call_ollama(chain, host, title, description, tags_list)

        if result is None:
            # 翻譯失敗：存入空字串避免每次重試，但 title_zh 設為特殊標記
            _save(vid_id, "[翻譯失敗]", "", [])
            fail += 1
        else:
            t_zh  = result.get("title_zh")       or title
            d_zh  = result.get("description_zh") or description
            tg_zh = result.get("tags_zh")

            # tags_zh 應為 list；若 model 回傳字串則切割
            if isinstance(tg_zh, str):
                tg_zh = [t.strip() for t in tg_zh.split(",") if t.strip()]
            elif not isinstance(tg_zh, list):
                tg_zh = tags_list   # fallback

            _save(vid_id, t_zh, d_zh, tg_zh)
            ok += 1

        _progress(idx, total, ok, skip, fail, t0, vid_id)

    elapsed = time.time() - t0
    print(f"\n\n✅ 翻譯完成：成功 {ok} | 跳過(已中文) {skip} | 失敗 {fail}")
    print(f"   總耗時：{elapsed/60:.1f} 分鐘（{elapsed/max(ok+skip,1):.1f} 秒/筆）")


def _save(vid_id: str, title_zh: str, description_zh: str, tags_zh: list):
    """將翻譯結果寫回 shorts.db。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """UPDATE shorts
               SET title_zh = ?, description_zh = ?, tags_zh = ?
               WHERE id = ?""",
            (
                title_zh,
                description_zh,
                json.dumps(tags_zh, ensure_ascii=False),
                vid_id,
            ),
        )
        conn.commit()


def _progress(idx, total, ok, skip, fail, t0, vid_id, note=""):
    elapsed  = time.time() - t0
    avg_sec  = elapsed / idx
    eta_sec  = avg_sec * (total - idx)
    eta_str  = f"{eta_sec/60:.1f}m" if eta_sec >= 60 else f"{eta_sec:.0f}s"
    pct      = idx / total * 100
    bar_fill = int(pct / 5)
    bar      = "█" * bar_fill + "░" * (20 - bar_fill)
    print(
        f"\r  [{bar}] {idx}/{total} ({pct:.0f}%)  "
        f"✓{ok} ⊘{skip} ✗{fail}  ETA {eta_str}  {vid_id[:11]}… {note}",
        end="", flush=True,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Translate shorts.db with Ollama Gemma 3 12B")
    parser.add_argument("--host",  default=DEFAULT_HOST,  help="Ollama host URL")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name")
    parser.add_argument("--limit", type=int, default=None, help="只翻前 N 筆（測試用）")
    args = parser.parse_args()

    print(f"🔤 Ollama 翻譯器")
    print(f"   DB    : {DB_PATH}")
    print(f"   Model : {args.model}")
    print(f"   Host  : {args.host}")

    run_translate(args.host, args.model, args.limit)


if __name__ == "__main__":
    main()
