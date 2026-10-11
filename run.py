"""Instrumentation harness: stands in for the tracing a framework would ship.

Everything here reads the environment `trustvian dev` hands the child:
  OTEL_EXPORTER_OTLP_TRACES_ENDPOINT   where spans go (dev's Collector)
  OTEL_RESOURCE_ATTRIBUTES             deployment.environment.name=local
  OTEL_SERVICE_NAME                    the agent identity (from --agent)
  OTEL_SEMCONV_STABILITY_OPT_IN=http   new HTTP attribute names
  OTEL_BSP_SCHEDULE_DELAY              short batch flush

It turns on three things:
  - the OpenInference OpenAI instrumentor -> an LLM span per chat completion
    (Trustvian shows `llm · <model>`; works against vLLM because the client
    is the OpenAI SDK with a custom base_url)
  - the requests instrumentor            -> the HTTP beneath each tool
  - a TOOL span around every function in tools.REGISTRY, carrying the two
    attributes Trustvian reads from OpenInference: openinference.span.kind=TOOL
    and tool.name. With a framework (CrewAI, LangGraph, ...) its instrumentor
    emits these; with a hand-written loop the harness does.

agent.py and tools.py never import any of this. REGISTRY entries are replaced
in place, so the agent's own lookup finds the traced wrapper.

Usage:  trustvian dev --instrumentation existing --agent support-agent -- python run.py
"""
import atexit
import functools
import os
import runpy
import sys

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from openinference.instrumentation.openai import OpenAIInstrumentor


def traced_tool(tracer, name, fn):
    """Wrap a tool function in an OpenInference TOOL span. Content-free: only the name."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with tracer.start_as_current_span(
            name, attributes={"openinference.span.kind": "TOOL", "tool.name": name}
        ):
            return fn(*args, **kwargs)

    return wrapper


def instrument(provider: TracerProvider) -> None:
    """The instrumentors and the TOOL spans, on `provider`. The tests call this with an in-memory exporter."""
    OpenAIInstrumentor().instrument(tracer_provider=provider)
    RequestsInstrumentor().instrument(tracer_provider=provider)

    import tools  # the same module object agent.py will import

    tracer = provider.get_tracer("quickstart.harness")
    for name, fn in list(tools.REGISTRY.items()):
        tools.REGISTRY[name] = traced_tool(tracer, name, fn)


def main() -> None:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    if not endpoint:
        sys.exit(
            "run.py: no OTLP endpoint in the environment — run this under "
            "`trustvian dev --instrumentation existing -- python run.py`"
        )
    print(f"run.py: exporter → {endpoint}", flush=True)

    # Resource.create() reads OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES;
    # OTLPSpanExporter() reads the endpoint and protocol variables; the batch
    # processor reads OTEL_BSP_SCHEDULE_DELAY. Nothing is hard-coded here.
    provider = TracerProvider(resource=Resource.create())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    atexit.register(provider.shutdown)  # flush before the process exits

    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)

    instrument(provider)

    runpy.run_path(os.path.join(here, "agent.py"), run_name="__main__")


if __name__ == "__main__":
    main()
