import os

os.environ["SUPPORT_AGENT_LLM_ENABLED"] = "false"

from app.agents.ticket_agent import agent
from app.api.tickets import load_sample_tickets
from app.core.config import get_settings
from app.main import app
from app.models.schemas import FeedbackRequest, TicketAnalyzeRequest
from app.services.feedback_repository import feedback_repository


def main() -> None:
    result = agent.analyze(
        TicketAnalyzeRequest(message="快递显示签收了但是我没收到，物流也没人联系我。")
    )
    assert result.classification.category == "logistics"
    assert result.estimated_minutes_saved > 0
    assert len(result.reply_draft) > 10
    assert result.reply_source in {"template", "llm"}
    assert result.external_context is not None

    complaint = agent.analyze(
        TicketAnalyzeRequest(message="我要投诉，今天必须处理，不然我就差评曝光。")
    )
    assert complaint.should_escalate is True

    feedback = feedback_repository.save(
        FeedbackRequest(
            ticket_id=result.ticket_id,
            original_reply=result.reply_draft,
            revised_reply=result.reply_draft + "\n我们会持续同步处理进度。",
            category=result.classification.category,
            accepted=False,
            editor="smoke_check",
        )
    )
    assert feedback.id > 0
    assert feedback_repository.metrics().total_feedback >= 1

    tickets = load_sample_tickets(get_settings().sample_tickets_path)
    assert len(tickets) >= 5
    assert app.title == "Customer Support Agent API"
    print("smoke check passed")


if __name__ == "__main__":
    main()
