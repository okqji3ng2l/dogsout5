# -*- coding: utf-8 -*-
"""腳本與影片生成後端（FastAPI APIRouter）

提供腳本／影片相關的兩支 API，由 app.py 掛載使用：
- POST /api/script：LangChain runnable 串流產生影片腳本（qwen3 + structured output）
- POST /api/video ：把最終腳本當作 prompt 傳送給 Gemini（Veo）生成短影音

端點皆為異步函數：qwen3 串流用原生 astream、Veo 長時間輪詢用
asyncio.sleep＋執行緒池，模型運行期間不會卡住事件迴圈，其他請求可同時處理。

API 金鑰從環境變數 GEMINI_API_KEY 讀取（thumbnail_router 匯入時已載入 .env）。
"""

import asyncio
import base64
import json
import os
import sqlite3
import time
import traceback

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from google import genai
from google.genai import types
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from pydantic import BaseModel

# 風格清單、schedule.db 路徑與 scheduled_videos 表格確保函式，與縮圖頁共用
from thumbnail_router import STYLES, SCHEDULE_DB_PATH, ensure_schedule_table

video_router = APIRouter()

DURATIONS = ["8s", "16s", "24s", "32s"]

SCRIPT_MODEL = "qwen3:8b"  # 腳本生成模型

# qwen3 的 system prompt：規範輸出格式，禁止建議與細節說明文字
SCRIPT_SYSTEM_PROMPT = (
    "圍繞短影音主題建立短影音腳本，如果短影音主題為中文，則輸出中文腳本而非英文腳本，如果短影音主題為英文，則輸出英文腳本而非中文腳本\n"
    "Ex:\n"
    "短影音主題:貓咪\n"
    "[貓咪]為中文，生成中文腳本，不要生成英文腳本\n"
    "[0:00-0:03]\n"
    "貓咪死了\n\n"

    "字數嚴格遵守短影音時長限制\n"
    "中文AI短影音腳本長度(含標點):影片時長10s:25~30字 影片時長15s:55~65字 影片時長30s:110~135字 影片時長40s:150~180字\n\n"

    "Ex:\n"
    "短影音主題:cat\n"
    "[cat]為英文，生成英文腳本，不要生成中文腳本\n"
    "[0:00-0:03]\n"
    "cat died\n\n"

    "英文AI短影音腳本長度(含標點):影片時長10s:20~25字 影片時長15s:35~45字 影片時長30s:70~80字 影片時長40s:95~110字\n"
    
    "嚴禁包含任何建議以及關於細節的文字描述(如同以下範例)\n"
    "Ex:\n"
    "* **影片時長 10s (約27字):**\n"
    "   【0:00 - 0:04】 可愛貓咪，享受日光浴。\n"
    "   【0:04 - 0:08】 突如其來的狂風… 警報！\n"
    "   【0:08 - 0:10】 喵… 變成像素？\n"
    "    注意:\n\n"
    "*   腳本中的時間點僅供參考，實際調整時需根據影片的具體內容進行調整。\n"
    "*   背景音樂的選擇和音效的運用至關重要，要根據畫面內容和情緒來決定。\n\n"
    "短影音腳本以短影音內容為主，風格則採用短影音主題和短影音畫風，腳本文字長度嚴格遵守短影音時長\n"
    "短影音腳本中應該包含適當的語句停頓的時間點，以及要加入的背景音樂(加入簡單但配合影像)，短影音腳本要增加異想不到細節或極度怪異的轉折以確保影片的流暢度，短影音角色必須事實說話或發出聲音，參考以下範例"
    "Ex:短影音主題:家裡，短影音時長:8s，短影音內容:貓咪吃飯\n"
    "產出腳本:\n"
    "【0:00 - 0:03】"
    "貓咪正在吃罐頭\n"
    "【0:03 - 0:05】 (播放電吉他聲)\n"
    "有一個阿嬤突然被風吹來\n"
    "【0:06 - 0:08】\n"
    "貓咪站起來接住阿嬤，然後說了句「謝謝」"
)

# structured output 的 JSON schema：模型固定回傳 {"script": "..."}
SCRIPT_SCHEMA = {
    "title": "ShortScript",
    "type": "object",
    "properties": {
        "script": {"type": "string", "description": "短影音腳本本文"},
    },
    "required": ["script"],
}

# DuckDuckGo 搜尋工具：限定搜尋 YouTube 腳本寫作教學文章，結果注入 prompt 供模型參考
search_tool = DuckDuckGoSearchRun()
SEARCH_QUERY_FILTER = (
    "site:https://www.georgeblackman.com/write-on-time/"
    "the-3-levels-of-youtube-scriptwriting"
)


def run_search(query: str) -> str:
    """帶上網站過濾條件執行搜尋；失敗時回傳空字串，不中斷腳本生成。"""
    try:
        return search_tool.invoke(f"{SEARCH_QUERY_FILTER} {query}")
    except Exception:
        return ""


# LangChain runnable：搜尋結果 ＋ prompt template → qwen3（structured output）
# system / user prompt 都用變數帶入，避免內文的括號被當成 template 語法
SCRIPT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "{system_prompt}\n\n"
            "以下為YouTube腳本寫作技巧的搜尋結果，撰寫腳本時作為參考:\n"
            "{search_results}",
        ),
        ("human", "{user_prompt}"),
    ]
)

SCRIPT_CHAIN = (
    {
        "system_prompt": lambda x: x["system_prompt"],
        "user_prompt": lambda x: x["user_prompt"],
        "search_results": lambda x: run_search(x["input"]),
    }
    | SCRIPT_PROMPT
    | ChatOllama(
        model=SCRIPT_MODEL, temperature=0.3, reasoning=False
    ).with_structured_output(SCRIPT_SCHEMA)
)


class ScriptRequest(BaseModel):
    """POST /api/script 的請求內容。"""

    keyword: str = ""
    style: str = ""
    duration: str = ""
    content: str = ""


class VideoRequest(BaseModel):
    """POST /api/video 的請求內容。"""

    script: str = ""
    image: str = ""  # 縮圖頁產生的 data URL


def save_schedule_row(script: str = "", thumbnail: bytes | None = None) -> None:
    """生成 script／video 的當下就新增一列（只填已知欄位，其餘留空）；
    一律 INSERT，不覆蓋任何既有列。"""
    SCHEDULE_DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(SCHEDULE_DB_PATH)
    try:
        ensure_schedule_table(con)
        con.execute(
            "INSERT INTO scheduled_videos (script, thumbnail) VALUES (?, ?)",
            (script, thumbnail),
        )
        con.commit()
    finally:
        con.close()


@video_router.post("/api/script")
async def api_script(req: ScriptRequest):
    """用 LangChain runnable 串流產生影片腳本（qwen3 + structured output）。"""
    keyword = req.keyword.strip()
    style = req.style.strip()
    duration = req.duration.strip()
    content = req.content.strip()
    if not keyword:
        return JSONResponse(
            {"error": "請先在「縮圖製作」頁選擇關鍵字作為影片主題"}, status_code=400
        )
    if style not in STYLES:
        return JSONResponse(
            {"error": "請先在「縮圖製作」頁選擇風格作為影片風格"}, status_code=400
        )
    if duration not in DURATIONS:
        return JSONResponse({"error": "請選擇影片時長"}, status_code=400)

    prompt = (
        f"短影音主題:{keyword}\n"
        f"短影音畫風:{style}\n"
        f"短影音時長:{duration}(s表示秒鐘，Ex:10s表示短影長度約為10秒鐘)\n"
        f"短影音內容:{content or '（不限，自由發揮）'}\n"
    )

    async def stream_script():
        """逐段把累積中的腳本以 NDJSON 送給前端，最後補上 prompt。"""
        final_script = ""
        try:
            # astream 為原生 async 串流，等待模型產出時不會卡住事件迴圈
            async for chunk in SCRIPT_CHAIN.astream(
                {
                    "system_prompt": SCRIPT_SYSTEM_PROMPT,
                    "user_prompt": prompt,
                    "input": keyword,  # 搜尋關鍵字（帶入 search_tool）
                }
            ):
                # stream 產出的是逐步累積的部分 JSON dict
                if isinstance(chunk, dict) and "script" in chunk:
                    final_script = chunk["script"]  # 累積內容，最後一次即為最終腳本
                    yield json.dumps(
                        {"script": final_script}, ensure_ascii=False
                    ) + "\n"
        except Exception as e:
            yield json.dumps(
                {"error": f"ollama 呼叫失敗：{e}"}, ensure_ascii=False
            ) + "\n"
            return
        if final_script:
            # 產生 script 的當下就存入 database/schedule.db（新增一列，只填 script）
            await asyncio.to_thread(save_schedule_row, final_script, None)
        yield json.dumps({"done": True, "prompt": prompt}, ensure_ascii=False) + "\n"

    return StreamingResponse(stream_script(), media_type="application/x-ndjson")


# Gemini 影片生成模型（Veo），可用環境變數覆寫
VIDEO_MODEL = os.getenv("GEMINI_VIDEO_MODEL", "veo-3.1-generate-preview")

# 輪詢逾時秒數：避免 operation 一直不 done 時無限等待，可用環境變數覆寫
VIDEO_POLL_TIMEOUT = float(os.getenv("GEMINI_VIDEO_TIMEOUT", "600"))

@video_router.post("/api/video")
async def api_video(req: VideoRequest):
    """腳本最後確認後，把最終腳本當作 prompt 傳送給 Gemini（Veo）生成短影音。

    若前端帶入縮圖製作頁產生的縮圖，會把縮圖一併傳給 Veo（image-to-video），
    並在 prompt 加上「短影音縮圖設定為附上的圖片」。

    Veo 是長時間作業：SDK 呼叫丟到執行緒池、輪詢間隔用 asyncio.sleep，
    生成影片的幾分鐘內事件迴圈仍可服務其他請求。
    """
    script = req.script.strip()
    image_url = req.image.strip()  # 縮圖頁產生的 data URL
    if not script:
        return JSONResponse(
            {"error": "腳本是空的，請先生成或編輯腳本"}, status_code=400
        )
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return JSONResponse({"error": "尚未設定 GEMINI_API_KEY"}, status_code=500)

    prompt = script
    image = None
    thumbnail_bytes = None  # 當下使用的縮圖原始位元組，成功後連同 script 存入 schedule.db
    if image_url.startswith("data:"):
        header, b64_data = image_url.split(",", 1)
        mime = header[len("data:"):].split(";")[0] or "image/png"
        thumbnail_bytes = base64.b64decode(b64_data)
        image = types.Image(image_bytes=thumbnail_bytes, mime_type=mime)
        prompt = f"{script}\n短影音縮圖設定為附上的圖片"

    try:
        client = genai.Client(api_key=api_key)
        kwargs = {
            "model": VIDEO_MODEL,
            "prompt": prompt,
        }
        if image is not None:
            kwargs["image"] = image
        operation = await asyncio.to_thread(
            client.models.generate_videos, **kwargs
        )
        poll_start = time.monotonic()
        while not operation.done:  # Veo 是長時間作業，需輪詢直到完成
            if time.monotonic() - poll_start > VIDEO_POLL_TIMEOUT:
                return JSONResponse(
                    {
                        "error": (
                            f"Gemini 影片生成逾時：輪詢超過 "
                            f"{int(VIDEO_POLL_TIMEOUT)} 秒仍未完成（operation="
                            f"{getattr(operation, 'name', operation)}）"
                        )
                    },
                    status_code=504,
                )
            await asyncio.sleep(10)
            operation = await asyncio.to_thread(client.operations.get, operation)
        if operation.error:
            return JSONResponse(
                {"error": f"Gemini 影片生成失敗：{operation.error}"}, status_code=502
            )
        videos = (operation.response and operation.response.generated_videos) or []
        if not videos:
            return JSONResponse(
                {"error": "Gemini 沒有回傳影片（可能被安全機制過濾）"}, status_code=502
            )
        video_file = videos[0].video
        await asyncio.to_thread(client.files.download, file=video_file)
        video_bytes = video_file.video_bytes
    except Exception:
        # 回傳完整錯誤堆疊（不只是 str(e)），方便定位 SDK / 網路等實際失敗原因
        return JSONResponse(
            {"error": f"Gemini 影片生成失敗：\n{traceback.format_exc()}"},
            status_code=502,
        )

    # 生成 video 的當下就存入 database/schedule.db：最終確認的 script，
    # 並存當下使用的縮圖（若有）；新增一列，url／upload_time 等留空
    await asyncio.to_thread(save_schedule_row, script, thumbnail_bytes)

    # 直接以 data URL 回傳給前端播放，不存檔
    b64 = base64.b64encode(video_bytes).decode("utf-8")
    return {"video": f"data:video/mp4;base64,{b64}"}
