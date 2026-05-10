import json
import urllib.request


payload = {
    "customer_id": "C1005",
    "order_id": "O90005",
    "message": "你们客服态度太差了，我要投诉，今天必须处理，不然我就差评曝光。",
}

request = urllib.request.Request(
    "http://127.0.0.1:8010/api/tickets/analyze/stream",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(request, timeout=90) as response:
    body = response.read().decode("utf-8")

events = []
for frame in body.split("\n\n"):
    if not frame.startswith("data: "):
        continue
    events.append(json.loads(frame[6:])["event"])

print(" ".join(events))
assert "classified" in events
assert "retrieved" in events
assert "reply_delta" in events
assert "completed" in events
