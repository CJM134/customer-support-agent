import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel


class Settings(BaseModel):
    app_name: str = "Customer Support Agent"
    baseline_minutes_per_ticket: float = 5.0
    agent_review_minutes_per_ticket: float = 1.2
    auto_close_minutes_per_ticket: float = 0.4
    data_dir: Path = Path(__file__).resolve().parents[2] / "data"
    feedback_db_path: Path = Path(__file__).resolve().parents[2] / "data" / "feedback.db"
    llm_enabled: bool = False
    llm_model: str = "qwen-plus"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 45.0
    llm_max_tokens: int = 420
    external_api_timeout_seconds: float = 8.0
    crm_api_base_url: str | None = None
    crm_api_token: str | None = None
    crm_customer_path_template: str = "/customers/{customer_id}"
    oms_api_base_url: str | None = None
    oms_api_token: str | None = None
    oms_order_path_template: str = "/orders/{order_id}"
    logistics_api_base_url: str | None = None
    logistics_api_token: str | None = None
    logistics_shipment_path_template: str = "/shipments/by-order/{order_id}"

    @property
    def llm_ready(self) -> bool:
        return self.llm_enabled and bool(self.llm_api_key)

    @property
    def crm_ready(self) -> bool:
        return bool(self.crm_api_base_url)

    @property
    def oms_ready(self) -> bool:
        return bool(self.oms_api_base_url)

    @property
    def logistics_ready(self) -> bool:
        return bool(self.logistics_api_base_url)

    @property
    def knowledge_base_path(self) -> Path:
        return self.data_dir / "knowledge_base.json"

    @property
    def sample_tickets_path(self) -> Path:
        return self.data_dir / "sample_tickets.json"

    @property
    def customer_profiles_path(self) -> Path:
        return self.data_dir / "customer_profiles.json"

    @property
    def orders_path(self) -> Path:
        return self.data_dir / "orders.json"

    @property
    def shipments_path(self) -> Path:
        return self.data_dir / "shipments.json"


@lru_cache
def get_settings() -> Settings:
    root_dir = Path(__file__).resolve().parents[2]
    for env_path in (root_dir.parent / ".env", root_dir / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)

    explicit_enabled = os.getenv("SUPPORT_AGENT_LLM_ENABLED")
    api_key = (
        os.getenv("SUPPORT_AGENT_LLM_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    return Settings(
        llm_enabled=explicit_enabled.lower() == "true" if explicit_enabled is not None else bool(api_key),
        llm_model=os.getenv("SUPPORT_AGENT_LLM_REPLY_MODEL", "qwen-plus"),
        llm_base_url=os.getenv("SUPPORT_AGENT_LLM_BASE_URL")
        or os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("OPENAI_BASE_URL"),
        llm_api_key=api_key,
        llm_timeout_seconds=float(os.getenv("SUPPORT_AGENT_LLM_TIMEOUT_SECONDS", "45")),
        llm_max_tokens=int(os.getenv("SUPPORT_AGENT_LLM_MAX_TOKENS", "420")),
        external_api_timeout_seconds=float(os.getenv("EXTERNAL_API_TIMEOUT_SECONDS", "8")),
        crm_api_base_url=os.getenv("CRM_API_BASE_URL"),
        crm_api_token=os.getenv("CRM_API_TOKEN"),
        crm_customer_path_template=os.getenv("CRM_CUSTOMER_PATH_TEMPLATE", "/customers/{customer_id}"),
        oms_api_base_url=os.getenv("OMS_API_BASE_URL"),
        oms_api_token=os.getenv("OMS_API_TOKEN"),
        oms_order_path_template=os.getenv("OMS_ORDER_PATH_TEMPLATE", "/orders/{order_id}"),
        logistics_api_base_url=os.getenv("LOGISTICS_API_BASE_URL"),
        logistics_api_token=os.getenv("LOGISTICS_API_TOKEN"),
        logistics_shipment_path_template=os.getenv(
            "LOGISTICS_SHIPMENT_PATH_TEMPLATE",
            "/shipments/by-order/{order_id}",
        ),
    )
