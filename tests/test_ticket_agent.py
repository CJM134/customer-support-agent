from app.agents.ticket_agent import agent
from app.models.schemas import TicketAnalyzeRequest


def test_refund_ticket_gets_refund_category():
    result = agent.analyze(
        TicketAnalyzeRequest(
            message="还没发货，我不想要了，请帮我取消订单并退款。",
            customer_id="C-TEST",
            order_id="O-TEST",
        )
    )

    assert result.classification.category == "refund"
    assert result.estimated_minutes_saved > 0
    assert result.knowledge_hits


def test_complaint_ticket_escalates():
    result = agent.analyze(
        TicketAnalyzeRequest(
            message="我要投诉，今天必须处理，不然我就差评曝光。",
            customer_id="C-TEST",
            order_id="O-TEST",
        )
    )

    assert result.should_escalate is True
    assert result.classification.priority == "urgent"
