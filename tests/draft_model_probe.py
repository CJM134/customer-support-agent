import os
import sys

model = sys.argv[1] if len(sys.argv) > 1 else "qwen-turbo"
os.environ["SUPPORT_AGENT_LLM_MODEL"] = model
os.environ["SUPPORT_AGENT_LLM_TIMEOUT_SECONDS"] = "30"

from tests.draft_probe import main

print("model=", model)
main()
