"""A scripted OpenAI-compatible endpoint for Hermes in the tests: real Hermes, no model calls.

    plan = {"parent": [turn, ...], "children": [[turn, ...], ...]}
    turn = {"calls": [[name, args], ...]} or {"text": "..."}

A request with `delegate_task` among its tools is the parent's; one with bank tools and no
`delegate_task` is a child's (children run one at a time: a request with no assistant message
yet starts the next child); any other request (titles, summaries) gets a short text.
Every request's role and tool names are kept in `seen`.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeOpenAI:
    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        self.seen: list[dict[str, Any]] = []
        self._parent = 0
        self._children: list[int] = []  # the next turn of each child
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["content-length"] or 0)))
                fake._answer(self, body)

            def do_GET(self) -> None:
                self._json(404, {"error": {"message": "not here"}})

            def _json(self, code: int, data: Any) -> None:
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeOpenAI":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()

    def _turn(self, body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        tools = [t["function"]["name"] for t in body.get("tools") or []]
        with self._lock:
            if "delegate_task" in tools:
                role, i = "parent", self._parent
                self._parent += 1
                turns = self.plan["parent"]
            elif {"create_charge", "list_charges", "get_invoice"} & set(tools):
                # Children run one at a time here: a request with no answer yet starts the next.
                if not any(m.get("role") == "assistant" for m in body["messages"]):
                    self._children.append(0)
                n = len(self._children) - 1
                role, i = f"child-{n + 1}", self._children[n]
                self._children[n] += 1
                turns = self.plan["children"][n]
            else:
                self.seen.append({"role": "aux", "tools": tools})
                return "aux", {"text": "ok"}
            self.seen.append(
                {
                    "role": role,
                    "model": body.get("model"),
                    "tools": sorted(tools),
                    "messages": body["messages"],
                }
            )
        return role, turns[i] if i < len(turns) else {"text": "Done."}

    def _answer(self, handler: Any, body: dict[str, Any]) -> None:
        role, turn = self._turn(body)
        message: dict[str, Any] = {"role": "assistant", "content": turn.get("text")}
        calls = [
            {
                "id": f"call_{role}_{n}_{len(self.seen)}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for n, (name, args) in enumerate(turn.get("calls") or [])
        ]
        if calls:
            message["tool_calls"] = calls
        finish = "tool_calls" if calls else "stop"
        usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
        if not body.get("stream"):
            handler._json(
                200,
                {
                    "id": "fake",
                    "object": "chat.completion",
                    "model": body.get("model"),
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": usage,
                },
            )
            return
        delta: dict[str, Any] = {"role": "assistant", "content": message["content"]}
        if calls:
            delta["tool_calls"] = [{"index": n, **c} for n, c in enumerate(calls)]
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            {"choices": [], "usage": usage},
        ]
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.end_headers()
        for chunk in chunks:
            data = {
                "id": "fake",
                "object": "chat.completion.chunk",
                "model": body.get("model"),
                **chunk,
            }
            handler.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
        handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()
