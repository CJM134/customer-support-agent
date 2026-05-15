"""用 LLM 扩增知识库条目。

用法:
  uv run scripts/expand_knowledge_base.py [--per-category N]

环境变量（复用项目现有配置）:
  SUPPORT_AGENT_LLM_API_KEY  或 DASHSCOPE_API_KEY / OPENAI_API_KEY
  SUPPORT_AGENT_LLM_BASE_URL 或 DASHSCOPE_BASE_URL / OPENAI_BASE_URL
  SUPPORT_AGENT_LLM_REPLY_MODEL  (默认 qwen-plus)
"""

import json
import os
import sys
from pathlib import Path

# 将项目根目录加入 sys.path，复用项目配置
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings


CATEGORIES = [
    "refund",
    "logistics",
    "account",
    "product",
    "complaint",
    "invoice",
    "other",
]

CATEGORY_SCENARIOS = {
    "refund": [
        "商品与描述不符要求退款",
        "重复支付申请退款",
        "已发货状态下申请退款",
        "部分退款（只退其中一件）",
        "超过7天退货时限但商品有质量问题",
        "优惠券/满减订单退款金额计算",
        "虚拟商品购买后申请退款",
    ],
    "logistics": [
        "包裹显示签收但客户未收到",
        "物流信息长时间不更新",
        "发货后修改地址",
        "包裹被退回发件地",
        "配送员态度恶劣",
        "跨境物流清关问题",
        "运费争议（退货运费谁承担）",
    ],
    "account": [
        "手机号换绑申请",
        "账号被盗用下单",
        "第三方登录无法使用",
        "账号被限制/冻结",
        "修改账号个人信息",
        "多个账号合并申请",
        "注销账号流程",
    ],
    "product": [
        "收到的商品有使用痕迹",
        "商品颜色/款式发错",
        "商品尺寸不合适想换货",
        "赠品缺失或破损",
        "组装/安装问题",
        "保质期过短或临期",
        "配件单独购买渠道",
    ],
    "complaint": [
        "客服响应慢或态度差",
        "承诺的补偿未兑现",
        "平台活动规则不透明",
        "商家拒绝履行售后",
        "配送超时导致损失",
        "商品与页面描述严重不符",
        "自动扣费未告知",
    ],
    "invoice": [
        "需要补开发票",
        "发票内容开错",
        "电子发票未收到",
        "企业专票资质审核",
        "发票丢失要求重开",
        "多订单合并开票",
        "跨境订单是否需要发票",
    ],
    "other": [
        "优惠券无法使用",
        "积分过期问题",
        "会员等级降级",
        "活动奖励未到账",
        "价格保护申请",
        "团购订单问题",
        "礼品卡使用问题",
    ],
}


def load_existing(path: Path) -> list[dict]:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return []


def build_prompt(category: str, scenarios: list[str], existing: list[dict]) -> str:
    existing_titles = [e["title"] for e in existing if e["category"] == category]
    return f"""你是一个电商客服知识库助手。请为 "{category}" 分类生成新的 FAQ 条目。

现有条目标题（请避免重复）：
{chr(10).join(f'  - {t}' for t in existing_titles) if existing_titles else "  暂无"}

请参考以下客户场景，每个场景生成 1 条知识库条目：
{chr(10).join(f'  {i+1}. {s}' for i, s in enumerate(scenarios))}

JSON 格式要求——返回一个数组，每个元素包含：
- id: 格式 KB-<分类大写>-<3位数字>，如 KB-REFUND-003
- category: "{category}"
- title: 简短的标题（10字以内）
- keywords: array of strings，5-8个关键词，覆盖客户常用的口语化说法
- answer: 完整的客服回复，语气自然、方案明确，50-150字

只输出 JSON 数组，不要输出额外文本。"""


def generate_entries(client, model: str, prompt: str) -> list[dict]:
    response = client.chat.completions.create(
        model=model,
        temperature=0.7,
        max_tokens=2000,
        messages=[
            {
                "role": "system",
                "content": "你是一个电商客服知识库助手。只输出 JSON，不要输出任何解释。",
            },
            {"role": "user", "content": prompt},
        ],
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("LLM 返回空内容")

    # 兼容可能包含的 markdown 代码块
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def merge_and_dedupe(existing: list[dict], new_entries: list[dict]) -> list[dict]:
    seen_ids = {e["id"] for e in existing}
    seen_titles = {e["title"] for e in existing}
    merged = list(existing)

    for entry in new_entries:
        if entry["id"] in seen_ids:
            continue
        if entry["title"] in seen_titles:
            continue
        merged.append(entry)
        seen_ids.add(entry["id"])
        seen_titles.add(entry["title"])
    return merged


def main():
    from openai import OpenAI

    settings = get_settings()
    if not settings.llm_enabled or not settings.llm_api_key:
        print("错误: LLM 未配置，请设置 SUPPORT_AGENT_LLM_API_KEY 等环境变量")
        sys.exit(1)

    knowledge_path = settings.knowledge_base_path
    existing = load_existing(knowledge_path)
    print(f"现有知识库: {len(existing)} 条")

    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=45,
        max_retries=2,
    )
    model = settings.llm_model

    per_category = 5
    if len(sys.argv) > 2 and sys.argv[1] == "--per-category":
        per_category = int(sys.argv[2])

    all_new: list[dict] = []
    for category in CATEGORIES:
        scenarios = CATEGORY_SCENARIOS.get(category, [])
        if not scenarios:
            continue

        print(f"\n生成 [{category}] 分类条目...", end=" ", flush=True)
        try:
            prompt = build_prompt(category, scenarios[:per_category], existing)
            entries = generate_entries(client, model, prompt)
            all_new.extend(entries)
            print(f"✓ 新增 {len(entries)} 条")
        except Exception as e:
            print(f"✗ 失败: {e}")

    if not all_new:
        print("\n未生成任何新条目")
        return

    ##验证所有新条目必填字段
    required = {"id", "category", "title", "keywords", "answer"}
    valid = []
    for entry in all_new:
        missing = required - set(entry.keys())
        if missing:
            print(f"  跳过 {entry.get('id', '?')}: 缺少字段 {missing}")
            continue
        valid.append(entry)

    merged = merge_and_dedupe(existing, valid)
    print(f"\n扩增前: {len(existing)} 条")
    print(f"新增(有效): {len(valid)} 条")
    print(f"扩增后: {len(merged)} 条")

    with knowledge_path.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"已保存到: {knowledge_path}")


if __name__ == "__main__":
    main()
