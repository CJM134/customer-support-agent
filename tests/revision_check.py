import json
import urllib.request


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


analyses = request_json("http://127.0.0.1:8011/api/admin/agent-analyses")
ticket_id = analyses["data"][0]["ticket_id"]

request_json(
    f"http://127.0.0.1:8011/api/admin/agent-analyses/{ticket_id}/revision",
    method="POST",
    payload={
        "revised_reply": "这是人工最终回复，用于验证业务后台显示。",
        "editor": "revision_check",
        "notes": "test",
    },
)

updated = request_json("http://127.0.0.1:8011/api/admin/agent-analyses")
first = updated["data"][0]
print(first["ticket_id"], first["display_reply_source"], first["display_reply"])
