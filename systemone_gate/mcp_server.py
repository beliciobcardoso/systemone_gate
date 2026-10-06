"""
Model Context Protocol (MCP) Server for SystemOne Gate.
Zero external dependencies (pure standard library).
Compatible with Claude Desktop, Cursor, Antigravity, Cline, Windsurf, Roo Code.
"""

import json
import subprocess
import sys
import traceback
from typing import Any, Dict

from . import __version__
from .client import SystemOneClient
from .diff_review import review_staged
from .policy import PolicyConfig, evaluate_diff

GIT_DIFF_TIMEOUT_SECONDS = 30
NOTHING_STAGED_NOTE = "no staged changes"
PREFER_STAGED_HINT = (
    " If the change is already staged in git, prefer systemone_review_staged: "
    "it reads the diff on the server and avoids passing it through the agent."
)

MCP_TOOLS = [
    {
        "name": "systemone_triage_error",
        "description": (
            "Triage and classify build, linker, runtime errors, or test failures using local Ollama "
            "Nimble (9B) decision model." + PREFER_STAGED_HINT
        ),
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
        "description": (
            "Evaluates architectural risk, breaking changes, and critical failure modes in a code patch "
            "or git diff using Nimble (9B)." + PREFER_STAGED_HINT
        ),
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
        "name": "systemone_review_staged",
        "description": (
            "Reviews the changes currently staged in git (git diff --cached, read by the server in its "
            "working directory) per file, skipping lockfiles/binaries, and returns risk, breaking-change "
            "answers, coverage and the allow/block policy decision. Preferred over "
            "systemone_review_diff: the diff never passes through the agent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Model to use ('nimble' or 'tev1:0.8b'). Defaults to 'nimble'.",
                    "default": "nimble"
                }
            }
        }
    },
    {
        "name": "systemone_command_guard",
        "description": (
            "Low-latency local safety check (deterministic rules first, then tev1:0.8b) before executing "
            "potentially risky shell/bash commands."
        ),
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
    result: Dict[str, Any] = {
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

def _staged_diff() -> Dict[str, Any]:
    """Runs `git diff --cached` in the current directory. Returns {"diff"} or {"error"}."""
    try:
        proc = subprocess.run(["git", "diff", "--cached"], capture_output=True, text=True,
                              errors="replace", timeout=GIT_DIFF_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return {"error": f"git diff --cached timed out after {GIT_DIFF_TIMEOUT_SECONDS}s"}
    except FileNotFoundError:
        return {"error": "git not found on the MCP server PATH"}
    except OSError:
        return {"error": "failed to run git on the MCP server"}
    if proc.returncode != 0:
        return {"error": "the MCP server working directory is not a git repository "
                         "or 'git diff --cached' failed"}
    return {"diff": proc.stdout}


def _review_staged_tool(client: Any, tool_args: Dict[str, Any]) -> Dict[str, Any]:
    staged = _staged_diff()
    if "error" in staged:
        return staged
    if not staged["diff"].strip():
        return {"answers": {},
                "coverage": {"reviewed": [], "skipped": [], "truncated": []},
                "note": NOTHING_STAGED_NOTE}
    try:
        cfg = PolicyConfig.from_env()
    except ValueError as exc:
        return {"error": f"Invalid policy configuration: {exc}"}
    res = review_staged(client, staged["diff"], tool_args.get("model"))
    if "error" in res:
        return res
    decision = evaluate_diff(res, cfg)
    return {**res, "decision": {"action": decision.action,
                                "reasons": list(decision.reasons),
                                "warning": decision.warning}}


def _dispatch_tool(client: Any, tool_name: Any, tool_args: Dict[str, Any]) -> Dict[str, Any]:
    """Run a tool and build the tools/call result. Never raises."""
    try:
        if tool_name == "systemone_triage_error":
            res = client.triage_error(tool_args.get("error_log", ""), model=tool_args.get("model"))
        elif tool_name == "systemone_review_diff":
            res = client.review_diff(tool_args.get("diff", ""), model=tool_args.get("model"))
        elif tool_name == "systemone_review_staged":
            res = _review_staged_tool(client, tool_args)
        elif tool_name == "systemone_command_guard":
            res = client.guard_command(tool_args.get("command", ""))
        elif tool_name == "systemone_query":
            res = client.evaluate(
                state=tool_args.get("state", ""),
                questions=tool_args.get("questions", {}),
                model=tool_args.get("model")
            )
        else:
            res = {"error": f"Tool '{tool_name}' not found"}
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
                        "version": __version__
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
