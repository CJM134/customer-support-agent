from __future__ import annotations

import logging
from collections import Counter

from app.core.config import get_settings
from app.models.schemas import EvaluationResult, TicketAnalysis

logger = logging.getLogger(__name__)


def estimate_minutes_saved(analysis: TicketAnalysis) -> float:
    settings = get_settings()
    assisted_minutes = (
        settings.agent_review_minutes_per_ticket
        if analysis.should_escalate
        else settings.auto_close_minutes_per_ticket
    )
    return round(max(settings.baseline_minutes_per_ticket - assisted_minutes, 0), 2)


def build_business_value(analysis: TicketAnalysis) -> dict[str, float | int | str]:
    settings = get_settings()
    saved = estimate_minutes_saved(analysis)
    handling_mode = "human_review" if analysis.should_escalate else "auto_ready"
    return {
        "baseline_minutes": settings.baseline_minutes_per_ticket,
        "agent_minutes": settings.agent_review_minutes_per_ticket
        if analysis.should_escalate
        else settings.auto_close_minutes_per_ticket,
        "minutes_saved": saved,
        "handling_mode": handling_mode,
        "confidence_percent": round(analysis.classification.confidence * 100, 1),
    }


def evaluate_analyses(analyses: list[TicketAnalysis], expected_categories: list[str] | None = None) -> EvaluationResult:
    total = len(analyses)
    logger.info("评估 %d 条分析结果", total)
    if total == 0:
        return EvaluationResult(
            total_tickets=0,
            classification_accuracy=0,
            auto_handle_rate=0,
            escalation_rate=0,
            total_minutes_saved=0,
            avg_minutes_saved_per_ticket=0,
            high_risk_tickets=0,
            category_distribution={},
        )

    if expected_categories:
        correct = sum(
            analysis.classification.category == expected
            for analysis, expected in zip(analyses, expected_categories, strict=False)
        )
        accuracy = correct / total
    else:
        accuracy = 0

    escalations = sum(analysis.should_escalate for analysis in analyses)
    high_risk = sum(bool(analysis.classification.risk_flags) for analysis in analyses)
    total_saved = round(sum(item.estimated_minutes_saved for item in analyses), 2)
    distribution = Counter(item.classification.category for item in analyses)

    return EvaluationResult(
        total_tickets=total,
        classification_accuracy=round(accuracy, 3),
        auto_handle_rate=round((total - escalations) / total, 3),
        escalation_rate=round(escalations / total, 3),
        total_minutes_saved=total_saved,
        avg_minutes_saved_per_ticket=round(total_saved / total, 2),
        high_risk_tickets=high_risk,
        category_distribution=dict(distribution),
    )
