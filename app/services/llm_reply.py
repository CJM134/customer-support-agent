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
            logger.debug("LLM 未配置，使用模板兜底")
            return ReplyDraft(fallback, "template")

        client = self._build_client(settings)
        try:
            content = self._llm_complete(client, settings, message, classification, hits, context, fallback)
            logger.info("LLM 回复生成成功: length=%d model=%s", len(content), settings.llm_model)
        except Exception:
            logger.exception("LLM reply generation failed; falling back to template reply")
            return ReplyDraft(fallback, "template")

        ##评估重试：评分低则重写一次
        eval_content = self._evaluate_reply(client, settings, message, classification, context, content)
        if eval_content is None:
            return ReplyDraft(content.strip(), "llm")

        score, feedback = eval_content
        logger.info("回复评估得分: %d/10", score)
        if score >= 6:
            return ReplyDraft(content.strip(), "llm")

        logger.info("回复评分 %d < 6，启动一次重写", score)
        try:
            rewritten = self._rewrite_with_evaluation(
                client, settings, message, classification, hits, context, fallback,
                content, feedback,
            )
            logger.info("重写完成: length=%d", len(rewritten))
            return ReplyDraft(rewritten.strip(), "llm_rewritten")
        except Exception:
            logger.exception("重写失败，使用原始 LLM 回复")
            return ReplyDraft(content.strip(), "llm")

    def stream_draft(
        self,
        message: str,
        classification: ClassificationResult,
        hits: list[KnowledgeHit],
        context: ExternalContext | None,
    ) -> Iterator[ReplyStreamEvent]:
        ##内部走 draft（含评估重试），结果流式输出
        result = self.draft(message, classification, hits, context)
        yield ReplyStreamEvent(type="source", source=result.source)
        yield from stream_text_chunks(result.content)

    def _build_client(self, settings):
        from openai import OpenAI

        return OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )

    def _llm_complete(
        self,
        client,
        settings,
        message: str,
        classification: ClassificationResult,
        hits: list[KnowledgeHit],
        context: ExternalContext | None,
        fallback: str,
    ) -> str:
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
            raise ValueError("empty_llm_response")
        return content.strip()

    def _evaluate_reply(
        self,
        client,
        settings,
        message: str,
        classification: ClassificationResult,
        context: ExternalContext | None,
        reply: str,
    ) -> tuple[int, str] | None:
        context_json = context.model_dump(mode="json") if context else {}
        prompt = (
            "你是一个客服回复质量评估器。评估以下回复并返回 JSON，"
            "不要输出任何额外文本。\n\n"
            "评估标准（各 1-10 分）：\n"
            "1. 相关性：是否针对客户问题\n"
            "2. 专业性：语气是否礼貌、负责\n"
            "3. 可执行性：是否给出明确的处理方案或下一步\n"
            "4. 准确性：是否有编造信息或错误承诺\n"
            "5. 简洁性：是否简洁易懂\n\n"
            "请综合给出总分（1-10），并列出最多 3 条改进建议。\n\n"
            "JSON 格式：\n"
            '{"score": <int>, "feedback": "<改进建议>"}\n\n'
            f"客户问题：{message}\n"
            f"分类：{classification.category}\n"
            f"客户上下文：{json.dumps(context_json, ensure_ascii=False)[:300]}\n"
            f"回复：{reply}"
        )
        try:
            response = client.chat.completions.create(
                model=settings.llm_model,
                temperature=0,
                max_tokens=200,
                messages=[
                    {"role": "system", "content": "你是一个回复质量评估器。只输出 JSON，不要输出解释。"},
                    {"role": "user", "content": prompt},
                ],
            )
            raw = response.choices[0].message.content
            if not raw:
                return None
            import json as json_mod

            result = json_mod.loads(raw.strip())
            score = int(result.get("score", 10))
            feedback = result.get("feedback", "")
            return score, feedback
        except Exception:
            logger.debug("回复评估失败，跳过重试", exc_info=True)
            return None

    def _rewrite_with_evaluation(
        self,
        client,
        settings,
        message: str,
        classification: ClassificationResult,
        hits: list[KnowledgeHit],
        context: ExternalContext | None,
        fallback: str,
        original_reply: str,
        feedback: str,
    ) -> str:
        context_json = context.model_dump(mode="json") if context else {}
        hit_json = [hit.model_dump(mode="json") for hit in hits]
        prompt = (
            "你是电商客服回复助手。下面是上一次回复的评估反馈，请根据反馈改进回复。\n"
            "只输出可发给客户的中文回复，语气自然、负责，不编造赔付，不超过180字。\n\n"
            f"客户问题：{message}\n"
            f"分类：{classification.category}，优先级：{classification.priority}\n"
            f"系统上下文：{json.dumps(context_json, ensure_ascii=False)[:500]}\n"
            f"处理依据：{json.dumps(hit_json, ensure_ascii=False)[:600]}\n"
            f"参考稿：{fallback[:300]}\n\n"
            f"上一次回复：{original_reply}\n"
            f"改进反馈：{feedback}\n\n"
            "请根据以上反馈改进回复。"
        )
        response = client.chat.completions.create(
            model=settings.llm_model,
            temperature=0.3,
            max_tokens=settings.llm_max_tokens,
            messages=[
                {
                    "role": "system",
                    "content": "你是电商客服回复助手。只输出可发给客户的中文回复。",
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content
        return content.strip() if content else original_reply

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
