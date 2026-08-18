# Instructions for GitHub Copilot in this repository

- This project builds a customer-inquiry triage agent using the
  Microsoft Agent Framework (Python package name: agent-framework).
- Use the Foundry integration: FoundryChatClient from
  agent_framework.foundry, authenticated with AzureCliCredential from
  azure.identity. Do NOT use API keys or hardcoded secrets.
- Read the Foundry project endpoint and model deployment name from
  environment variables: FOUNDRY_PROJECT_ENDPOINT and
  FOUNDRY_MODEL_DEPLOYMENT_NAME. Load them from a .env file using
  python-dotenv.
- Keep the entire agent in a single file: src/agent.py. Keep it simple
  and heavily commented in plain English — the maintainer is not a
  professional developer.
- The agent's behavior, categories, grounding rules, output format, and
  escalation rules are defined in SPEC.md. Treat SPEC.md as the source
  of truth; put its rules into the agent's system instructions.
- Ground the agent in the two documents under /docs.
- src/agent.py should be runnable from the terminal as:
  python src/agent.py sample-inquiries/new-job.txt
  It reads the inquiry file passed as an argument and prints the
  structured result (category, confidence, escalate, draft_reply).
- Do not use deprecated Semantic Kernel or AutoGen APIs. Use only
  current agent-framework (v1.x) patterns.
- Do not add extra frameworks, servers, databases, or UI. Terminal
  output only.