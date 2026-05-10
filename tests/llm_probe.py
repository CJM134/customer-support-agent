from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    print("enabled=", settings.llm_enabled)
    print("ready=", settings.llm_ready)
    print("model=", settings.llm_model)
    print("base_url_set=", bool(settings.llm_base_url))
    print("api_key_set=", bool(settings.llm_api_key))

    if not settings.llm_ready:
        return

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=20,
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": "请只回复：LLM_OK"}],
            temperature=0,
        )
        print("reply=", response.choices[0].message.content)
    except Exception as exc:
        print("error_type=", type(exc).__name__)
        print("error=", str(exc))


if __name__ == "__main__":
    main()
