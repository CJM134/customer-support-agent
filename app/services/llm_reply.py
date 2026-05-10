from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterator

from app.core.config import get_settings
from app.models.schemas import ClassificationResult, ExternalContext, KnowledgeHit
from app.services.reply_generator import generate_reply

logger = logging.getLogger(__name__)


class ReplyDraft:
    def __init__(self, content: str, source: str):
        self.content = content
        self.source = source


@dataclass
class ReplyStreamEvent:
    type: str
    content: str = ""
    source: str = ""


class LLMReplyGenerator:
    def draft(
        self,
        message: str,
        classification: ClassificationResult,
        hits: list[KnowledgeHit],
        context: ExternalContext | None,
    ) -> ReplyDraft:
        settings = get_settings()
        fallback = generate_reply(message, classification, hits)
        if not settings.llm_enabled or not settings.llm_api_key:
            return ReplyDraft(fallback, "template")

        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )
            response = client.chat.completions.create(
                model=settings.llm_model,
                temperature=0.2,
                max_tokens=settings.llm_max_tokens,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是电商客服回复助手。只输出可发给客户的中文回复。"
                            "语气自然、负责，不编造赔付，不超过180字。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._build_prompt(message, classification, hits, context, fallback),
                    },
                ],
            )
            content = response.choices[0].message.content
            if not content:
                return ReplyDraft(fallback, "template")
            return ReplyDraft(content.strip(), "llm")
        except Exception:
            logger.exception("LLM reply generation failed; falling back to template reply")
            return ReplyDraft(fallback, "template")

    def stream_draft(
        self,
        message: str,
        classification: ClassificationResult,
        hits: list[KnowledgeHit],
        context: ExternalContext | None,
    ) -> Iterator[ReplyStreamEvent]:
        settings = get_settings()
        fallback = generate_reply(message, classification, hits)
        if not settings.llm_enabled or not settings.llm_api_key:
            yield ReplyStreamEvent(type="source", source="template")
            yield from stream_text_chunks(fallback)
            return

        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )
            stream = client.chat.completions.create(
                model=settings.llm_model,
                temperature=0.2,
                max_tokens=settings.llm_max_tokens,
                stream=True,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是电商客服回复助手。只输出可发给客户的中文回复。"
                            "语气自然、负责，不编造赔付，不超过180字。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._build_prompt(message, classification, hits, context, fallback),
                    },
                ],
            )
            yield ReplyStreamEvent(type="source", source="llm")
            for chunk in stream:
                if not chunk.choices:
                    continue
                content = chunk.choices[0].delta.content
                if content:
                    yield ReplyStreamEvent(type="delta", content=content)
        except Exception:
            logger.exception("LLM streaming failed; falling back to template reply")
            yield ReplyStreamEvent(type="source", source="template")
            yield from stream_text_chunks(fallback)

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
        return "\n".join(
            [
                f"客户问题：{message}",
                f"分类：{classification.category}，优先级：{classification.priority}",
                f"系统上下文：{json.dumps(context_json, ensure_ascii=False)[:500]}",
                f"处理依据：{json.dumps(hit_json, ensure_ascii=False)[:600]}",
                f"参考稿：{fallback[:300]}",
                "请改写为更自然的客服回复，包含确认问题、处理方案和下一步动作。",
            ]
        )


reply_generator = LLMReplyGenerator()


def stream_text_chunks(text: str, size: int = 12) -> Iterator[ReplyStreamEvent]:
    for index in range(0, len(text), size):
        yield ReplyStreamEvent(type="delta", content=text[index : index + size])
