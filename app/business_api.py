from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "business.db"
FEEDBACK_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "feedback.db"

CUSTOMERS = [
    ("C1001", "李女士", "standard", 860.0, "normal"),
    ("C1002", "王先生", "standard", 420.0, "normal"),
    ("C1005", "赵女士", "vip", 12800.0, "high"),
    ("C1007", "星河采购部", "enterprise", 93600.0, "high"),
    ("C-DEMO", "演示客户", "standard", 1200.0, "normal"),
]

ORDERS = [
    ("O90001", "C1001", "paid_not_shipped", 299.0, "2026-05-06 10:20:00", "便携榨汁杯"),
    ("O90002", "C1002", "shipped", 159.0, "2026-05-05 14:11:00", "无线充电器"),
    ("O90005", "C1005", "delivered", 899.0, "2026-05-01 09:01:00", "智能空气净化器"),
    ("O90007", "C1007", "shipped", 28600.0, "2026-05-04 16:20:00", "企业采购套装"),
    ("O-DEMO", "C-DEMO", "shipped", 199.0, "2026-05-08 11:00:00", "演示商品"),
]

SHIPMENTS = [
    ("O90002", "顺丰", "SF90002", "delivered_disputed", "系统显示已签收，客户反馈未收到", "2026-05-08 18:30:00"),
    ("O90005", "京东物流", "JD90005", "delivered", "已签收", "2026-05-03 15:22:00"),
    ("O90007", "德邦", "DB90007", "stalled", "干线运输中，超过48小时未更新", "2026-05-06 09:40:00"),
    ("O-DEMO", "顺丰", "SFDEMO", "delivered_disputed", "系统显示已签收，客户反馈未收到", "2026-05-08 20:10:00"),
]

app = FastAPI(
    title="Mock Business System API",
    description="CRM, OMS, and logistics API backed by SQLite.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ProcessOrderRequest(BaseModel):
    source: str = "manual"
    resolution_note: str | None = None


class AgentAnalysisRequest(BaseModel):
    ticket_id: str
    customer_id: str | None = None
    order_id: str | None = None
    message: str
    category: str
    priority: str
    confidence: float
    reply_source: str
    reply_draft: str
    should_escalate: bool
    escalation_reason: str | None = None
    business_sync_success: bool = False
    business_sync_message: str | None = None
    estimated_minutes_saved: float = 0


class AgentAnalysisRevisionRequest(BaseModel):
    revised_reply: str
    editor: str | None = None
    notes: str | None = None


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(DB_PATH))
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    logger.info("业务数据库初始化: %s", DB_PATH)
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                segment TEXT NOT NULL,
                lifetime_value REAL NOT NULL,
                risk_level TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                customer_id TEXT,
                status TEXT NOT NULL,
                amount REAL NOT NULL,
                paid_at TEXT NOT NULL,
                item_names TEXT NOT NULL,
                support_status TEXT NOT NULL DEFAULT 'pending',
                support_source TEXT,
                processed_at TEXT,
                resolution_note TEXT
            )
            """
        )
        ensure_order_columns(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shipments (
                order_id TEXT PRIMARY KEY,
                carrier TEXT NOT NULL,
                tracking_no TEXT NOT NULL,
                status TEXT NOT NULL,
                latest_event TEXT NOT NULL,
                last_updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ticket_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                customer_id TEXT,
                order_id TEXT,
                message TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                confidence REAL NOT NULL,
                reply_source TEXT NOT NULL,
                reply_draft TEXT NOT NULL,
                should_escalate INTEGER NOT NULL,
                escalation_reason TEXT,
                business_sync_success INTEGER NOT NULL,
                business_sync_message TEXT,
                estimated_minutes_saved REAL NOT NULL,
                final_reply TEXT,
                final_reply_source TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                review_notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        ensure_analysis_columns(connection)
        connection.executemany(
            """
            INSERT OR IGNORE INTO customers (
                customer_id, name, segment, lifetime_value, risk_level
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            CUSTOMERS,
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO orders (
                order_id, customer_id, status, amount, paid_at, item_names
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ORDERS,
        )
        connection.executemany(
            """
            UPDATE orders
            SET customer_id = ?, status = ?, amount = ?, paid_at = ?, item_names = ?
            WHERE order_id = ?
            """,
            [(customer_id, status, amount, paid_at, item_names, order_id) for order_id, customer_id, status, amount, paid_at, item_names in ORDERS],
        )
        connection.executemany(
            """
            INSERT OR IGNORE INTO shipments (
                order_id, carrier, tracking_no, status, latest_event, last_updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            SHIPMENTS,
        )

    count = connection.execute("SELECT COUNT(*) as cnt FROM customers").fetchone()["cnt"]
    logger.info("数据库就绪: %d 个客户, %d 个订单, %d 个物流记录已初始化", count, len(ORDERS), len(SHIPMENTS))


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("业务后台启动中，监听端口 8011")
    init_db()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def admin_home() -> str:
    logger.debug("返回管理后台首页")
    return ADMIN_HTML


@app.get("/admin", response_class=HTMLResponse)
async def admin_page() -> str:
    logger.debug("返回管理后台页面")
    return ADMIN_HTML


@app.get("/admin/escalations", response_class=HTMLResponse)
async def escalation_page() -> str:
    logger.debug("返回转人工队列页面")
    return ESCALATION_HTML


@app.get("/api/admin/orders")
async def list_admin_orders() -> dict[str, list[dict[str, Any]]]:
    logger.info("查询所有管理订单")
    rows = fetch_all(
        """
        SELECT
            o.order_id,
            o.customer_id,
            o.status AS order_status,
            o.amount,
            o.paid_at,
            o.item_names,
            o.support_status,
            o.support_source,
            o.processed_at,
            o.resolution_note,
            c.name AS customer_name,
            c.segment,
            c.risk_level,
            s.carrier,
            s.tracking_no,
            s.status AS shipment_status,
            s.latest_event,
            s.last_updated_at,
            (
                SELECT ta.category
                FROM ticket_analyses ta
                WHERE ta.order_id = o.order_id
                ORDER BY ta.id DESC
                LIMIT 1
            ) AS latest_agent_category,
            (
                SELECT ta.priority
                FROM ticket_analyses ta
                WHERE ta.order_id = o.order_id
                ORDER BY ta.id DESC
                LIMIT 1
            ) AS latest_agent_priority,
            (
                SELECT ta.reply_source
                FROM ticket_analyses ta
                WHERE ta.order_id = o.order_id
                ORDER BY ta.id DESC
                LIMIT 1
            ) AS latest_agent_reply_source,
            (
                SELECT ta.final_reply_source
                FROM ticket_analyses ta
                WHERE ta.order_id = o.order_id
                ORDER BY ta.id DESC
                LIMIT 1
            ) AS latest_agent_final_reply_source,
            (
                SELECT ta.created_at
                FROM ticket_analyses ta
                WHERE ta.order_id = o.order_id
                ORDER BY ta.id DESC
                LIMIT 1
            ) AS latest_agent_created_at
            ,
            (
                SELECT ta.reply_draft
                FROM ticket_analyses ta
                WHERE ta.order_id = o.order_id
                ORDER BY ta.id DESC
                LIMIT 1
            ) AS latest_agent_reply_draft,
            (
                SELECT ta.final_reply
                FROM ticket_analyses ta
                WHERE ta.order_id = o.order_id
                ORDER BY ta.id DESC
                LIMIT 1
            ) AS latest_agent_final_reply,
            (
                SELECT ta.reviewed_by
                FROM ticket_analyses ta
                WHERE ta.order_id = o.order_id
                ORDER BY ta.id DESC
                LIMIT 1
            ) AS latest_agent_reviewed_by,
            (
                SELECT ta.reviewed_at
                FROM ticket_analyses ta
                WHERE ta.order_id = o.order_id
                ORDER BY ta.id DESC
                LIMIT 1
            ) AS latest_agent_reviewed_at
        FROM orders o
        LEFT JOIN customers c ON c.customer_id = o.customer_id
        LEFT JOIN shipments s ON s.order_id = o.order_id
        ORDER BY
            CASE o.support_status WHEN 'pending' THEN 0 ELSE 1 END,
            o.paid_at DESC
        """,
        (),
    )
    orders = [admin_order_to_dict(row) for row in rows]
    logger.info("返回 %d 条管理订单", len(orders))
    return {"data": orders}


@app.post("/api/admin/reset-demo")
async def reset_demo_data() -> dict[str, Any]:
    logger.warning("重置所有演示数据 — 清空调单分析和反馈记录")
    with connect() as connection:
        connection.execute("DELETE FROM ticket_analyses")
        connection.execute(
            """
            UPDATE orders
            SET support_status = 'pending',
                support_source = NULL,
                processed_at = NULL,
                resolution_note = NULL
            """
        )
        connection.executemany(
            """
            UPDATE orders
            SET customer_id = ?, status = ?, amount = ?, paid_at = ?, item_names = ?
            WHERE order_id = ?
            """,
            [(customer_id, status, amount, paid_at, item_names, order_id) for order_id, customer_id, status, amount, paid_at, item_names in ORDERS],
        )
    reset_feedback_data()
    logger.info("演示数据重置完成")
    return {"success": True, "message": "演示数据已重置"}


@app.post("/api/admin/agent-analyses")
async def save_agent_analysis(request: AgentAnalysisRequest) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "保存 Agent 分析记录: ticket=%s category=%s priority=%s escalate=%s",
        request.ticket_id, request.category, request.priority, request.should_escalate,
    )
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO ticket_analyses (
                ticket_id,
                customer_id,
                order_id,
                message,
                category,
                priority,
                confidence,
                reply_source,
                reply_draft,
                should_escalate,
                escalation_reason,
                business_sync_success,
                business_sync_message,
                estimated_minutes_saved,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.ticket_id,
                request.customer_id,
                request.order_id,
                request.message,
                request.category,
                request.priority,
                request.confidence,
                request.reply_source,
                request.reply_draft,
                int(request.should_escalate),
                request.escalation_reason,
                int(request.business_sync_success),
                request.business_sync_message,
                request.estimated_minutes_saved,
                created_at,
            ),
        )
    return {"success": True, "id": cursor.lastrowid, "created_at": created_at}


@app.get("/api/admin/agent-analyses")
async def list_agent_analyses(limit: int = 50) -> dict[str, list[dict[str, Any]]]:
    logger.debug("查询 Agent 分析记录 (limit=%d)", limit)
    rows = fetch_all(
        """
        SELECT *
        FROM ticket_analyses
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return {"data": [analysis_to_dict(row) for row in rows]}


@app.post("/api/admin/agent-analyses/{ticket_id}/revision")
async def save_agent_analysis_revision(ticket_id: str, request: AgentAnalysisRevisionRequest) -> dict[str, Any]:
    reviewed_at = datetime.now(timezone.utc).isoformat()
    logger.info("保存人工复核结果: ticket=%s editor=%s", ticket_id, request.editor)
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE ticket_analyses
            SET final_reply = ?,
                final_reply_source = 'human',
                reviewed_by = ?,
                reviewed_at = ?,
                review_notes = ?
            WHERE ticket_id = ?
            """,
            (
                request.revised_reply,
                request.editor,
                reviewed_at,
                request.notes,
                ticket_id,
            ),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Agent analysis not found")

    row = fetch_one("SELECT * FROM ticket_analyses WHERE ticket_id = ?", (ticket_id,))
    return {"success": True, "data": analysis_to_dict(row) if row else None}


@app.get("/api/admin/escalations")
async def list_escalations() -> dict[str, list[dict[str, Any]]]:
    logger.debug("查询转人工队列")
    rows = fetch_all(
        """
        SELECT
            ta.*,
            o.support_status,
            c.name AS customer_name,
            c.segment,
            c.risk_level
        FROM ticket_analyses ta
        LEFT JOIN orders o ON o.order_id = ta.order_id
        LEFT JOIN customers c ON c.customer_id = ta.customer_id
        WHERE ta.should_escalate = 1
        ORDER BY ta.id DESC
        """,
        (),
    )
    rows_list = [escalation_to_dict(row) for row in rows]
    logger.info("返回 %d 条转人工工单", len(rows_list))
    return {"data": rows_list}


@app.post("/api/admin/orders/{order_id}/process")
async def mark_order_processed(order_id: str, request: ProcessOrderRequest | None = None) -> dict[str, Any]:
    processed_at = datetime.now(timezone.utc).isoformat()
    source = request.source if request else "manual"
    note = (
        request.resolution_note
        if request and request.resolution_note
        else "已核实订单与物流状态，客服已完成处理并同步客户。"
    )
    logger.info("处理订单: order=%s source=%s", order_id, source)
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE orders
            SET support_status = 'processed',
                support_source = ?,
                processed_at = ?,
                resolution_note = ?
            WHERE order_id = ?
            """,
            (request.source if request else "manual", processed_at, note, order_id),
        )
        if cursor.rowcount == 0:
            logger.warning("订单处理失败，未找到订单: %s", order_id)
            raise HTTPException(status_code=404, detail="Order not found")

    row = fetch_one("SELECT * FROM orders WHERE order_id = ?", (order_id,))
    logger.info("订单 %s 已标记为已处理", order_id)
    return {"success": True, "data": dict(row) if row else None}


@app.post("/api/admin/orders/{order_id}/reopen")
async def reopen_order(order_id: str) -> dict[str, Any]:
    logger.info("重新打开订单: order=%s", order_id)
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE orders
            SET support_status = 'pending',
                support_source = NULL,
                processed_at = NULL,
                resolution_note = NULL
            WHERE order_id = ?
            """,
            (order_id,),
        )
        if cursor.rowcount == 0:
            logger.warning("重新打开失败，未找到订单: %s", order_id)
            raise HTTPException(status_code=404, detail="Order not found")

    row = fetch_one("SELECT * FROM orders WHERE order_id = ?", (order_id,))
    logger.info("订单 %s 已重新打开（恢复为待处理）", order_id)
    return {"success": True, "data": dict(row) if row else None}


@app.get("/customers/{customer_id}")
async def get_customer(customer_id: str) -> dict[str, Any]:
    logger.info("查询客户: %s", customer_id)
    row = fetch_one("SELECT * FROM customers WHERE customer_id = ?", (customer_id,))
    if row is None:
        logger.warning("客户不存在: %s", customer_id)
        raise HTTPException(status_code=404, detail="Customer not found")
    return {"data": dict(row)}


@app.get("/orders/{order_id}")
async def get_order(order_id: str) -> dict[str, Any]:
    logger.info("查询订单: %s", order_id)
    row = fetch_one("SELECT * FROM orders WHERE order_id = ?", (order_id,))
    if row is None:
        logger.warning("订单不存在: %s", order_id)
        raise HTTPException(status_code=404, detail="Order not found")

    order = dict(row)
    order["items"] = [item for item in order.pop("item_names").split("|") if item]
    return {"data": order}


@app.get("/shipments/by-order/{order_id}")
async def get_shipment_by_order(order_id: str) -> dict[str, Any]:
    logger.info("查询订单物流: %s", order_id)
    row = fetch_one("SELECT * FROM shipments WHERE order_id = ?", (order_id,))
    if row is None:
        logger.warning("物流信息不存在: %s", order_id)
        raise HTTPException(status_code=404, detail="Shipment not found")
    return {"data": dict(row)}


def fetch_one(sql: str, params: tuple[str, ...]) -> sqlite3.Row | None:
    with connect() as connection:
        return connection.execute(sql, params).fetchone()


def fetch_all(sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
    with connect() as connection:
        return connection.execute(sql, params).fetchall()


def reset_feedback_data() -> None:
    if not FEEDBACK_DB_PATH.exists():
        return

    connection = sqlite3.connect(str(FEEDBACK_DB_PATH))
    try:
        with connection:
            connection.execute("DELETE FROM feedback")
    except sqlite3.OperationalError:
        return
    finally:
        connection.close()


def ensure_order_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(orders)").fetchall()
    }
    migrations = {
        "customer_id": "ALTER TABLE orders ADD COLUMN customer_id TEXT",
        "support_status": "ALTER TABLE orders ADD COLUMN support_status TEXT NOT NULL DEFAULT 'pending'",
        "support_source": "ALTER TABLE orders ADD COLUMN support_source TEXT",
        "processed_at": "ALTER TABLE orders ADD COLUMN processed_at TEXT",
        "resolution_note": "ALTER TABLE orders ADD COLUMN resolution_note TEXT",
    }
    for column, sql in migrations.items():
        if column not in existing_columns:
            connection.execute(sql)


def ensure_analysis_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(ticket_analyses)").fetchall()
    }
    migrations = {
        "final_reply": "ALTER TABLE ticket_analyses ADD COLUMN final_reply TEXT",
        "final_reply_source": "ALTER TABLE ticket_analyses ADD COLUMN final_reply_source TEXT",
        "reviewed_by": "ALTER TABLE ticket_analyses ADD COLUMN reviewed_by TEXT",
        "reviewed_at": "ALTER TABLE ticket_analyses ADD COLUMN reviewed_at TEXT",
        "review_notes": "ALTER TABLE ticket_analyses ADD COLUMN review_notes TEXT",
    }
    for column, sql in migrations.items():
        if column not in existing_columns:
            connection.execute(sql)


def admin_order_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item_names = row["item_names"] or ""
    return {
        "order_id": row["order_id"],
        "customer_id": row["customer_id"],
        "customer_name": row["customer_name"],
        "segment": row["segment"],
        "risk_level": row["risk_level"],
        "order_status": row["order_status"],
        "amount": row["amount"],
        "paid_at": row["paid_at"],
        "items": [item for item in item_names.split("|") if item],
        "support_status": row["support_status"],
        "support_source": row["support_source"],
        "processed_at": row["processed_at"],
        "resolution_note": row["resolution_note"],
        "carrier": row["carrier"],
        "tracking_no": row["tracking_no"],
        "shipment_status": row["shipment_status"],
        "latest_event": row["latest_event"],
        "last_updated_at": row["last_updated_at"],
        "latest_agent_category": row["latest_agent_category"],
        "latest_agent_priority": row["latest_agent_priority"],
        "latest_agent_reply_source": row["latest_agent_reply_source"],
        "latest_agent_final_reply_source": row["latest_agent_final_reply_source"],
        "latest_agent_created_at": row["latest_agent_created_at"],
        "latest_agent_reply_draft": row["latest_agent_reply_draft"],
        "latest_agent_final_reply": row["latest_agent_final_reply"],
        "latest_agent_reviewed_by": row["latest_agent_reviewed_by"],
        "latest_agent_reviewed_at": row["latest_agent_reviewed_at"],
    }


def analysis_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["should_escalate"] = bool(item["should_escalate"])
    item["business_sync_success"] = bool(item["business_sync_success"])
    item["display_reply"] = item.get("final_reply") or item.get("reply_draft")
    item["display_reply_source"] = item.get("final_reply_source") or item.get("reply_source")
    return item


def escalation_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = analysis_to_dict(row)
    item["customer_name"] = row["customer_name"]
    item["segment"] = row["segment"]
    item["risk_level"] = row["risk_level"]
    item["support_status"] = row["support_status"]
    return item


ADMIN_HTML = """
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>业务后台 · 订单处理</title>
    <style>
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background: #f5f7fa;
        color: #172033;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
        letter-spacing: 0;
      }
      header {
        background: #fff;
        border-bottom: 1px solid #dce3ea;
      }
      .shell {
        width: min(1180px, calc(100vw - 32px));
        margin: 0 auto;
      }
      .topbar {
        min-height: 72px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
      }
      h1 { margin: 0; font-size: 24px; }
      .sub { margin-top: 4px; color: #667085; font-size: 13px; }
      main { padding: 24px 0 40px; }
      .stats {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
        margin-bottom: 16px;
      }
      .stat, .panel {
        background: #fff;
        border: 1px solid #dce3ea;
        border-radius: 8px;
      }
      .stat { padding: 14px; }
      .label { color: #667085; font-size: 13px; }
      .value { margin-top: 8px; font-size: 28px; font-weight: 800; }
      .panel { overflow: hidden; }
      table {
        width: 100%;
        border-collapse: collapse;
      }
      th, td {
        padding: 13px 14px;
        text-align: left;
        border-bottom: 1px solid #edf1f5;
        vertical-align: top;
        font-size: 14px;
      }
      th {
        background: #f9fafb;
        color: #526071;
        font-size: 12px;
        font-weight: 700;
      }
      .muted { color: #667085; line-height: 1.5; }
      .badge {
        display: inline-flex;
        min-height: 26px;
        align-items: center;
        border-radius: 999px;
        padding: 4px 10px;
        font-size: 12px;
        font-weight: 700;
      }
      .pending { background: #fff7ed; color: #b45309; }
      .processed { background: #ecfdf3; color: #027a48; }
      .risk { background: #fef2f2; color: #b91c1c; }
      button {
        border: 0;
        border-radius: 6px;
        background: #0f766e;
        color: #fff;
        padding: 9px 12px;
        font-weight: 700;
        cursor: pointer;
      }
      button:disabled {
        background: #98a2b3;
        cursor: default;
      }
      .ghost {
        background: #344054;
        margin-top: 8px;
      }
      .modal {
        position: fixed;
        inset: 0;
        display: none;
        align-items: center;
        justify-content: center;
        padding: 24px;
        background: rgba(15, 23, 42, 0.45);
      }
      .modal.open { display: flex; }
      .dialog {
        width: min(760px, calc(100vw - 32px));
        max-height: calc(100vh - 48px);
        overflow: auto;
        background: #fff;
        border-radius: 8px;
        border: 1px solid #dce3ea;
        padding: 18px;
      }
      .dialog h2 { margin: 0 0 12px; font-size: 20px; }
      .reply-box {
        white-space: pre-wrap;
        border: 1px solid #dce3ea;
        border-radius: 8px;
        padding: 12px;
        margin: 8px 0 14px;
        line-height: 1.6;
        background: #fbfcfd;
      }
      @media (max-width: 900px) {
        .stats { grid-template-columns: 1fr 1fr; }
        table { min-width: 980px; }
        .panel { overflow-x: auto; }
      }
    </style>
  </head>
  <body>
    <header>
      <div class="shell topbar">
        <div>
          <h1>业务后台 · 订单处理</h1>
          <div class="sub">SQLite CRM / OMS / 物流数据服务，供 Agent 实时查询</div>
        </div>
        <div style="display:flex; gap:10px; align-items:center">
          <a href="/admin/escalations" style="color:#0f766e;font-weight:700;text-decoration:none">转人工队列</a>
          <button style="background:#344054" onclick="resetDemo()">重置演示数据</button>
          <button onclick="loadOrders()">刷新</button>
        </div>
      </div>
    </header>
    <main class="shell">
      <section class="stats" id="stats"></section>
      <section class="panel">
        <table>
          <thead>
            <tr>
              <th>订单</th>
              <th>客户</th>
              <th>订单状态</th>
              <th>物流</th>
              <th>客服处理</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="orders"></tbody>
        </table>
      </section>
    </main>
    <script>
      async function loadOrders() {
        const res = await fetch("/api/admin/orders");
        const payload = await res.json();
        renderStats(payload.data);
        renderOrders(payload.data);
      }

      function renderStats(orders) {
        const total = orders.length;
        const processed = orders.filter((item) => item.support_status === "processed").length;
        const pending = total - processed;
        const disputed = orders.filter((item) => item.shipment_status === "delivered_disputed" || item.shipment_status === "stalled").length;
        document.querySelector("#stats").innerHTML = [
          stat("总订单", total),
          stat("已处理", processed),
          stat("待处理", pending),
          stat("异常物流", disputed),
        ].join("");
      }

      function stat(label, value) {
        return `<div class="stat"><div class="label">${label}</div><div class="value">${value}</div></div>`;
      }

      function renderOrders(orders) {
        document.querySelector("#orders").innerHTML = orders.map((item) => {
          const processed = item.support_status === "processed";
          const risk = item.risk_level === "high" ? '<span class="badge risk">高风险</span>' : "";
          return `
            <tr>
              <td>
                <strong>${item.order_id}</strong>
                <div class="muted">¥${item.amount} · ${item.items.join("、") || "-"}</div>
              </td>
              <td>
                <strong>${item.customer_id || "-"}</strong>
                <div class="muted">${item.customer_name || "-"}</div>
                <div class="muted">${item.segment || "-"} ${risk}</div>
              </td>
              <td>
                <strong>${item.order_status}</strong>
                <div class="muted">${item.paid_at}</div>
              </td>
              <td>
                <strong>${item.shipment_status || "未发货"}</strong>
                <div class="muted">${item.carrier || "-"} ${item.tracking_no || ""}</div>
                <div class="muted">${item.latest_event || ""}</div>
              </td>
              <td>
                <span class="badge ${processed ? "processed" : "pending"}">${processed ? "已处理" : "待处理"}</span>
                <div class="muted">来源：${sourceName(item.support_source)}</div>
                <div class="muted">${item.resolution_note || "等待客服处理"}</div>
                <div class="muted">${item.processed_at || ""}</div>
                <div class="muted">${agentSummary(item)}</div>
              </td>
              <td>
                <button onclick="${processed ? `reopenOrder('${item.order_id}')` : `processOrder('${item.order_id}')`}">
                  ${processed ? "重新打开" : "标记已处理"}
                </button>
                <button class="ghost" ${item.latest_agent_reply_draft ? "" : "disabled"} onclick="showReply('${item.order_id}')">查看回复</button>
              </td>
            </tr>
          `;
        }).join("");
      }

      async function processOrder(orderId) {
        await fetch(`/api/admin/orders/${orderId}/process`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source: "manual" }),
        });
        await loadOrders();
      }

      async function reopenOrder(orderId) {
        await fetch(`/api/admin/orders/${orderId}/reopen`, { method: "POST" });
        await loadOrders();
      }

      async function resetDemo() {
        if (!confirm("确认重置演示数据？订单会恢复待处理，Agent 分析记录会清空。")) return;
        await fetch("/api/admin/reset-demo", { method: "POST" });
        await loadOrders();
      }

      function sourceName(source) {
        if (source === "agent") return "Agent 自动处理";
        if (source === "manual") return "人工处理";
        return "未处理";
      }

      function agentSourceName(source) {
        if (source === "structured_llm") return "结构化 JSON 输出";
        if (source === "llm") return "LLM 生成";
        if (source === "template") return "模板兜底";
        if (source === "human") return "人工复核";
        return source || "-";
      }

      function agentSummary(item) {
        if (!item.latest_agent_category) return "";
        const source = item.latest_agent_final_reply_source || item.latest_agent_reply_source;
        return `最近 Agent：${item.latest_agent_category} / ${item.latest_agent_priority} / ${agentSourceName(source)}`;
      }

      let cachedOrders = [];

      function renderOrders(orders) {
        cachedOrders = orders;
        document.querySelector("#orders").innerHTML = orders.map((item) => {
          const processed = item.support_status === "processed";
          const risk = item.risk_level === "high" ? '<span class="badge risk">高风险</span>' : "";
          return `
            <tr>
              <td>
                <strong>${item.order_id}</strong>
                <div class="muted">¥${item.amount} · ${item.items.join("、") || "-"}</div>
              </td>
              <td>
                <strong>${item.customer_id || "-"}</strong>
                <div class="muted">${item.customer_name || "-"}</div>
                <div class="muted">${item.segment || "-"} ${risk}</div>
              </td>
              <td>
                <strong>${item.order_status}</strong>
                <div class="muted">${item.paid_at}</div>
              </td>
              <td>
                <strong>${item.shipment_status || "未发货"}</strong>
                <div class="muted">${item.carrier || "-"} ${item.tracking_no || ""}</div>
                <div class="muted">${item.latest_event || ""}</div>
              </td>
              <td>
                <span class="badge ${processed ? "processed" : "pending"}">${processed ? "已处理" : "待处理"}</span>
                <div class="muted">来源：${sourceName(item.support_source)}</div>
                <div class="muted">${item.resolution_note || "等待客服处理"}</div>
                <div class="muted">${item.processed_at || ""}</div>
                <div class="muted">${agentSummary(item)}</div>
              </td>
              <td>
                <button onclick="${processed ? `reopenOrder('${item.order_id}')` : `processOrder('${item.order_id}')`}">
                  ${processed ? "重新打开" : "标记已处理"}
                </button>
                <button class="ghost" ${item.latest_agent_reply_draft ? "" : "disabled"} onclick="showReply('${item.order_id}')">查看回复</button>
              </td>
            </tr>
          `;
        }).join("");
      }

      function showReply(orderId) {
        const item = cachedOrders.find((order) => order.order_id === orderId);
        if (!item) return;
        document.querySelector("#replyTitle").textContent = `${item.order_id} · ${item.customer_id || "-"}`;
        document.querySelector("#replyMeta").textContent = `原始来源：${agentSourceName(item.latest_agent_reply_source)} · 最终来源：${agentSourceName(item.latest_agent_final_reply_source || item.latest_agent_reply_source)}`;
        document.querySelector("#replyDraft").textContent = item.latest_agent_reply_draft || "暂无 Agent 回复";
        document.querySelector("#finalReply").textContent = item.latest_agent_final_reply || "暂无人工最终回复";
        document.querySelector("#reviewMeta").textContent = item.latest_agent_reviewed_at
          ? `复核人：${item.latest_agent_reviewed_by || "-"} · ${item.latest_agent_reviewed_at}`
          : "尚未人工复核";
        document.querySelector("#replyModal").classList.add("open");
      }

      function closeReply() {
        document.querySelector("#replyModal").classList.remove("open");
      }

      loadOrders();
    </script>
    <div class="modal" id="replyModal">
      <div class="dialog">
        <h2 id="replyTitle">回复内容</h2>
        <div class="muted" id="replyMeta"></div>
        <h3>Agent 原始回复</h3>
        <div class="reply-box" id="replyDraft"></div>
        <h3>人工最终回复</h3>
        <div class="reply-box" id="finalReply"></div>
        <div class="muted" id="reviewMeta"></div>
        <div style="margin-top:14px">
          <button onclick="closeReply()">关闭</button>
        </div>
      </div>
    </div>
  </body>
</html>
"""


ESCALATION_HTML = """
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>转人工队列</title>
    <style>
      * { box-sizing: border-box; }
      body {
        margin: 0;
        background: #f5f7fa;
        color: #172033;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
        letter-spacing: 0;
      }
      header {
        background: #fff;
        border-bottom: 1px solid #dce3ea;
      }
      .shell {
        width: min(1180px, calc(100vw - 32px));
        margin: 0 auto;
      }
      .topbar {
        min-height: 72px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
      }
      h1 { margin: 0; font-size: 24px; }
      .sub { margin-top: 4px; color: #667085; font-size: 13px; }
      main { padding: 24px 0 40px; }
      .panel {
        background: #fff;
        border: 1px solid #dce3ea;
        border-radius: 8px;
        overflow: hidden;
      }
      table {
        width: 100%;
        border-collapse: collapse;
      }
      th, td {
        padding: 13px 14px;
        text-align: left;
        border-bottom: 1px solid #edf1f5;
        vertical-align: top;
        font-size: 14px;
      }
      th {
        background: #f9fafb;
        color: #526071;
        font-size: 12px;
        font-weight: 700;
      }
      .muted { color: #667085; line-height: 1.5; }
      .badge {
        display: inline-flex;
        min-height: 26px;
        align-items: center;
        border-radius: 999px;
        padding: 4px 10px;
        font-size: 12px;
        font-weight: 700;
      }
      .danger { background: #fef2f2; color: #b91c1c; }
      .warn { background: #fff7ed; color: #b45309; }
      button {
        border: 0;
        border-radius: 6px;
        background: #0f766e;
        color: #fff;
        padding: 9px 12px;
        font-weight: 700;
        cursor: pointer;
      }
      a { color: #0f766e; font-weight: 700; text-decoration: none; }
      @media (max-width: 900px) {
        table { min-width: 980px; }
        .panel { overflow-x: auto; }
      }
    </style>
  </head>
  <body>
    <header>
      <div class="shell topbar">
        <div>
          <h1>转人工队列</h1>
          <div class="sub">展示 Agent 判定需要人工复核的工单、原因和回复草稿</div>
        </div>
        <div style="display:flex; gap:10px; align-items:center">
          <a href="/admin">订单后台</a>
          <button onclick="loadEscalations()">刷新</button>
        </div>
      </div>
    </header>
    <main class="shell">
      <section class="panel">
        <table>
          <thead>
            <tr>
              <th>工单</th>
              <th>客户/订单</th>
              <th>分类</th>
              <th>转人工原因</th>
              <th>回复草稿</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody id="queue"></tbody>
        </table>
      </section>
    </main>
    <script>
      async function loadEscalations() {
        const res = await fetch("/api/admin/escalations");
        const payload = await res.json();
        renderQueue(payload.data);
      }

      function agentSourceName(source) {
        if (source === "structured_llm") return "结构化 JSON 输出";
        if (source === "llm") return "LLM 生成";
        if (source === "template") return "模板兜底";
        if (source === "human") return "人工复核";
        return source || "-";
      }

      function renderQueue(items) {
        document.querySelector("#queue").innerHTML = items.map((item) => `
          <tr>
            <td>
              <strong>${item.ticket_id}</strong>
              <div class="muted">${item.created_at}</div>
            </td>
            <td>
              <strong>${item.customer_id || "-"}</strong>
              <div class="muted">${item.customer_name || "-"}</div>
              <div class="muted">${item.order_id || "-"}</div>
            </td>
            <td>
              <span class="badge ${item.priority === "urgent" ? "danger" : "warn"}">${item.category} / ${item.priority}</span>
              <div class="muted">置信度：${Math.round(item.confidence * 100)}%</div>
            </td>
            <td>${item.escalation_reason || "-"}</td>
            <td>
              <div class="muted">${truncate(item.display_reply, 180)}</div>
              <div class="muted">来源：${agentSourceName(item.display_reply_source)}</div>
              <div class="muted">${item.reviewed_at ? `人工复核：${item.reviewed_by || "-"} · ${item.reviewed_at}` : ""}</div>
            </td>
            <td>
              <strong>${item.support_status || "pending"}</strong>
              <div class="muted">${item.business_sync_message || ""}</div>
            </td>
          </tr>
        `).join("") || '<tr><td colspan="6" class="muted">暂无转人工工单</td></tr>';
      }

      function truncate(text, max) {
        if (!text) return "";
        return text.length > max ? `${text.slice(0, max)}...` : text;
      }

      loadEscalations();
    </script>
  </body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.business_api:app", host="127.0.0.1", port=8011, reload=True)
