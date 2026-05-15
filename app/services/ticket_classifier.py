from __future__ import annotations

import logging
from collections import Counter

from app.models.schemas import Category, ClassificationResult, Priority

logger = logging.getLogger(__name__)


CATEGORY_KEYWORDS: dict[Category, list[str]] = {
    "refund": [
        "退款", "退钱", "退货", "取消", "不想要", "赔付", "售后",
        "不符", "货不对板", "虚假宣传", "退货退款", "退运费",
    ],
    "logistics": [
        "物流", "快递", "发货", "配送", "没收到", "签收", "延迟", "运单",
        "未收到", "没到", "催派",
    ],
    "account": ["登录", "账号", "密码", "验证码", "绑定", "注册", "会员", "手机号", "换绑"],
    "product": [
        "质量", "破损", "坏了", "尺寸", "颜色", "少件", "配件", "说明书",
        "瑕疵", "起球", "不 work", "质量问题",
    ],
    "complaint": [
        "投诉", "生气", "差评", "曝光", "欺骗", "态度", "维权",
        "实物不一样", "图文不符", "假冒", "骗人",
    ],
    "invoice": ["发票", "抬头", "税号", "报销", "专票", "普票", "开票"],
    "other": [],
}

URGENT_WORDS = ["投诉", "曝光", "维权", "欺骗", "严重", "马上", "立刻", "今天必须"]
HIGH_VALUE_WORDS = ["大客户", "企业采购", "批量", "续费", "VIP", "会员"]
NEGATIVE_WORDS = ["生气", "失望", "差评", "垃圾", "太差", "不满", "欺骗"]


##选出最高分类，匹配关键词，识别风险标记
def classify_ticket(message: str) -> ClassificationResult:
    normalized = message.lower()
    scores: Counter[str] = Counter()
    matched: list[str] = []

    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in normalized:
                scores[category] += 1
                matched.append(keyword)

    category = scores.most_common(1)[0][0] if scores else "other"
    confidence = 0.45
    if scores:
        top_score = scores[category]
        confidence = min(0.95, 0.55 + top_score * 0.12)

    risk_flags = []
    if any(word.lower() in normalized for word in URGENT_WORDS):
        risk_flags.append("urgent_language")
    if any(word.lower() in normalized for word in HIGH_VALUE_WORDS):
        risk_flags.append("high_value_customer")
    if any(word.lower() in normalized for word in NEGATIVE_WORDS):
        risk_flags.append("negative_sentiment")

    ##判定优先级
    priority = decide_priority(category=category, risk_flags=risk_flags, confidence=confidence)

    logger.debug(
        "文本分类结果: category=%s priority=%s confidence=%.2f keywords=%s flags=%s",
        category, priority, round(confidence, 2), matched, risk_flags,
    )
    return ClassificationResult(
        category=category,
        priority=priority,
        confidence=round(confidence, 2),
        matched_keywords=sorted(set(matched)),
        risk_flags=risk_flags,
    )


def decide_priority(category: Category, risk_flags: list[str], confidence: float) -> Priority:
    if "urgent_language" in risk_flags or "negative_sentiment" in risk_flags:
        return "urgent"
    if "high_value_customer" in risk_flags or category == "complaint":
        return "high"
    if confidence < 0.55:
        return "normal"
    return "normal" if category in {"refund", "logistics", "invoice"} else "low"
