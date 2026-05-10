from app.services.structured_output import parse_structured_decision


def test_parse_structured_decision_from_markdown_fence():
    decision = parse_structured_decision(
        """
        ```json
        {
          "category": "logistics",
          "priority": "high",
          "reply": "我们会马上核查物流节点，并同步处理进展。",
          "should_escalate": false,
          "confidence": 0.82,
          "risk_flags": ["logistics_delay"]
        }
        ```
        """
    )

    assert decision.category == "logistics"
    assert decision.priority == "high"
    assert decision.should_escalate is False


def test_parse_structured_decision_rejects_invalid_category():
    try:
        parse_structured_decision(
            '{"category":"shipping","priority":"high","reply":"ok","should_escalate":false}'
        )
    except Exception:
        return
    raise AssertionError("invalid category should be rejected")


if __name__ == "__main__":
    test_parse_structured_decision_from_markdown_fence()
    test_parse_structured_decision_rejects_invalid_category()
    print("structured_parse_check ok")
