"""In-process fake of Ollama's /v1/systemone endpoint (stdlib only)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

DEFAULT_ANSWERS = {"fake": {"choice": "ok", "probabilities": {"ok": 1.0}}}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):  # keep test output quiet
        pass

    def do_POST(self):
        fake = self.server.fake
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {"_unparsable": raw.decode("utf-8", "replace")}
        with fake._lock:
            fake._requests.append(body)
            cfg = fake._snapshot_config()

        if cfg["delay"]:
            fake._stop.wait(cfg["delay"])

        if cfg["status"] is not None:
            body_override = cfg["status_body"]
            self._send(cfg["status"], b'{"error": "forced"}' if body_override is None else body_override)
            return
        if cfg["raw_body"] is not None:
            self._send(200, cfg["raw_body"])
            return

        model = body.get("model") if isinstance(body, dict) else None
        if cfg["divergent"]:
            payload: Dict[str, Any] = {"model": model, "unexpected": True}
        elif model in cfg["responses"]:
            payload = cfg["responses"][model]
        else:
            payload = {
                "model": model,
                "answers": DEFAULT_ANSWERS,
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        self._send(200, json.dumps(payload).encode("utf-8"))

    def _send(self, status: int, data: bytes):
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client gave up (timeout tests)


class FakeOllama:
    """Context manager; configure per test via attributes/`respond`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self.responses: Dict[str, Dict[str, Any]] = {}
        self.status: Optional[int] = None
        self.delay: float = 0.0
        self.raw_body: Optional[bytes] = None
        self.status_body: Optional[bytes] = None  # body sent with `status`; None = default JSON
        self.divergent: bool = False
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def _snapshot_config(self) -> Dict[str, Any]:
        return {
            "responses": dict(self.responses),
            "status": self.status,
            "delay": self.delay,
            "raw_body": self.raw_body,
            "status_body": self.status_body,
            "divergent": self.divergent,
        }

    def respond(self, model: str, body: Dict[str, Any]) -> None:
        self.responses[model] = body

    @property
    def requests(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._requests)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d/v1/systemone" % self.port

    def start(self) -> "FakeOllama":
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.fake = self
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()  # release any sleeping handler
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "FakeOllama":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
