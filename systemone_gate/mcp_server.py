"""
Model Context Protocol (MCP) Server for SystemOne Gate.
Zero external dependencies (pure standard library).
Compatible with Claude Desktop, Cursor, Antigravity, Cline, Windsurf, Roo Code.
"""

import sys
import json
import traceback
from typing import Dict, Any

from .client import SystemOneClient
from .rubrics import (
    RUBRIC_DIFF_RISK,
    RUBRIC_ERROR_TRIAGE,
    RUBRIC_COMMAND_SAFETY,
    RUBRIC_AGENT_ROUTING,
)

MCP_TOOLS = [
    {
        "name": "systemone_triage_error",
        "description": "Triage and classify build, linker, runtime errors, or test failures using local Ollama Nimble (9B) decision model.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "error_log": {
                    "type": "string",
                    "description": "The compiler error, stack trace, or test failure output to evaluate."
                },
                "model": {
                    "type": "string",
                    "description": "Decision model to use ('nimble' or 'tev1:0.8b'). Defaults to 'nimble'.",
                    "default": "nimble"
                }
            },
            "required": ["error_log"]
        }
    },
    {
        "name": "systemone_review_diff",
        "description": "Evaluates architectural risk, breaking changes, and critical failure modes in a code patch or git diff using Nimble (9B).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "diff": {
                    "type": "string",
                    "description": "The git diff or code changes to evaluate."
                },
                "model": {
                    "type": "string",
                    "description": "Model to use ('nimble' or 'tev1:0.8b'). Defaults to 'nimble'.",
                    "default": "nimble"
                }
            },
            "required": ["diff"]
        }
    },
    {
        "name": "systemone_command_guard",
        "description": "Ultra-fast safety check (<15ms via tev1:0.8b) before executing potentially risky shell/bash commands.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash or CLI command to inspect."
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "systemone_query",
        "description": "Perform any custom choice or score evaluation against a state using Ollama System One.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "description": "Context or input text to evaluate."
                },
                "questions": {
                    "type": "object",
                    "description": "Dictionary of questions according to Ollama /v1/systemone schema (choice or score)."
                },
                "model": {
                    "type": "string",
                    "description": "Ollama model ('nimble' or 'tev1:0.8b'). Defaults to 'nimble'.",
                    "default": "nimble"
                }
            },
            "required": ["state", "questions"]
        }
    }
]

def send_jsonrpc(obj: Dict[str, Any]):
    line = json.dumps(obj)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()

def _text_result(payload: Dict[str, Any], is_error: bool = False) -> Dict[str, Any]:
    # A tool payload carrying an "error" key is a failure, not an answer.
    if isinstance(payload, dict) and "error" in payload:
        is_error = True
    result = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, indent=2, ensure_ascii=False)
            }
        ]
    }
    if is_error:
        result["isError"] = True
    return result

def _dispatch_tool(client: Any, tool_name: Any, tool_args: Dict[str, Any]) -> Dict[str, Any]:
    """Run a tool and build the tools/call result. Never raises."""
    try:
        if tool_name == "systemone_triage_error":
            res = client.triage_error(tool_args.get("error_log", ""), model=tool_args.get("model"))
        elif tool_name == "systemone_review_diff":
            res = client.review_diff(tool_args.get("diff", ""), model=tool_args.get("model"))
        elif tool_name == "systemone_command_guard":
            res = client.guard_command(tool_args.get("command", ""))
        elif tool_name == "systemone_query":
            res = client.evaluate(
                state=tool_args.get("state", ""),
                questions=tool_args.get("questions", {}),
                model=tool_args.get("model")
            )
        else:
            res = {"error": f"Tool '{tool_name}' não encontrada"}
        return _text_result(res)
    except Exception:
        # stdout is the protocol channel: log details to stderr only.
        traceback.print_exc(file=sys.stderr)
        return _text_result({"error": "Internal error while executing tool"}, is_error=True)

def run_mcp_server():
    client = SystemOneClient()

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except Exception:
            # Never echo the offending text; keep details minimal on stderr.
            print("mcp: ignoring invalid JSON line (parse error)", file=sys.stderr)
            send_jsonrpc({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"}
            })
            continue

        if not isinstance(req, dict):
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params")
        if not isinstance(params, dict):
            params = {}

        if method == "initialize":
            send_jsonrpc({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "systemone-gate",
                        "version": "0.1.0"
                    }
                }
            })
        elif method == "notifications/initialized":
            pass
        elif method == "ping":
            send_jsonrpc({"jsonrpc": "2.0", "id": req_id, "result": {}})
        elif method == "tools/list":
            send_jsonrpc({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": MCP_TOOLS
                }
            })
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments")
            if tool_args is None:
                tool_args = {}

            if not isinstance(tool_args, dict):
                send_jsonrpc({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid params: 'arguments' must be an object"
                    }
                })
                continue

            send_jsonrpc({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": _dispatch_tool(client, tool_name, tool_args)
            })
        else:
            if req_id is not None:
                send_jsonrpc({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                })

if __name__ == "__main__":
    run_mcp_server()
