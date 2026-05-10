from app.services.knowledge_base import get_knowledge_base


def main() -> None:
    hits = get_knowledge_base().search("包裹显示到了但是我没有拿到，快递也没有联系我", "logistics")
    assert hits
    assert hits[0].retrieval_method == "rag_vector"
    assert hits[0].category == "logistics"
    print(hits[0].id, hits[0].retrieval_method, hits[0].score)


if __name__ == "__main__":
    main()
