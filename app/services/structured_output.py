from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.models.schemas import ClassificationResult, ExternalContext, KnowledgeHit, StructuredDecision
from app.services.reply_generator import generate_reply

logger = logging.getLogger(__name__)


@dataclass
class StructuredDecisionResult:
    decision: StructuredDecision | None
    error: str | None = None


class StructuredOutputGenerator:
    def decide(
        self,
        message: str,
        classification: ClassificationResult,
        hits: list[KnowledgeHit],
        context: ExternalContext | None,
    ) -> StructuredDecisionResult:
        settings = get_settings()
        if not settings.llm_enabled or not settings.llm_api_key:
            logger.info("结构化输出: LLM 未配置，跳过")
            return StructuredDecisionResult(decision=None, error="llm_not_ready")

        fallback = generate_reply(message, classification, hits)

        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )
            content = self._complete(
                client=client,
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                prompt=self._build_prompt(message, classification, hits, context, fallback),
            )
            try:
                return StructuredDecisionResult(decision=parse_structured_decision(content))
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                logger.debug("结构化输出: 需要修复重试: %s", exc)
                repaired = self._complete(
                    client=client,
                    model=settings.llm_model,
                    max_tokens=settings.llm_max_tokens,
                    prompt=self._build_repair_prompt(content, str(exc)),
                    temperature=0,
                )
                return StructuredDecisionResult(decision=parse_structured_decision(repaired))
        except Exception as exc:
            logger.exception("Structured LLM output failed; falling back to normal reply flow")
            return StructuredDecisionResult(decision=None, error=str(exc))

    def _complete(
        self,
        client: Any,
        model: str,
        max_tokens: int,
        prompt: str,
        temperature: float = 0.1,
    ) -> str:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是电商客服工单 Agent。必须只返回一个合法 JSON 对象，"
                        "不要输出 Markdown、解释或额外文本。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("empty_llm_response")
        return content.strip()

    def _build_prompt(
        self,
        message: str,
        classification: ClassificationResult,
        hits: list[KnowledgeHit],
        context: ExternalContext | None,
        fallback: str,
    ) -> str:
        context_json = context.model_dump(mode="json") if context else {}
        hit_json = [hit.model_dump(mode="json") for hit in hits]
        schema = {
            "type": "object",
            "required": ["category", "priority", "reply", "should_escalate"],
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["refund", "logistics", "account", "product", "complaint", "invoice", "other"],
                },
                "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
                "reply": {"type": "string", "minLength": 1},
                "should_escalate": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "risk_flags": {"type": "array", "items": {"type": "string"}},
                "escalation_reason": {"type": ["string", "null"]},
                "reasoning": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        }
        return "\n".join(
            [
                "请基于客户问题、CRM/订单/物流上下文、RAG 知识命中，输出结构化决策 JSON。",
                "JSON Schema:",
                json.dumps(schema, ensure_ascii=False),
                "规则:",
                "1. reply 必须是可直接发给客户的中文客服回复，语气自然，避免承诺不存在的赔付。",
                "2. should_escalate=true 只用于高风险、投诉、VIP/企业客户、知识缺失或需要人工核验的场景。",
                "3. 如果上下文与客户描述矛盾，需要在 reply 中说明会核查，不要编造物流结论。",
                "4. category 和 priority 可修正规则分类结果，但必须使用 schema 中的枚举值。",
                f"客户问题: {message}",
                f"规则分类: {classification.model_dump_json()}",
                f"业务上下文: {json.dumps(context_json, ensure_ascii=False)[:1000]}",
                f"RAG 知识命中: {json.dumps(hit_json, ensure_ascii=False)[:1200]}",
                f"模板兜底回复: {fallback[:400]}",
            ]
        )

    def _build_repair_prompt(self, raw_output: str, error: str) -> str:
        return "\n".join(
            [
                "下面的模型输出没有通过 JSON/Pydantic 校验。",
                "请修复为一个合法 JSON 对象，只输出 JSON，不要输出解释。",
                "必须包含字段: category, priority, reply, should_escalate。",
                "可选字段: confidence, risk_flags, escalation_reason, reasoning。",
                f"校验错误: {error[:600]}",
                f"原始输出: {raw_output[:1600]}",
            ]
        )


def parse_structured_decision(content: str) -> StructuredDecision:
    raw = json.loads(extract_json_object(content))
    return StructuredDecision.model_validate(raw)


def extract_json_object(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("json_object_not_found")
    return text[start : end + 1]


structured_output_generator = StructuredOutputGenerator()
