import json
import urllib.request


with urllib.request.urlopen("http://127.0.0.1:8011/api/admin/orders", timeout=30) as response:
    orders = json.loads(response.read().decode("utf-8"))["data"]

target = next(item for item in orders if item["order_id"] == "O90002")
print(
    target["order_id"],
    bool(target["latest_agent_reply_draft"]),
    target["latest_agent_reply_source"],
    target["latest_agent_final_reply_source"],
)
