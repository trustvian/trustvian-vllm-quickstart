"""A scripted OpenAI-compatible chat endpoint, standard library only.

The tests point the agent's model at it: each POST to /v1/chat/completions
answers with the next scripted message, either tool calls or a final answer,
so the agent and its instrumentation run for real without a model.
"""
import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

MODEL = "scripted-model"


def tool_call(name: str, **arguments) -> dict:
    return {"tool_calls": [(name, arguments)]}


def answer(text: str) -> dict:
    return {"content": text}


class FakeOpenAI:
    def __init__(self):
        self.script = []
        self.requests = []
        self._ids = itertools.count(1)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                server.requests.append(json.loads(self.rfile.read(length) or b"{}"))
                step = server.script.pop(0) if server.script else answer("done")
                message = {"role": "assistant", "content": step.get("content")}
                if "tool_calls" in step:
                    message["tool_calls"] = [
                        {"id": f"call_{next(server._ids)}", "type": "function",
                         "function": {"name": name, "arguments": json.dumps(args)}}
                        for name, args in step["tool_calls"]
                    ]
                body = json.dumps({
                    "id": f"chatcmpl-{next(server._ids)}", "object": "chat.completion",
                    "created": 0, "model": MODEL,
                    "choices": [{"index": 0, "message": message,
                                 "finish_reason": "tool_calls" if "tool_calls" in step else "stop"}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def play(self, steps: list) -> None:
        self.script = list(steps)
        self.requests.clear()
