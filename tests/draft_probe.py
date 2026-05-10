from app.models.schemas import ClassificationResult
from app.services.knowledge_base import get_knowledge_base
from app.services.llm_reply import reply_generator


def main() -> None:
    classification = ClassificationResult(
        category="logistics",
        priority="high",
        confidence=0.8,
        matched_keywords=["物流"],
        risk_flags=[],
    )
    hits = get_knowledge_base().search("快递显示签收了但是我没收到，物流也没人联系我。", "logistics")
    draft = reply_generator.draft(
        message="快递显示签收了但是我没收到，物流也没人联系我。",
        classification=classification,
        hits=hits,
        context=None,
    )
    print("source=", draft.source)
    print("content=", draft.content[:100])


if __name__ == "__main__":
    main()
