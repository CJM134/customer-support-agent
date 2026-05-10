import json
import urllib.request


payload = {
    "customer_id": "C-DEMO",
    "order_id": "O-DEMO",
    "message": "收到的商品破损了，里面配件也少了一包。",
}

request = urllib.request.Request(
    "http://127.0.0.1:8010/api/tickets/analyze",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(request, timeout=45) as response:
    data = json.loads(response.read().decode("utf-8"))
    print(data["classification"]["category"], data["reply_source"])
