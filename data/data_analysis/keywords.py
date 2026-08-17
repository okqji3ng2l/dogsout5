"""
keywords.py — 用 LangChain + GPT-5 mini 將 shorts.db 每列的
              title_zh / description_zh / tags_zh 縮減成 5 個中英文關鍵詞彙
              （必須涵蓋影片的主角與劇情）

新增欄位（首次執行自動 ALTER TABLE）：
  keywords TEXT — 關鍵詞彙，格式如：身體恐怖 (Body Horror)，AI與視覺特效 (AI & VFX)，...

API 金鑰：從 data/.env 以 os.getenv() 讀取 OPENAI_API_KEY

每次執行都從頭處理全部列，已填過的 keywords 直接覆蓋。

安裝依賴：pip install -r requirements.txt

執行：
  python keywords.py               # 處理全部未歸納列
  python keywords.py --limit 20    # 只處理前 20 筆（測試用）
  python keywords.py --model gpt-5-mini
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from langsmith_setup import enable_tracing

DB_PATH       = Path(__file__).parent.parent.parent / "database" / "shorts.db"
ENV_PATH      = Path(__file__).parent.parent / ".env"        # data/.env
DEFAULT_MODEL = "gpt-5-mini"

# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
你是影片內容歸納助手。使用者會給你一支 YouTube Shorts 影片的中文標題、中文描述與中文標籤，
請將這三組文字縮減成 5 個「中文 (English)」格式的關鍵詞彙，用來簡單歸納這支影片的內容。

規則：
- 必須恰好輸出 5 個關鍵詞彙
- 每個關鍵詞彙格式必須是：中文 (English)
- 關鍵詞彙必須包含影片內的「主角」（人物、動物或生物等）與「劇情」（發生了什麼事）
- 只輸出關鍵詞彙，以全形逗號「，」分隔，不要任何其他說明或前綴
- 關鍵詞彙要能概括影片主題，避免直接照抄 #hashtag

範例輸入：
title: 有東西入侵了我的身體 😱🐍🪲 #shorts #viral #trending #ai #vfx #horror #funny #bodyhorror
description: 我以為只是平凡的一天...直到奇怪的生物開始入侵我的身體。😱 蛇、蟻、蜜蜂，以及更可怕的東西顛覆了我的世界。🐍🐜🐝 是真實存在的...還是我的想像？ 👀 使用表演、AI 影片生成、臉部濾鏡和視覺特效，僅供娛樂目的。
tags: ["shorts", "viral", "trending", "ai", "vfx", "horror", "funny", "bodyhorror", "creepy", "monster", "fyp", "comedy"]

範例輸出：
身體恐怖 (Body Horror)，AI與視覺特效 (AI & VFX)，生物入侵 (Creature Invasion)，恐怖喜劇 (Horror Comedy)，創意短片 (Creative Shorts)"""

USER_PROMPT = """\
title: {title_zh}
description: {description_zh}
tags: {tags_zh}"""


# ── DB 遷移：新增 keywords 欄位 ───────────────────────────────────────────────

def migrate_db():
    with sqlite3.connect(DB_PATH) as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(shorts)")}
        if "keywords" not in existing:
            conn.execute("ALTER TABLE shorts ADD COLUMN keywords TEXT")
            print("  ✅ 新增欄位：keywords")
        conn.commit()


# ── LLM ───────────────────────────────────────────────────────────────────────

def build_chain(model: str):
    load_dotenv(ENV_PATH)   # 從 run_all.py 匯入呼叫時也能載入 data/.env
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print(f"❌ 在 {ENV_PATH} 找不到 OPENAI_API_KEY")
        sys.exit(1)

    llm = ChatOpenAI(model=model, api_key=api_key)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("user", USER_PROMPT),
    ])
    return prompt | llm


def extract_keywords(chain, title_zh: str, description_zh: str, tags_zh: str) -> str | None:
    """呼叫 GPT-5 mini，回傳關鍵詞彙字串；失敗回傳 None。"""
    try:
        resp = chain.invoke({
            "title_zh":       title_zh or "",
            "description_zh": description_zh or "",
            "tags_zh":        tags_zh or "[]",
        })
        text = (resp.content or "").strip()
        # 去掉模型可能加上的「關鍵詞彙:」前綴
        for prefix in ("關鍵詞彙:", "關鍵詞彙：", "keywords:"):
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()
        return text or None
    except Exception as e:
        print(f"\n  ⚠️ API 呼叫失敗：{e}")
        return None


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run_keywords(model: str, limit: int | None):
    if not DB_PATH.exists():
        print(f"❌ 找不到資料庫：{DB_PATH}")
        sys.exit(1)

    migrate_db()
    enable_tracing()
    chain = build_chain(model)

    # 每次都從頭處理全部列，已有的 keywords 直接覆蓋
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT id, title_zh, description_zh, tags_zh FROM shorts"
        if limit:
            query += f" LIMIT {limit}"
        rows = conn.execute(query).fetchall()

    total = len(rows)
    if total == 0:
        print("✅ 資料庫沒有任何影片，無需處理。")
        return

    print(f"\n🔑 共 {total} 筆全部重新歸納（模型：{model}，既有 keywords 將被覆蓋）\n")

    ok = fail = 0
    t0 = time.time()

    for idx, row in enumerate(rows, 1):
        kw = extract_keywords(chain, row["title_zh"], row["description_zh"], row["tags_zh"])
        if kw is None:
            fail += 1
        else:
            _save(row["id"], kw)
            ok += 1
        _progress(idx, total, ok, fail, t0, row["id"])

    elapsed = time.time() - t0
    print(f"\n\n✅ 歸納完成：成功 {ok} | 失敗 {fail}")
    print(f"   總耗時：{elapsed/60:.1f} 分鐘（{elapsed/max(ok,1):.1f} 秒/筆）")
    if fail:
        print("   失敗的列保留原值（或 NULL），重新執行本程式會全部重跑。")


def _save(vid_id: str, keywords: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE shorts SET keywords = ? WHERE id = ?", (keywords, vid_id))
        conn.commit()


def _progress(idx, total, ok, fail, t0, vid_id):
    elapsed  = time.time() - t0
    avg_sec  = elapsed / idx
    eta_sec  = avg_sec * (total - idx)
    eta_str  = f"{eta_sec/60:.1f}m" if eta_sec >= 60 else f"{eta_sec:.0f}s"
    pct      = idx / total * 100
    bar_fill = int(pct / 5)
    bar      = "█" * bar_fill + "░" * (20 - bar_fill)
    print(
        f"\r  [{bar}] {idx}/{total} ({pct:.0f}%)  ✓{ok} ✗{fail}  ETA {eta_str}  {vid_id[:11]}…",
        end="", flush=True,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Summarize shorts.db rows into keywords with GPT-5 mini")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model name")
    parser.add_argument("--limit", type=int, default=None, help="只處理前 N 筆（測試用）")
    args = parser.parse_args()

    print("🔑 關鍵詞彙歸納器（LangChain + GPT-5 mini）")
    print(f"   DB    : {DB_PATH}")
    print(f"   Model : {args.model}")
    print(f"   .env  : {ENV_PATH}")

    run_keywords(args.model, args.limit)


if __name__ == "__main__":
    main()
