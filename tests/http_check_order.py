import json
import sys
import urllib.request

customer_id = sys.argv[1] if len(sys.argv) > 1 else "C1001"
order_id = sys.argv[2] if len(sys.argv) > 2 else "O90001"
message = sys.argv[3] if len(sys.argv) > 3 else "快递显示签收了但是我没收到，物流也没人联系我，麻烦尽快处理。"

payload = {
    "customer_id": customer_id,
    "order_id": order_id,
    "message": message,
}

request = urllib.request.Request(
    "http://127.0.0.1:8010/api/tickets/analyze",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(request, timeout=60) as response:
    data = json.loads(response.read().decode("utf-8"))
    sync = data.get("business_sync") or {}
    print(data["classification"]["category"], data["reply_source"], sync.get("success"), sync.get("message"))
