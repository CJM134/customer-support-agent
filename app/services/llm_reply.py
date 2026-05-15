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

        ##规则评估：评分低则重写一次
        eval_result = self._evaluate_reply(message, classification, content)
        if eval_result is None:
            return ReplyDraft(content.strip(), "llm")

        score, feedback = eval_result
        logger.info("规则评估得分: %d/10 | %s", score, feedback)
        if score >= 6:
            return ReplyDraft(content.strip(), "llm")

        logger.info("规则评分 %d < 6，启动一次重写", score)
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
        message: str,
        classification: ClassificationResult,
        reply: str,
    ) -> tuple[int, str] | None:
        """Rule-based reply quality evaluation. Returns (score, feedback) where score < 6 triggers a rewrite."""
        score = 10
        feedback_parts: list[str] = []

        # 1. 长度检查
        if len(reply) < 10:
            return 3, "回复过短，缺少实质内容"
        if len(reply) > 400:
            score -= 2
            feedback_parts.append("回复过长，建议精简")

        # 2. 空话套话检测 — 有"请稍等"类空话但无实质处理方案
        placeholders = ["请稍等", "正在为您查询", "已记录", "耐心等待", "正在处理中"]
        solution_words = [
            "退款", "补发", "换货", "核实", "查询", "提交", "申请",
            "联系", "处理", "解决", "安排", "协调", "跟进", "反馈",
        ]
        has_placeholder = any(p in reply for p in placeholders)
        has_solution = any(s in reply for s in solution_words)
        if has_placeholder and not has_solution:
            score -= 4
            feedback_parts.append("回复含空话但缺少实质处理方案")

        # 3. 关键词覆盖 — 客户问题中的核心词在回复中出现比例
        key_terms = self._extract_key_terms(message)
        if key_terms:
            covered = sum(1 for t in key_terms if t in reply)
            ratio = covered / len(key_terms)
            if ratio < 0.25:
                score -= 4
                feedback_parts.append("回复未针对客户具体问题")
            elif ratio < 0.4:
                score -= 2
                feedback_parts.append("回复对客户问题的覆盖不够")

        # 4. 投诉类需包含致歉
        if classification.category == "complaint":
            if "抱歉" not in reply and "歉意" not in reply and "道歉" not in reply:
                score -= 2
                feedback_parts.append("投诉类回复建议包含致歉")

        # 5. 赔偿承诺检测 — 有赔偿承诺但知识库无支撑
        promise_words = ["赔偿", "赔付", "补偿"]
        if any(p in reply for p in promise_words):
            score -= 2
            feedback_parts.append("回复含赔偿承诺，请确认依据")

        # 6. 可执行性 — 是否包含下一步动作指引
        action_words = ["请您", "建议您", "您可以", "我们会", "我们将", "已为您", "正在为您", "请联系"]
        if not any(a in reply for a in action_words):
            score -= 2
            feedback_parts.append("未给出明确下一步动作")

        score = max(1, min(10, score))
        feedback = "；".join(feedback_parts) if feedback_parts else "质量合格"
        return score, feedback

    @staticmethod
    def _extract_key_terms(text: str) -> list[str]:
        """提取客户消息中的核心关键词（2-8字中文短语，排除常见停用词）。"""
        import re

        terms = re.findall(r"[一-鿿]{2,}", text)
        stop_words = {
            "什么", "怎么", "这个", "那个", "一个", "可以", "没有", "不是",
            "我们", "你们", "他们", "已经", "还是", "或者", "因为", "所以",
            "但是", "如果", "虽然", "而且", "然后", "之后", "之前",
            "就是", "非常", "比较", "很多", "一些", "这种", "这样",
            "那么", "一下", "一直", "大家", "自己",
        }
        return [t for t in terms if t not in stop_words and len(t) <= 10]

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
