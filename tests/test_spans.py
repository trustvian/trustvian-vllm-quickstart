"""The two-ticket agent (steps 3 and 4) under run.py: its tool spans, and no ticket text in any span."""
import agent
from fake_openai import answer, tool_call

TICKET_TEXTS = [t["text"] for t in agent.TICKETS]


def test_the_reference_agent_emits_its_tools_and_no_ticket_text(spans, model):
    model.play([
        tool_call("lookup_customer", customer_id="c-1001"),
        tool_call("send_reply", customer_id="c-1001", message="Sorry, refunded."),
        answer("Used lookup_customer and send_reply."),
        tool_call("lookup_customer", customer_id="c-1002"),
        tool_call("send_reply", customer_id="c-1002", message="Use the reset link."),
        answer("Used lookup_customer and send_reply."),
    ])
    for ticket in agent.TICKETS:
        agent.run_ticket(ticket)
    finished = spans()
    tools = [s.attributes.get("tool.name") for s in finished
             if s.attributes.get("openinference.span.kind") == "TOOL"]
    assert tools == ["lookup_customer", "send_reply", "lookup_customer", "send_reply"]
    for span in finished:
        for key, value in span.attributes.items():
            for text in TICKET_TEXTS + ["Sorry, refunded.", "Use the reset link."]:
                assert text not in str(value), (span.name, key)
