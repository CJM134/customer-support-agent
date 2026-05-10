from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import Settings, get_settings
from app.models.schemas import BusinessSyncResult, CustomerContext, ExternalContext, OrderContext, ShipmentContext

logger = logging.getLogger(__name__)


class IntegrationApiClient:
    def __init__(self, base_url: str | None, token: str | None, timeout_seconds: float):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        self.timeout_seconds = timeout_seconds

    @property
    def ready(self) -> bool:
        return bool(self.base_url)

    def get(self, path_template: str, **params: str | None) -> dict[str, Any] | None:
        if not self.ready:
            return None

        try:
            path = self._format_path(path_template, params)
        except KeyError:
            return None
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = self.token if self.token.lower().startswith("bearer ") else f"Bearer {self.token}"

        try:
            with httpx.Client(timeout=self.timeout_seconds, trust_env=False) as client:
                response = client.get(url, headers=headers)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("External API request failed: %s", exc)
            return None

        return unwrap_payload(data)

    def post(self, path_template: str, payload: dict[str, Any], **params: str | None) -> dict[str, Any] | None:
        if not self.ready:
            return None

        try:
            path = self._format_path(path_template, params)
        except KeyError:
            return None
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = self.token if self.token.lower().startswith("bearer ") else f"Bearer {self.token}"

        try:
            with httpx.Client(timeout=self.timeout_seconds, trust_env=False) as client:
                response = client.post(url, headers=headers, json=payload)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("External API POST failed: %s", exc)
            return None

        return unwrap_payload(data) or data

    def _format_path(self, path_template: str, params: dict[str, str | None]) -> str:
        safe_params = {
            key: quote(str(value), safe="")
            for key, value in params.items()
            if value is not None
        }
        path = path_template.format(**safe_params)
        return path if path.startswith("/") else f"/{path}"


class ExternalSystemGateway:
    """HTTP adapter boundary for CRM, OMS, and logistics systems."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.crm = IntegrationApiClient(
            self.settings.crm_api_base_url,
            self.settings.crm_api_token,
            self.settings.external_api_timeout_seconds,
        )
        self.oms = IntegrationApiClient(
            self.settings.oms_api_base_url,
            self.settings.oms_api_token,
            self.settings.external_api_timeout_seconds,
        )
        self.logistics = IntegrationApiClient(
            self.settings.logistics_api_base_url,
            self.settings.logistics_api_token,
            self.settings.external_api_timeout_seconds,
        )

    def get_context(self, customer_id: str | None, order_id: str | None) -> ExternalContext:
        customer_data = self.crm.get(
            self.settings.crm_customer_path_template,
            customer_id=customer_id,
        )
        order_data = self.oms.get(
            self.settings.oms_order_path_template,
            order_id=order_id,
        )
        shipment_data = self.logistics.get(
            self.settings.logistics_shipment_path_template,
            order_id=order_id,
        )

        return ExternalContext(
            customer=normalize_customer(customer_data) if customer_data else None,
            order=normalize_order(order_data) if order_data else None,
            shipment=normalize_shipment(shipment_data) if shipment_data else None,
        )

    def mark_order_processed(self, order_id: str | None, note: str) -> BusinessSyncResult:
        if not order_id:
            return BusinessSyncResult(
                attempted=False,
                success=False,
                order_id=None,
                message="没有订单 ID，未同步业务后台。",
            )
        if not self.oms.ready:
            return BusinessSyncResult(
                attempted=False,
                success=False,
                order_id=order_id,
                message="OMS 未配置，未同步业务后台。",
            )

        data = self.oms.post(
            "/api/admin/orders/{order_id}/process",
            {"source": "agent", "resolution_note": note},
            order_id=order_id,
        )
        if data is None:
            return BusinessSyncResult(
                attempted=True,
                success=False,
                order_id=order_id,
                message="已尝试同步业务后台，但接口未返回成功。",
            )

        return BusinessSyncResult(
            attempted=True,
            success=True,
            order_id=order_id,
            message="已自动同步业务后台，订单标记为已处理。",
        )

    def save_agent_analysis(self, analysis: Any) -> BusinessSyncResult:
        if not self.oms.ready:
            return BusinessSyncResult(
                attempted=False,
                success=False,
                order_id=getattr(analysis, "order_id", None),
                message="OMS 未配置，未保存 Agent 分析记录。",
            )

        payload = {
            "ticket_id": analysis.ticket_id,
            "customer_id": analysis.customer_id,
            "order_id": analysis.order_id,
            "message": analysis.message,
            "category": analysis.classification.category,
            "priority": analysis.classification.priority,
            "confidence": analysis.classification.confidence,
            "reply_source": analysis.reply_source,
            "reply_draft": analysis.reply_draft,
            "should_escalate": analysis.should_escalate,
            "escalation_reason": analysis.escalation_reason,
            "business_sync_success": analysis.business_sync.success if analysis.business_sync else False,
            "business_sync_message": analysis.business_sync.message if analysis.business_sync else None,
            "estimated_minutes_saved": analysis.estimated_minutes_saved,
        }
        data = self.oms.post("/api/admin/agent-analyses", payload)
        if data is None:
            return BusinessSyncResult(
                attempted=True,
                success=False,
                order_id=analysis.order_id,
                message="已尝试保存 Agent 分析记录，但业务后台未返回成功。",
            )

        return BusinessSyncResult(
            attempted=True,
            success=True,
            order_id=analysis.order_id,
            message="Agent 分析记录已保存到业务后台。",
        )


def unwrap_payload(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        for key in ("data", "result", "customer", "order", "shipment"):
            value = data.get(key)
            if isinstance(value, dict):
                return value
        return data
    return None


def first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def normalize_customer(data: dict[str, Any]) -> CustomerContext:
    return CustomerContext(
        customer_id=first_value(data, "customer_id", "customerId", "id"),
        name=first_value(data, "name", "full_name", "fullName", "nickname"),
        segment=first_value(data, "segment", "tier", "level", "customer_level", "customerLevel"),
        lifetime_value=to_float(first_value(data, "lifetime_value", "lifetimeValue", "ltv", "total_spend", "totalSpend")),
        risk_level=first_value(data, "risk_level", "riskLevel", "risk", "complaint_risk", "complaintRisk"),
    )


def normalize_order(data: dict[str, Any]) -> OrderContext:
    return OrderContext(
        order_id=first_value(data, "order_id", "orderId", "id"),
        status=first_value(data, "status", "order_status", "orderStatus"),
        amount=to_float(first_value(data, "amount", "total_amount", "totalAmount", "pay_amount", "payAmount")),
        paid_at=first_value(data, "paid_at", "paidAt", "payment_time", "paymentTime", "created_at", "createdAt"),
        items=normalize_items(first_value(data, "items", "products", "goods", "order_items", "orderItems")),
        support_status=first_value(data, "support_status", "supportStatus", "service_status", "serviceStatus"),
        processed_at=first_value(data, "processed_at", "processedAt"),
        resolution_note=first_value(data, "resolution_note", "resolutionNote"),
    )


def normalize_shipment(data: dict[str, Any]) -> ShipmentContext:
    return ShipmentContext(
        order_id=first_value(data, "order_id", "orderId"),
        carrier=first_value(data, "carrier", "company", "express_company", "expressCompany"),
        tracking_no=first_value(data, "tracking_no", "trackingNo", "tracking_number", "trackingNumber", "waybill_no", "waybillNo"),
        status=first_value(data, "status", "logistics_status", "logisticsStatus", "delivery_status", "deliveryStatus"),
        latest_event=first_value(data, "latest_event", "latestEvent", "latest_status", "latestStatus", "latest_trace", "latestTrace"),
        last_updated_at=first_value(data, "last_updated_at", "lastUpdatedAt", "update_time", "updateTime"),
    )


def normalize_items(raw_items: Any) -> list[str]:
    if not isinstance(raw_items, list):
        return []

    items: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            items.append(item)
        elif isinstance(item, dict):
            name = first_value(item, "name", "title", "sku_name", "skuName", "product_name", "productName")
            if name:
                items.append(str(name))
    return items


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@lru_cache
def get_external_gateway() -> ExternalSystemGateway:
    return ExternalSystemGateway()
