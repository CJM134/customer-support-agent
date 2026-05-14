from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import tickets
from app.core.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Customer Support Agent API",
    description="客服工单处理 Agent：分类、知识库检索、回复草稿、人工介入判断和量化指标。",
    version="0.1.0",
)

logger.info("客服工单 Agent API 初始化中")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tickets.router, prefix="/api", tags=["tickets"])

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    logger.info("静态文件已挂载: %s", STATIC_DIR)

logger.info("Agent API 路由已注册 (prefix=/api)")


@app.get("/")
async def index():
    logger.debug("返回 index.html 页面")
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}
