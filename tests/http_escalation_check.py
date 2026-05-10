import json
import urllib.request


payload = {
    "customer_id": "C1005",
    "order_id": "O90005",
    "message": "你们客服态度太差了，我要投诉，今天必须处理，不然我就差评曝光。",
}

request = urllib.request.Request(
    "http://127.0.0.1:8010/api/tickets/analyze",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(request, timeout=60) as response:
    data = json.loads(response.read().decode("utf-8"))
    record = data.get("analysis_record_sync") or {}
    sync = data.get("business_sync") or {}
    print(data["should_escalate"], record.get("success"), sync.get("message"))
