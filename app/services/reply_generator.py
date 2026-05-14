from __future__ import annotations

import logging

from app.models.schemas import ClassificationResult, KnowledgeHit

logger = logging.getLogger(__name__)


TONE_BY_PRIORITY = {
    "urgent": "非常抱歉给您带来不好的体验，我们已经将问题标记为紧急处理。",
    "high": "感谢您的反馈，我们会优先跟进这条问题。",
    "normal": "您好，感谢您的反馈。",
    "low": "您好，已收到您的问题。",
}


def generate_reply(message: str, classification: ClassificationResult, hits: list[KnowledgeHit]) -> str:
    logger.debug(
        "生成模板回复: category=%s priority=%s hits=%d",
        classification.category, classification.priority, len(hits),
    )
    opening = TONE_BY_PRIORITY[classification.priority]
    if hits:
        article = hits[0]
        answer = article.answer
        reference = f"参考处理规则：{article.title}"
    else:
        answer = "我们已记录您的问题，需要进一步核实订单和账号信息后为您处理。"
        reference = "未匹配到高置信知识库条目，建议人工确认。"

    next_step = next_step_for(classification, bool(hits))
    return "\n".join([opening, answer, next_step, reference])


def next_step_for(classification: ClassificationResult, has_hit: bool) -> str:
    if classification.category == "refund":
        return "请您保留商品状态和订单信息，我们会根据售后规则尽快确认退款或退货方案。"
    if classification.category == "logistics":
        return "我们会核对物流节点，如存在异常会联系承运方并同步最新进展。"
    if classification.category == "invoice":
        return "请确认发票抬头、税号和开票类型，信息完整后会尽快开具。"
    if classification.category == "complaint":
        return "该问题会转交专员复核，处理过程中会保留完整沟通记录。"
    if has_hit:
        return "您可以先按上述方式尝试处理，如仍未解决我们会继续跟进。"
    return "为了避免误判，我们建议转人工进一步确认。"
