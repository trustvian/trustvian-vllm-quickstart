"""Runs agent.py under run.py's instrumentation, with a scripted model and spans kept in memory.

No model server, no Trustvian, no network beyond loopback: the fake CRM and a
scripted OpenAI-compatible endpoint (fake_openai.py) run in threads on free
ports, and the agent calls that endpoint as it would vLLM.
"""
import os
import re
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The settings that keep content out of spans, taken from the agent service in
# compose.yaml — the configuration the stack ships — rather than restated
# here. The instrumentor reads them when it is turned on.
CONTENT_SETTINGS = ("OPENINFERENCE_HIDE_",)


def shipped_settings() -> dict:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    environment = compose["services"]["agent"]["environment"]
    settings = {}
    for key, value in environment.items():
        if key.startswith(CONTENT_SETTINGS):
            match = re.fullmatch(r"\$\{\w+:-(.*)\}", str(value))
            settings[key] = match.group(1) if match else str(value)
    return settings


os.environ.update(shipped_settings())
os.environ["CRM_ERROR_RATE"] = "0"  # each test decides which calls fail

import mock_services  # noqa: E402

_crm = HTTPServer(("127.0.0.1", 0), mock_services.Handler)
threading.Thread(target=_crm.serve_forever, daemon=True).start()
os.environ["CRM_URL"] = f"http://127.0.0.1:{_crm.server_address[1]}"

from fake_openai import FakeOpenAI  # noqa: E402

FAKE = FakeOpenAI()
# agent.py builds its client from these when it is imported.
os.environ["LLM_MODEL"] = "scripted-model"
os.environ["VLLM_BASE_URL"] = FAKE.url

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

import run  # noqa: E402

_exporter = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_exporter))
run.instrument(_provider)  # the harness's own setup, with an in-memory exporter


@pytest.fixture
def spans():
    _exporter.clear()
    yield _exporter.get_finished_spans
    _exporter.clear()


@pytest.fixture
def model():
    """The scripted endpoint: model.play([...]) sets what it answers next."""
    yield FAKE
    FAKE.play([])
