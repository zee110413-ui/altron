"""A stand-in for the AI server for the field test's hands part on GitHub Actions: the hands are driven straight through
the brain's console, so the model is not needed — the brain only has to see an AI that answers. It answers every
question with one short phrase, the way llama-server's OpenAI-compatible API does (plain or streamed).

    python ci/fake_llm.py <port>
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = "Принял."


class Handler(BaseHTTPRequestHandler):
    def _json(self, data, code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json({"status": "ok"})
        elif self.path.rstrip("/").endswith("/models"):
            self._json({"data": [{"id": "fake"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        usage = {"prompt_tokens": 100, "completion_tokens": 2}
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in ({"choices": [{"delta": {"content": ANSWER}}]}, {"choices": [{"delta": {}}], "usage": usage}):
                self.wfile.write(("data: %s\n\n" % json.dumps(chunk, ensure_ascii=False)).encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json({"choices": [{"message": {"role": "assistant", "content": ANSWER}}], "usage": usage})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
