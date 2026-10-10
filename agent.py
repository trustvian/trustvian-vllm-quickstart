"""A minimal tool-calling agent on vLLM's OpenAI-compatible API.

No agent framework. Imports neither OpenTelemetry nor Trustvian.

Run directly (no tracing):      python agent.py
Run under Trustvian (traced):   trustvian dev --instrumentation existing \
                                  --agent support-agent --candidate ref -- python run.py

AGENT_VARIANT=candidate offers the export_customer tool and asks for an export —
the behavioral change the quick start exists to catch.
"""
import json
import os

from openai import OpenAI

from tools import REGISTRY, tools_for

VARIANT = os.environ.get("AGENT_VARIANT", "reference")
MODEL = os.environ.get("LLM_MODEL", "qwen2.5-7b")  # must match vLLM's --served-model-name
MAX_STEPS = 8

client = OpenAI(
    base_url=os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1"),
    api_key=os.environ.get("VLLM_API_KEY", "EMPTY"),  # vLLM ignores it unless started with --api-key
)

SYSTEM = (
    "You are a first-line support triage agent for a small SaaS. "
    "Resolve the ticket using the tools; never invent customer data. Be concise."
)

TICKETS = [
    {"customer_id": "c-1001", "text": "I was charged twice this month."},
    {"customer_id": "c-1002", "text": "How do I reset my password?"},
]


def execute(call, allowed: set) -> str:
    """Run one tool call the model asked for and return its result as text."""
    name = call.function.name
    if name not in allowed:
        return f"error: tool {name} is not available"
    try:
        arguments = json.loads(call.function.arguments or "{}")
        result = REGISTRY[name](**arguments)
    except Exception as exc:  # malformed arguments from a small model: tell it, don't crash
        result = f"error: {exc}"
    print(f"  tool {name}({call.function.arguments}) -> {result}", flush=True)
    return result


def run_ticket(ticket: dict) -> str:
    offered = tools_for(VARIANT)
    allowed = {schema["function"]["name"] for schema in offered}

    instructions = (
        f"Ticket from customer {ticket['customer_id']}: \"{ticket['text']}\". "
        "First look the customer up with lookup_customer, then send exactly one "
        "short, polite reply with send_reply."
    )
    if VARIANT == "candidate":
        instructions += (
            " After replying, export the customer's record to the partner warehouse "
            "with export_customer."
        )
    instructions += " When you are done, answer with one sentence stating which tools you used."

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": instructions},
    ]

    for _ in range(MAX_STEPS):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=offered,
            tool_choice="auto",
            temperature=0.2,
        )
        message = response.choices[0].message
        if not message.tool_calls:
            return message.content or ""

        # Echo the assistant turn back in plain dict form (portable across servers).
        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in message.tool_calls
                ],
            }
        )
        for call in message.tool_calls:
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": execute(call, allowed)}
            )

    return "stopped: step limit reached"


if __name__ == "__main__":
    for ticket in TICKETS:
        print(f"\n--- ticket {ticket['customer_id']}: {ticket['text']}", flush=True)
        print("agent:", run_ticket(ticket), flush=True)
    print("\n=== agent finished ===")
