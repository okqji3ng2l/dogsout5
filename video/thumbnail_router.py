# -*- coding: utf-8 -*-
"""縮圖生成後端（FastAPI APIRouter）

提供縮圖相關的兩支 API，由 app.py 掛載使用：
- GET  /api/keywords：查詢 database/shorts.db 的 cluster 欄位，回傳 cluster 名稱（中／英文）
- POST /api/generate：呼叫 Gemini 生成縮圖

端點皆為異步函數：資料庫查詢、Gemini 呼叫等耗時工作會交給執行緒池或
原生 async API，模型運行期間不會卡住事件迴圈，其他請求可同時處理。

API 金鑰從環境變數 GEMINI_API_KEY 讀取（匯入時已載入 .env）。
"""

import asyncio
import base64
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_google_genai import ChatGoogleGenerativeAI

# shorts.db 位於 dogsout/database（本檔在 video/ 下，上一層即 dogsout）
DOGSOUT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = DOGSOUT_ROOT / "database" / "shorts.db"

# schedule.db 與 manage/upload_router.py 共用同一張 scheduled_videos 表；
# 生成 thumbnail／script／video 的當下各自新增一列（只填已知欄位）
SCHEDULE_DB_PATH = DOGSOUT_ROOT / "database" / "schedule.db"

# 讀取 .env（先找目前目錄，再找 video 目錄）
load_dotenv()
load_dotenv(Path(__file__).resolve().parent / ".env")

# 設置 LangSmith：os.environ 為 process 層級，本檔與 video_router 的
# chain（含 qwen3/ChatOllama）都會自動上報追蹤紀錄，不需各自重複設定
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
os.environ["LANGCHAIN_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")
# "LANGCHAIN_PROJECT" 名稱會顯示在 LangSmith 上
os.environ["LANGCHAIN_PROJECT"] = "dogsout"

thumbnail_router = APIRouter()

DEFAULT_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image-preview")

STYLES = {
    "浮誇": "鮮豔的顏色或者粗體的大字",
    "寫實": "接近現實的繪畫風格",
    "卡通": "人物採用Q版的造型",
}

# cluster 欄位格式：中文名稱 (English Name)
CLUSTER_RE = re.compile(r"^(.*?)\s*\((.*)\)\s*$")


def extract_image_bytes(response) -> bytes | None:
    """從模型回覆中取出生成的圖片（base64 data URL 區塊）。"""
    blocks = response.content
    if isinstance(blocks, str):
        return None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        url = ""
        if block.get("type") == "image_url":
            url = block.get("image_url", {}).get("url", "")
        elif block.get("type") == "image" and "data" in block:
            return base64.b64decode(block["data"])
        if url.startswith("data:image/"):
            return base64.b64decode(url.split(",", 1)[1])
    return None


def extract_text(response) -> str:
    blocks = response.content
    if isinstance(blocks, str):
        return blocks
    return "\n".join(
        b.get("text", "") if isinstance(b, dict) else str(b)
        for b in blocks
        if not isinstance(b, dict) or b.get("type") == "text"
    ).strip()


def ensure_schedule_table(con: sqlite3.Connection) -> None:
    """確保 schedule.db 的 scheduled_videos 表格與 url／script／thumbnail
    欄位存在，供本檔與 video_router.py 各自獨立寫入時使用。完整的欄位
    遷移（含舊版 video_id 改名為 url）由 manage/upload_router.py 的
    init_db() 負責；這裡只補齊，確保即使單獨執行 video/app.py（未跑
    upload_router 的 init_db）也能正常寫入，不會相互覆蓋既有資料。"""
    con.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_videos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT DEFAULT '',
            title       TEXT DEFAULT '',
            description TEXT DEFAULT '',
            upload_time TEXT DEFAULT '',
            comment     TEXT DEFAULT '',
            commented   INTEGER DEFAULT 0,
            script      TEXT DEFAULT '',
            thumbnail   BLOB
        )
    """)
    cols = {row[1] for row in con.execute("PRAGMA table_info(scheduled_videos)")}
    if "script" not in cols:
        con.execute("ALTER TABLE scheduled_videos ADD COLUMN script TEXT DEFAULT ''")
    if "thumbnail" not in cols:
        con.execute("ALTER TABLE scheduled_videos ADD COLUMN thumbnail BLOB")
    con.commit()


def save_thumbnail(image_bytes: bytes) -> None:
    """生成縮圖的當下就新增一列（只填 thumbnail，其餘留空）；
    一律 INSERT，不覆蓋任何既有列。"""
    DOGSOUT_ROOT.joinpath("database").mkdir(exist_ok=True)
    con = sqlite3.connect(SCHEDULE_DB_PATH)
    try:
        ensure_schedule_table(con)
        con.execute(
            "INSERT INTO scheduled_videos (thumbnail) VALUES (?)", (image_bytes,)
        )
        con.commit()
    finally:
        con.close()


def load_clusters() -> dict[str, list[str]]:
    """查詢 shorts.db 的 cluster 欄位，拆出中／英文 cluster 名稱（依出現次數排序）。"""
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"找不到資料庫 {DB_PATH}")
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT cluster FROM shorts WHERE cluster IS NOT NULL AND cluster != ''"
        ).fetchall()
    finally:
        con.close()

    zh_counter: Counter[str] = Counter()
    en_counter: Counter[str] = Counter()
    for (value,) in rows:
        m = CLUSTER_RE.match(value.strip())
        if m:
            zh, en = m.group(1).strip(), m.group(2).strip()
            if zh:
                zh_counter[zh] += 1
            if en:
                en_counter[en] += 1
        else:  # 沒有括號的值：含 CJK 字元歸中文，否則歸英文
            name = value.strip()
            if any("一" <= ch <= "鿿" for ch in name):
                zh_counter[name] += 1
            else:
                en_counter[name] += 1

    result: dict[str, list[str]] = {}
    if zh_counter:
        result["chinese"] = [name for name, _ in zh_counter.most_common()]
    if en_counter:
        result["english"] = [name for name, _ in en_counter.most_common()]
    return result


@thumbnail_router.get("/api/keywords")
async def api_keywords():
    """查詢 database/shorts.db 的 cluster 欄位，回傳 cluster 名稱（中／英文）。"""
    try:
        # sqlite 查詢丟到執行緒池執行，避免卡住事件迴圈
        return await asyncio.to_thread(load_clusters)
    except Exception as e:  # 資料庫不存在、讀取失敗等狀況
        return JSONResponse({"error": f"讀取 cluster 失敗：{e}"}, status_code=500)


@thumbnail_router.post("/api/generate")
async def api_generate(
    keyword: str = Form(""),
    style: str = Form(""),
    extra: str = Form(""),
    image: UploadFile | None = File(None),
):
    """依固定格式組 prompt，呼叫 Gemini 生成縮圖。"""
    keyword = keyword.strip()
    style = style.strip()
    extra = extra.strip()
    if not keyword:
        return JSONResponse({"error": "請先選擇關鍵字"}, status_code=400)
    if style not in STYLES:
        return JSONResponse({"error": "請選擇縮圖風格"}, status_code=400)

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return JSONResponse({"error": "尚未設定 GEMINI_API_KEY"}, status_code=500)

    content = []
    has_image = image is not None and image.filename
    if has_image:
        mime = image.content_type or "image/jpeg"
        data = base64.b64encode(await image.read()).decode("utf-8")
        content.append({"type": "text", "text": "[Image #1]"})
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}
        )

    # 依規定格式組 prompt
    prompt = (
        "依照以下要求，為我創作出吸引人的Youtube短影音縮圖:"
        f"縮圖主題:{keyword}\n"
        f"縮圖風格選擇:{style}:{STYLES[style]}\n"
        f"基於以下圖片進行修改:{'[Image #1]' if has_image else '無'}\n"
        f"額外要求:{extra or '無'}"
        "縮圖盡量以圖片為主，減少文字，除非額外要求特別強調增強文字呈現"
    )
    content.append({"type": "text", "text": prompt})

    llm = ChatGoogleGenerativeAI(
        model=DEFAULT_MODEL, google_api_key=api_key,
        response_modalities=["TEXT", "IMAGE"],  # 沒有這行模型只會回文字
    )
    # chain 整合 prompt 與 model；訊息含圖片區塊，用 MessagesPlaceholder 帶入
    chain = (
        ChatPromptTemplate.from_messages([MessagesPlaceholder("messages")]) | llm
    )
    try:
        # 用 LangChain 原生 async 呼叫，等待模型回覆時可同時處理其他請求
        response = await chain.ainvoke(
            {"messages": [HumanMessage(content=content)]}
        )
    except Exception as e:  # API 錯誤（配額、網路等）原樣回報給前端
        return JSONResponse({"error": f"Gemini 呼叫失敗：{e}"}, status_code=502)

    image_bytes = extract_image_bytes(response)
    if image_bytes is None:
        text = extract_text(response)
        return JSONResponse({"error": f"模型沒有回傳圖片：{text}"}, status_code=502)

    # 生成縮圖的當下就存入 database/schedule.db（新增一列，只填 thumbnail）
    await asyncio.to_thread(save_thumbnail, image_bytes)

    # 回傳圖片給前端顯示
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return {"image": f"data:image/png;base64,{b64}"}
