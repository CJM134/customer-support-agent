from __future__ import annotations

from typing import Any, Iterator, TypedDict
from uuid import uuid4

from langgraph.graph import END, StateGraph

from app.models.schemas import (
    ClassificationResult,
    ExternalContext,
    KnowledgeHit,
    StructuredDecision,
    TicketAnalyzeRequest,
    TicketAnalysis,
    BusinessSyncResult,
)
from app.services.external_systems import get_external_gateway
from app.services.knowledge_base import get_knowledge_base
from app.services.llm_reply import reply_generator, stream_text_chunks
from app.services.metrics import build_business_value, estimate_minutes_saved
from app.services.reply_generator import generate_reply
from app.services.structured_output import structured_output_generator
from app.services.ticket_classifier import classify_ticket


class TicketState(TypedDict, total=False):
    request: TicketAnalyzeRequest
    external_context: ExternalContext
    classification: ClassificationResult
    knowledge_hits: list[KnowledgeHit]
    reply_draft: str
    reply_source: str
    structured_decision: StructuredDecision
    should_escalate: bool
    escalation_reason: str | None
    business_sync: BusinessSyncResult
    analysis: TicketAnalysis
    use_llm: bool
    allow_side_effects: bool
    already_processed: bool


class CustomerSupportAgent:
    """LangGraph-powered ticket agent.

    The graph is intentionally explicit:
    classify -> retrieve -> draft -> evaluate.
    """

    def __init__(self) -> None:
        self.graph = self._build_graph()

    def analyze(
        self,
        request: TicketAnalyzeRequest,
        use_llm: bool = True,
        allow_side_effects: bool = True,
    ) -> TicketAnalysis:
        final_state = self.graph.invoke(
            {
                "request": request,
                "use_llm": use_llm,
                "allow_side_effects": allow_side_effects,
            }
        )
        return final_state["analysis"]

    def stream_analyze(
        self,
        request: TicketAnalyzeRequest,
        allow_side_effects: bool = True,
    ) -> Iterator[dict[str, Any]]:
        state: TicketState = {
            "request": request,
            "use_llm": True,
            "allow_side_effects": allow_side_effects,
        }

        state.update(self._classify(state))
        yield {
            "event": "classified",
            "classification": state["classification"],
            "external_context": state.get("external_context"),
            "already_processed": state.get("already_processed", False),
        }

        state.update(self._retrieve(state))
        yield {
            "event": "retrieved",
            "knowledge_hits": state["knowledge_hits"],
        }

        if state.get("already_processed"):
            state.update(self._draft(state))
            yield {
                "event": "reply_source",
                "reply_source": state["reply_source"],
            }
            yield {
                "event": "reply_delta",
                "delta": state["reply_draft"],
            }
        else:
            structured_state = self._draft_with_structured_output(state)
            if structured_state:
                state.update(structured_state)
                yield {
                    "event": "structured_decision",
                    "structured_decision": state["structured_decision"],
                }
                yield {
                    "event": "reply_source",
                    "reply_source": state["reply_source"],
                }
                for reply_event in stream_text_chunks(state["reply_draft"]):
                    yield {
                        "event": "reply_delta",
                        "delta": reply_event.content,
                    }
            else:
                reply_parts: list[str] = []
                reply_source = "template"
                for reply_event in reply_generator.stream_draft(
                    message=request.message,
                    classification=state["classification"],
                    hits=state["knowledge_hits"],
                    context=state.get("external_context"),
                ):
                    if reply_event.type == "source":
                        reply_source = reply_event.source or "template"
                        yield {
                            "event": "reply_source",
                            "reply_source": reply_source,
                        }
                    elif reply_event.type == "delta":
                        reply_parts.append(reply_event.content)
                        yield {
                            "event": "reply_delta",
                            "delta": reply_event.content,
                        }

                state["reply_draft"] = "".join(reply_parts)
                state["reply_source"] = reply_source

        state.update(self._evaluate(state))
        yield {
            "event": "completed",
            "analysis": state["analysis"],
        }

    def _build_graph(self):
        graph = StateGraph(TicketState)
        graph.add_node("classify", self._classify)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("draft", self._draft)
        graph.add_node("evaluate", self._evaluate)

        graph.set_entry_point("classify")
        graph.add_edge("classify", "retrieve")
        graph.add_edge("retrieve", "draft")
        graph.add_edge("draft", "evaluate")
        graph.add_edge("evaluate", END)
        return graph.compile()

    def _classify(self, state: TicketState) -> dict[str, Any]:
        request = state["request"]
        context = get_external_gateway().get_context(request.customer_id, request.order_id)
        classification = classify_ticket(request.message)
        classification = self._enrich_classification_with_context(classification, context)
        return {
            "external_context": context,
            "classification": classification,
            "already_processed": bool(context.order and context.order.support_status == "processed"),
        }

    def _retrieve(self, state: TicketState) -> dict[str, Any]:
        if state.get("already_processed"):
            return {"knowledge_hits": []}

        request = state["request"]
        classification = state["classification"]
        hits = get_knowledge_base().search(request.message, classification.category)
        return {"knowledge_hits": hits}

    def _draft(self, state: TicketState) -> dict[str, Any]:
        request = state["request"]
        if state.get("already_processed"):
            context = state.get("external_context")
            note = context.order.resolution_note if context and context.order else None
            processed_at = context.order.processed_at if context and context.order else None
            content = (
                "该订单已在业务后台标记为已处理，无需重复生成客服回复。\n"
                f"处理说明：{note or '已完成客服处理。'}\n"
                f"处理时间：{processed_at or '-'}"
            )
            return {
                "reply_draft": content,
                "reply_source": "template",
            }

        if state.get("use_llm", True):
            structured_state = self._draft_with_structured_output(state)
            if structured_state:
                return structured_state

            draft = reply_generator.draft(
                message=request.message,
                classification=state["classification"],
                hits=state["knowledge_hits"],
                context=state.get("external_context"),
            )
            content = draft.content
            source = draft.source
        else:
            content = generate_reply(request.message, state["classification"], state["knowledge_hits"])
            source = "template"
        return {
            "reply_draft": content,
            "reply_source": source,
        }

    def _draft_with_structured_output(self, state: TicketState) -> dict[str, Any] | None:
        if not state.get("use_llm", True):
            return None

        request = state["request"]
        result = structured_output_generator.decide(
            message=request.message,
            classification=state["classification"],
            hits=state["knowledge_hits"],
            context=state.get("external_context"),
        )
        if not result.decision:
            return None

        decision = result.decision
        return {
            "classification": self._merge_structured_classification(state["classification"], decision),
            "reply_draft": decision.reply.strip(),
            "reply_source": "structured_llm",
            "structured_decision": decision,
        }

    def _evaluate(self, state: TicketState) -> dict[str, Any]:
        request = state["request"]
        classification = state["classification"]
        hits = state["knowledge_hits"]
        context = state.get("external_context")
        should_escalate, reason = self._should_escalate(classification, bool(hits), context)
        structured_decision = state.get("structured_decision")
        if structured_decision and structured_decision.should_escalate:
            should_escalate = True
            reason = (
                reason
                or structured_decision.escalation_reason
                or structured_decision.reasoning
                or "LLM structured decision requires human review."
            )
        if state.get("already_processed"):
            should_escalate = False
            reason = None
        business_sync = self._sync_business_status(
            order_id=request.order_id,
            should_escalate=should_escalate,
            reply_source=state["reply_source"],
            already_processed=state.get("already_processed", False),
            allow_side_effects=state.get("allow_side_effects", True),
        )

        analysis = TicketAnalysis(
            ticket_id=f"T-{uuid4().hex[:8].upper()}",
            customer_id=request.customer_id,
            order_id=request.order_id,
            message=request.message,
            external_context=context,
            classification=classification,
            knowledge_hits=hits,
            reply_draft=state["reply_draft"],
            reply_source=state["reply_source"],  # type: ignore[arg-type]
            structured_decision=structured_decision,
            should_escalate=should_escalate,
            escalation_reason=reason,
            business_sync=business_sync,
            estimated_minutes_saved=0,
            business_value={},
        )
        analysis.estimated_minutes_saved = estimate_minutes_saved(analysis)
        analysis.business_value = build_business_value(analysis)
        analysis.analysis_record_sync = self._save_analysis_record(
            analysis,
            allow_side_effects=state.get("allow_side_effects", True),
        )
        return {"analysis": analysis}

    def _sync_business_status(
        self,
        order_id: str | None,
        should_escalate: bool,
        reply_source: str,
        already_processed: bool,
        allow_side_effects: bool,
    ) -> BusinessSyncResult:
        if not allow_side_effects:
            return BusinessSyncResult(
                attempted=False,
                success=False,
                order_id=order_id,
                message="当前为评估/看板调用，未回写业务后台。",
            )
        if already_processed:
            return BusinessSyncResult(
                attempted=False,
                success=True,
                order_id=order_id,
                message="订单已处理，已跳过重复分析和重复回写。",
            )
        if should_escalate:
            return BusinessSyncResult(
                attempted=False,
                success=False,
                order_id=order_id,
                message="该工单需要人工复核，未自动更新业务后台。",
            )

        note = f"Agent 已生成{reply_source.upper()}回复并判定可自动处理，订单状态已同步。"
        return get_external_gateway().mark_order_processed(order_id, note)

    def _save_analysis_record(
        self,
        analysis: TicketAnalysis,
        allow_side_effects: bool,
    ) -> BusinessSyncResult:
        if not allow_side_effects:
            return BusinessSyncResult(
                attempted=False,
                success=False,
                order_id=analysis.order_id,
                message="当前为评估/看板调用，未保存 Agent 分析记录。",
            )
        return get_external_gateway().save_agent_analysis(analysis)

    def _enrich_classification_with_context(
        self,
        classification: ClassificationResult,
        context: ExternalContext,
    ) -> ClassificationResult:
        flags = list(classification.risk_flags)
        priority = classification.priority

        if context.customer and context.customer.risk_level == "high":
            flags.append("crm_high_risk")
            priority = "high" if priority in {"low", "normal"} else priority

        if context.customer and context.customer.segment in {"vip", "enterprise"}:
            flags.append("strategic_customer")
            priority = "high" if priority in {"low", "normal"} else priority

        if context.shipment and context.shipment.status in {"stalled", "delivered_disputed"}:
            flags.append(f"logistics_{context.shipment.status}")
            if classification.category == "logistics":
                priority = "high" if priority == "normal" else priority

        return classification.model_copy(
            update={
                "priority": priority,
                "risk_flags": sorted(set(flags)),
            }
        )

    def _merge_structured_classification(
        self,
        classification: ClassificationResult,
        decision: StructuredDecision,
    ) -> ClassificationResult:
        flags = set(classification.risk_flags)
        flags.update(decision.risk_flags)
        flags.add("structured_output_validated")
        return classification.model_copy(
            update={
                "category": decision.category,
                "priority": decision.priority,
                "confidence": decision.confidence,
                "risk_flags": sorted(flags),
            }
        )

    def _should_escalate(
        self,
        classification: ClassificationResult,
        has_knowledge_hit: bool,
        context: ExternalContext | None,
    ) -> tuple[bool, str | None]:
        if classification.priority == "urgent":
            return True, "命中紧急语气、强负面情绪或高风险上下文，需要人工优先处理。"
        if classification.category == "complaint":
            return True, "投诉类问题建议专员复核，避免二次升级。"
        if context and context.customer and context.customer.segment in {"vip", "enterprise"}:
            return True, "VIP 或企业客户建议人工复核，保护关键客户体验。"
        if classification.confidence < 0.58:
            return True, "分类置信度偏低，需要人工确认。"
        if not has_knowledge_hit:
            return True, "未匹配到知识库规则，需要人工补充处理依据。"
        return False, None


agent = CustomerSupportAgent()
