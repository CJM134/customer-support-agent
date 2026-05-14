from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents.ticket_agent import agent
from app.core.config import get_settings
from app.models.schemas import (
    DashboardResult,
    EvaluationResult,
    ExternalIntegrationStatus,
    ExternalContext,
    FeedbackMetrics,
    FeedbackRecord,
    FeedbackRequest,
    LLMConfigStatus,
    TicketAnalyzeRequest,
    TicketAnalysis,
)
from app.services.external_systems import get_external_gateway
from app.services.feedback_repository import feedback_repository
from app.services.metrics import evaluate_analyses

router = APIRouter()
RECENT_ANALYSES: list[TicketAnalysis] = []
MAX_RECENT = 20
logger = logging.getLogger(__name__)


@router.post("/tickets/analyze", response_model=TicketAnalysis)
async def analyze_ticket(request: TicketAnalyzeRequest) -> TicketAnalysis:
    logger.info(
        "分析工单: customer=%s order=%s message=%.60s",
        request.customer_id, request.order_id, request.message,
    )
    analysis = await asyncio.to_thread(agent.analyze, request, True, True)
    RECENT_ANALYSES.insert(0, analysis)
    del RECENT_ANALYSES[MAX_RECENT:]
    logger.info(
        "工单分析完成: ticket=%s category=%s escalate=%s source=%s",
        analysis.ticket_id, analysis.classification.category,
        analysis.should_escalate, analysis.reply_source,
    )
    return analysis


@router.post("/tickets/analyze/stream")
async def stream_analyze_ticket(request: TicketAnalyzeRequest) -> StreamingResponse:
    logger.info(
        "流式分析工单: customer=%s order=%s message=%.60s",
        request.customer_id, request.order_id, request.message,
    )

    def generate():
        try:
            for event in agent.stream_analyze(request, allow_side_effects=True):
                if event.get("event") == "completed":
                    analysis = event["analysis"]
                    RECENT_ANALYSES.insert(0, analysis)
                    del RECENT_ANALYSES[MAX_RECENT:]
                yield format_sse(event)
        except Exception as exc:
            logger.exception("流式分析异常: customer=%s order=%s", request.customer_id, request.order_id)
            yield format_sse({"event": "error", "message": str(exc)})

    return StreamingResponse(generate(), media_type="text/event-stream; charset=utf-8")


@router.post("/evaluate", response_model=EvaluationResult)
async def evaluate_sample_tickets() -> EvaluationResult:
    logger.info("开始批量评估样本工单")
    tickets = load_sample_tickets(get_settings().sample_tickets_path)
    analyses: list[TicketAnalysis] = []
    expected_categories: list[str] = []

    for item in tickets:
        analyses.append(
            agent.analyze(
                TicketAnalyzeRequest(
                    customer_id=item.get("customer_id"),
                    order_id=item.get("order_id"),
                    message=item["message"],
                ),
                False,
                False,
            )
        )
        expected_categories.append(item["expected_category"])

    result = evaluate_analyses(analyses, expected_categories)
    logger.info(
        "批量评估完成: accuracy=%.1f%% auto_handle=%.1f%% saved=%.1fmin total=%d",
        result.classification_accuracy * 100,
        result.auto_handle_rate * 100,
        result.total_minutes_saved,
        result.total_tickets,
    )
    return result


@router.get("/dashboard", response_model=DashboardResult)
async def get_dashboard() -> DashboardResult:
    logger.info("构建看板数据")
    tickets = load_sample_tickets(get_settings().sample_tickets_path)
    analyses = [
        agent.analyze(
            TicketAnalyzeRequest(
                customer_id=item.get("customer_id"),
                order_id=item.get("order_id"),
                message=item["message"],
            ),
            False,
            False,
        )
        for item in tickets
    ]
    summary = evaluate_analyses(analyses, [item["expected_category"] for item in tickets])
    recent = RECENT_ANALYSES[:5] or analyses[:5]
    logger.debug("看板返回 %d 条最近分析", len(recent))
    return DashboardResult(summary=summary, recent_analyses=recent)


@router.get("/integrations/context", response_model=ExternalContext)
async def get_integration_context(customer_id: str | None = None, order_id: str | None = None) -> ExternalContext:
    logger.info("查询集成上下文: customer=%s order=%s", customer_id, order_id)
    return get_external_gateway().get_context(customer_id, order_id)


@router.post("/feedback/revisions", response_model=FeedbackRecord)
async def save_feedback(request: FeedbackRequest) -> FeedbackRecord:
    logger.info("保存反馈: ticket=%s category=%s accepted=%s", request.ticket_id, request.category, request.accepted)
    return feedback_repository.save(request)


@router.get("/feedback/revisions", response_model=list[FeedbackRecord])
async def list_feedback(limit: int = 20) -> list[FeedbackRecord]:
    logger.debug("查询反馈修订记录 (limit=%d)", limit)
    return feedback_repository.list_recent(limit=limit)


@router.get("/feedback/metrics", response_model=FeedbackMetrics)
async def get_feedback_metrics() -> FeedbackMetrics:
    logger.debug("查询反馈指标")
    return feedback_repository.metrics()


@router.get("/config/llm", response_model=LLMConfigStatus)
async def get_llm_config() -> LLMConfigStatus:
    settings = get_settings()
    status = LLMConfigStatus(
        enabled=settings.llm_enabled,
        ready=settings.llm_ready,
        model=settings.llm_model,
        base_url_set=bool(settings.llm_base_url),
        api_key_set=bool(settings.llm_api_key),
    )
    logger.debug("LLM 配置: enabled=%s ready=%s model=%s", status.enabled, status.ready, status.model)
    return status


@router.get("/config/integrations", response_model=ExternalIntegrationStatus)
async def get_external_integration_config() -> ExternalIntegrationStatus:
    settings = get_settings()
    status = ExternalIntegrationStatus(
        crm_ready=settings.crm_ready,
        oms_ready=settings.oms_ready,
        logistics_ready=settings.logistics_ready,
        crm_path_template=settings.crm_customer_path_template,
        oms_path_template=settings.oms_order_path_template,
        logistics_path_template=settings.logistics_shipment_path_template,
    )
    logger.debug(
        "集成配置: crm=%s oms=%s logistics=%s",
        status.crm_ready, status.oms_ready, status.logistics_ready,
    )
    return status


def load_sample_tickets(path: Path) -> list[dict]:
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"Sample data not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def format_sse(payload: dict) -> str:
    return f"data: {json.dumps(to_jsonable(payload), ensure_ascii=False)}\n\n"


def to_jsonable(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value
