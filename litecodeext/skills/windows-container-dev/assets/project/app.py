"""用于 Windows + Dev Container + VS Code 断点验收的本地 HTTP 服务。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import parse_qs, urlsplit


def add(a, b):
    result = a + b
    return result


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == '/health':
            body = {'ok': True, 'greeting': 'hello LiteCode'}
        elif parsed.path == '/add':
            values = parse_qs(parsed.query)
            try:
                a, b = int(values['a'][0]), int(values['b'][0])
            except (ValueError, KeyError):
                self.send_error(400, 'a and b must be integers')
                return
            result = add(a, b)
            body = {'a': a, 'b': b, 'result': result}
        else:
            self.send_error(404)
            return
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == '__main__':
    print('LiteCode development check: http://localhost:8080', flush=True)
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
