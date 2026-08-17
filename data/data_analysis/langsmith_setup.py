"""
langsmith_setup.py — 啟用 LangSmith 追蹤（data_analysis 內所有用到
Ollama／LangChain 模型的程式共用）

沿用 video/thumbnail_router.py 的做法：讀 data/.env 的 LANGCHAIN_API_KEY，
設定 LANGCHAIN_TRACING_V2 等環境變數後，LangChain runnable（ChatOllama、
OllamaEmbeddings 等）呼叫時會自動把追蹤紀錄上報到 LangSmith。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent.parent / ".env"   # data/.env


def enable_tracing(project: str = "dogsout") -> None:
    load_dotenv(ENV_PATH)
    if not os.getenv("LANGCHAIN_API_KEY"):
        print(f"⚠️ 在 {ENV_PATH} 找不到 LANGCHAIN_API_KEY，略過 LangSmith 追蹤")
        return
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"
    os.environ["LANGCHAIN_PROJECT"] = project
