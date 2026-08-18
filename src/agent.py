"""
Contoso Home Services - Customer Inquiry Triage Agent
======================================================

Plain-English overview for the maintainer:

This is a small command-line program. You give it a text file containing a
customer's email/web-form inquiry, and it prints back a structured triage
result: what kind of inquiry it is, how confident the AI is, whether a human
needs to jump on it right away, and a draft reply a human can review and send.

It is built with the Microsoft Agent Framework (the "agent_framework" Python
package) talking to a model hosted on Microsoft Foundry.

Before running this for the first time:
  1. Install the required packages:
       pip install agent-framework-foundry azure-identity python-dotenv
  2. Copy .env.sample to .env (same folder as this file's parent, i.e. the
     project root) and fill in your real Foundry project endpoint and model
     deployment name.
  3. Make sure you're logged in to Azure CLI (run: az login) because we use
     AzureCliCredential to authenticate - no API keys are stored anywhere.

How to run it:
    python src/agent.py sample-inquiries/new-job.txt

The agent's rules (what counts as each category, when to escalate, how the
reply should sound, etc.) all come from SPEC.md and are baked into the
instructions we give the model below. The agent is only allowed to use the
two documents in /docs (price-sheet.md and service-policy.md) for prices and
policies - if something isn't covered there, it's told to say so rather than
make something up.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Literal

# python-dotenv loads variables from a local .env file into the environment,
# so we don't have to hardcode secrets or endpoints in this file.
from dotenv import load_dotenv

# pydantic lets us describe exactly what shape we want the AI's answer to be
# in (category, confidence, escalate, draft_reply). The Agent Framework will
# ask the model to fill this shape in and hand us back a validated object.
from pydantic import BaseModel, Field

# These are the Microsoft Agent Framework pieces we need:
#   - Agent: wraps a chat client + instructions into something you can "run"
#   - FoundryChatClient: talks to a model deployed on Microsoft Foundry
from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient

# AzureCliCredential re-uses your `az login` session instead of needing an
# API key. This is the Foundry-recommended, secret-free way to authenticate.
from azure.identity import AzureCliCredential

# Load the .env file (if present) so FOUNDRY_PROJECT_ENDPOINT and
# FOUNDRY_MODEL_DEPLOYMENT_NAME are available as environment variables.
load_dotenv()

# ---------------------------------------------------------------------------
# 1. Figure out where things live on disk.
# ---------------------------------------------------------------------------
# This file lives in <project-root>/src/agent.py, so the project root is one
# folder up from here. We use that to reliably find the /docs folder no
# matter what directory the script is run from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
PRICE_SHEET_PATH = DOCS_DIR / "price-sheet.md"
SERVICE_POLICY_PATH = DOCS_DIR / "service-policy.md"


# ---------------------------------------------------------------------------
# 2. Describe the exact shape of the answer we want back from the AI.
# ---------------------------------------------------------------------------
# This mirrors the "Output format" section of SPEC.md exactly. Using a
# pydantic model like this means the Agent Framework will ask the model for
# a JSON answer matching this shape, and will parse it back into a real
# Python object for us (see TriageResult.category, .confidence, etc. below).
class TriageResult(BaseModel):
    """The structured result the agent must produce for every inquiry."""

    category: Literal["NEW_JOB", "SCHEDULING", "COMPLAINT", "BILLING"] = Field(
        description="Exactly one of the four inquiry categories."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="How confident the agent is in the category above."
    )
    escalate: bool = Field(
        description="True if a human needs to look at this right away."
    )
    escalate_reason: str = Field(
        default="",
        description=(
            "A one-line reason why escalate is true. Leave this as an empty "
            "string when escalate is false."
        ),
    )
    draft_reply: str = Field(
        description="A warm, short, plain-spoken draft reply for a human to review and send."
    )


# ---------------------------------------------------------------------------
# 3. Build the instructions we give the AI (this is where SPEC.md becomes
#    actual behavior instead of just a document).
# ---------------------------------------------------------------------------
def load_grounding_documents() -> str:
    """Read the two /docs files and return their text, ready to paste into
    the agent's instructions. These are the ONLY source of truth the agent
    is allowed to use for prices and policies."""
    price_sheet_text = PRICE_SHEET_PATH.read_text(encoding="utf-8")
    service_policy_text = SERVICE_POLICY_PATH.read_text(encoding="utf-8")

    return (
        "=== price-sheet.md ===\n"
        f"{price_sheet_text}\n\n"
        "=== service-policy.md ===\n"
        f"{service_policy_text}"
    )


def build_instructions() -> str:
    """Build the full system instructions for the agent, combining the
    behavior rules from SPEC.md with the grounding documents from /docs."""
    grounding_documents = load_grounding_documents()

    return f"""You are the customer-inquiry triage agent for Contoso Home
Services, a 12-person plumbing and HVAC company serving the greater Seattle
area. Customer inquiries arrive by email and web form at all hours, and the
owner currently triages them personally, often late at night. Your job is to
save the owner time by doing that triage for them.

For every inquiry you are given, do all of the following:

1. CLASSIFY the inquiry into exactly one of these four categories:
   - NEW_JOB - a request for new work or a price estimate
   - SCHEDULING - a change, confirmation, or question about an existing appointment
   - COMPLAINT - dissatisfaction with completed or ongoing work
   - BILLING - a question about an invoice or payment

2. GROUND YOURSELF ONLY in the two reference documents below. They are the
   company's current prices and policies. Use ONLY these documents for any
   prices or policy statements you make. If the documents do not cover
   something the customer is asking about, say so plainly instead of
   guessing or making something up.

{grounding_documents}

3. DRAFT A REPLY for a human to review and send (never send anything
   yourself). The reply must be:
   - warm and plain-spoken, and short
   - never committing to a specific appointment time or date (a human
     coordinator schedules all appointments)
   - for NEW_JOB inquiries, include relevant estimated prices pulled from the
     price sheet above

4. DECIDE WHETHER TO ESCALATE. Set escalate to true when any of these are true:
   - the inquiry is a COMPLAINT, or
   - the inquiry mentions active water damage, a gas smell, or any other
     safety risk, or
   - your classification confidence is low
   When escalate is true, fill in escalate_reason with a single short
   sentence explaining why. When escalate is false, leave escalate_reason as
   an empty string.

Remember: you never send anything to the customer yourself. Every draft_reply
you produce is only a draft for a human to approve and send.
"""


# ---------------------------------------------------------------------------
# 4. The main program: read the inquiry file, ask the agent to triage it,
#    and print the structured result.
# ---------------------------------------------------------------------------
async def triage_inquiry(inquiry_text: str) -> TriageResult:
    """Send one customer inquiry to the Foundry-hosted model and get back a
    validated TriageResult."""

    # FoundryChatClient reads FOUNDRY_PROJECT_ENDPOINT and
    # FOUNDRY_MODEL_DEPLOYMENT_NAME from the environment (loaded above from
    # .env via load_dotenv()). AzureCliCredential means we authenticate using
    # your existing `az login` session - no API keys anywhere in this file.
    client = FoundryChatClient(
        credential=AzureCliCredential(),
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],
    )

    agent = Agent(
        client=client,
        name="ContosoTriageAgent",
        instructions=build_instructions(),
    )

    # Asking for options={"response_format": TriageResult} tells the Agent
    # Framework to request a JSON answer shaped like our TriageResult model,
    # and to parse it for us. The parsed object shows up on result.value.
    result = await agent.run(
        inquiry_text,
        options={"response_format": TriageResult},
    )

    if result.value is None:
        raise RuntimeError(
            "The agent did not return a result matching the expected "
            f"format. Raw response was:\n{result.text}"
        )

    return result.value


def print_result(result: TriageResult) -> None:
    """Print the triage result to the terminal in a simple, readable way."""
    print("category:     ", result.category)
    print("confidence:   ", result.confidence)
    if result.escalate:
        print("escalate:      true -", result.escalate_reason)
    else:
        print("escalate:      false")
    print("draft_reply:")
    print(result.draft_reply)


async def main() -> None:
    # We expect exactly one argument: the path to the inquiry text file.
    if len(sys.argv) != 2:
        print("Usage: python src/agent.py <path-to-inquiry-file>")
        sys.exit(1)

    inquiry_path = Path(sys.argv[1])
    inquiry_text = inquiry_path.read_text(encoding="utf-8")

    result = await triage_inquiry(inquiry_text)
    print_result(result)


if __name__ == "__main__":
    asyncio.run(main())
