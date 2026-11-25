"""Watch an MCP conversation byte by byte: a scripted "agent" talks raw JSON-RPC to agent/server.py.

    uv run python agent/trace_demo.py          # whole conversation
    uv run python agent/trace_demo.py --step   # press Enter before each message

Blue = what the agent sends (stdin of the server), green = what the server answers (its stdout),
grey = where the server is in its own code (stderr, enabled by LEDGERLY_TRACE=1).
The "agent" here is a script, not an LLM: its "thinking" lines show what a model would decide at that point.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from mcp.types import LATEST_PROTOCOL_VERSION

SERVER = Path(__file__).resolve().parent / "server.py"
BLUE, GREEN, GREY, BOLD, RESET = "\033[34m", "\033[32m", "\033[90m", "\033[1m", "\033[0m"
MAX_LINES = 30

SCENARIO = [
    ("Question: what was MRR of Polish customers at the end of Q1 2025?\n"
     "First the client and the server shake hands and agree on the protocol version.",
     "initialize", {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
                    "clientInfo": {"name": "trace-demo", "version": "0.1"}}),
    ("The client confirms the handshake. A notification has no id, so the server sends no answer.",
     "notifications/initialized", None),
    ("Which tools does this server have? The descriptions and parameter schemas come from server.py.",
     "tools/list", {}),
    ("The instructions said: call list_metrics first. The descriptions come from semantic_models/*.yml.",
     "tools/call", {"name": "list_metrics", "arguments": {}}),
    ("'mrr' fits the question. I'll guess the country dimension is called customer__country...",
     "tools/call", {"name": "query_metric", "arguments": {
         "metrics": ["mrr"], "grain": "quarter", "start": "2025-01-01", "end": "2025-03-31",
         "filters": {"customer__country": ["PL"]}}}),
    ("The error lists the valid names: it is customer__country_code. Retry.",
     "tools/call", {"name": "query_metric", "arguments": {
         "metrics": ["mrr"], "grain": "quarter", "start": "2025-01-01", "end": "2025-03-31",
         "filters": {"customer__country_code": ["PL"]}}}),
]


def show(color: str, label: str, message: dict) -> None:
    lines = json.dumps(message, indent=2, ensure_ascii=False).splitlines()
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES] + [f"  ... ({len(lines) - MAX_LINES} more lines)"]
    print(f"{color}{BOLD}{label}{RESET}")
    print(color + "\n".join(lines) + RESET, flush=True)


def pipe_stderr(stream) -> None:
    for line in stream:
        print(f"{GREY}{line.rstrip()}{RESET}", flush=True)


def main() -> None:
    step = "--step" in sys.argv
    env = {**os.environ, "LEDGERLY_TRACE": "1"}
    proc = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)
    threading.Thread(target=pipe_stderr, args=(proc.stderr,), daemon=True).start()
    print(f"{BOLD}started: python {SERVER.name}  (stdin/stdout = MCP channel, stderr = trace){RESET}\n")

    for request_id, (thinking, method, params) in enumerate(SCENARIO, start=1):
        print(f"\n{BOLD}── agent thinks: {thinking}{RESET}")
        if step:
            input("   [Enter]")
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        is_notification = method.startswith("notifications/")
        if not is_notification:
            message["id"] = request_id
        show(BLUE, "agent → server (stdin)", message)
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        if is_notification:
            continue
        while True:  # skip any server-initiated notifications until our answer arrives
            response = json.loads(proc.stdout.readline())
            if response.get("id") == request_id:
                break
        time.sleep(0.3)  # let the trace lines for this request print first
        show(GREEN, "server → agent (stdout)", response)

    proc.stdin.close()
    proc.wait(timeout=10)


if __name__ == "__main__":
    main()
