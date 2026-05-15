from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


Category = Literal[
    "refund",
    "logistics",
    "account",
    "product",
    "complaint",
    "invoice",
    "other",
]

Priority = Literal["low", "normal", "high", "urgent"]
ReplySource = Literal["llm", "template", "structured_llm", "llm_rewritten"]


class TicketAnalyzeRequest(BaseModel):
    customer_id: str | None = Field(default=None, description="Optional customer identifier.")
    message: str = Field(min_length=1, description="Raw customer ticket text.")
    order_id: str | None = None


class CustomerContext(BaseModel):
    customer_id: str | None = None
    name: str | None = None
    segment: str | None = None
    lifetime_value: float | None = None
    risk_level: str | None = None


class OrderContext(BaseModel):
    order_id: str | None = None
    status: str | None = None
    amount: float | None = None
    paid_at: str | None = None
    items: list[str] = []
    support_status: str | None = None
    processed_at: str | None = None
    resolution_note: str | None = None


class ShipmentContext(BaseModel):
    order_id: str | None = None
    carrier: str | None = None
    tracking_no: str | None = None
    status: str | None = None
    latest_event: str | None = None
    last_updated_at: str | None = None


class ExternalContext(BaseModel):
    customer: CustomerContext | None = None
    order: OrderContext | None = None
    shipment: ShipmentContext | None = None


class BusinessSyncResult(BaseModel):
    attempted: bool = False
    success: bool = False
    order_id: str | None = None
    message: str


class KnowledgeHit(BaseModel):
    id: str
    title: str
    category: Category
    score: float
    answer: str
    retrieval_method: str = "rag_vector"


class ClassificationResult(BaseModel):
    category: Category
    priority: Priority
    confidence: float = Field(ge=0, le=1)
    matched_keywords: list[str]
    risk_flags: list[str]


class StructuredDecision(BaseModel):
    category: Category
    priority: Priority
    reply: str = Field(min_length=1)
    should_escalate: bool
    confidence: float = Field(default=0.7, ge=0, le=1)
    risk_flags: list[str] = Field(default_factory=list)
    escalation_reason: str | None = None
    reasoning: str | None = None

    @field_validator("reply")
    @classmethod
    def reply_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reply must not be blank")
        return stripped


class TicketAnalysis(BaseModel):
    ticket_id: str
    customer_id: str | None
    order_id: str | None
    message: str
    external_context: ExternalContext | None = None
    classification: ClassificationResult
    knowledge_hits: list[KnowledgeHit]
    reply_draft: str
    reply_source: ReplySource
    structured_decision: StructuredDecision | None = None
    should_escalate: bool
    escalation_reason: str | None
    business_sync: BusinessSyncResult | None = None
    analysis_record_sync: BusinessSyncResult | None = None
    estimated_minutes_saved: float
    business_value: dict[str, float | int | str]


class EvaluationResult(BaseModel):
    total_tickets: int
    classification_accuracy: float
    auto_handle_rate: float
    escalation_rate: float
    total_minutes_saved: float
    avg_minutes_saved_per_ticket: float
    high_risk_tickets: int
    category_distribution: dict[str, int]


class DashboardResult(BaseModel):
    summary: EvaluationResult
    recent_analyses: list[TicketAnalysis]


class FeedbackRequest(BaseModel):
    ticket_id: str
    original_reply: str
    revised_reply: str
    category: Category
    accepted: bool = True
    editor: str | None = None
    notes: str | None = None


class FeedbackRecord(FeedbackRequest):
    id: int
    created_at: str


class FeedbackMetrics(BaseModel):
    total_feedback: int
    acceptance_rate: float
    avg_revision_ratio: float
    by_category: dict[str, int]


class LLMConfigStatus(BaseModel):
    enabled: bool
    ready: bool
    model: str
    base_url_set: bool
    api_key_set: bool


class ExternalIntegrationStatus(BaseModel):
    crm_ready: bool
    oms_ready: bool
    logistics_ready: bool
    crm_path_template: str
    oms_path_template: str
    logistics_path_template: str
