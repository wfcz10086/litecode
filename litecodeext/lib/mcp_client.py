"""
mcp_client.py — MCP 客户端最小骨架 (P13)
=========================================
极速版: 支持通过 stdio 与 MCP server 通信 (JSON-RPC 2.0).
后续可加 HTTP/SSE transport 和完整工具发现.

设计思路:
  1. spawn subprocess: mcp server 启动命令 (e.g. "npx mcp-server-filesystem /path")
  2. JSON-RPC over stdin/stdout: line-delimited
  3. 暴露: list_tools / call_tool 两个核心方法
  4. 失败容错: 超时 / 进程死亡 → 返回 error dict, 不抛

API:
  client = MCPClient(["npx", "mcp-server-filesystem", "/path"])
  client.start()
  tools = client.list_tools()  # [{"name", "description", "schema"}]
  result = client.call_tool("read_file", {"path": "/x"})
  client.stop()

不实现完整 MCP 规范, 只够 LiteCode 调最常见的 MCP server.
"""
import json
import subprocess
import threading
import time
import queue
from typing import List, Dict, Optional, Any


class MCPClient:
    """stdio JSON-RPC MCP 客户端"""

    def __init__(self, command: List[str], timeout: float = 30.0):
        self.command = command
        self.timeout = timeout
        self.proc: Optional[subprocess.Popen] = None
        self._req_id = 0
        self._lock = threading.Lock()
        self._stderr_buf: List[str] = []
        self._reader_thread: Optional[threading.Thread] = None
        self._response_queue: queue.Queue = queue.Queue()
        self._running = False

    def start(self) -> bool:
        if self.proc is not None:
            return True
        try:
            self.proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            self._running = True
            # reader thread: 持续读 stdout 入 queue
            self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._reader_thread.start()
            # initialize handshake (MCP 标准)
            init_resp = self._rpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "litecode", "version": "1.4"},
            })
            return init_resp is not None and "error" not in init_resp
        except Exception as e:
            self._stderr_buf.append(f"start error: {e}")
            return False

    def _reader_loop(self):
        if not self.proc or not self.proc.stdout:
            return
        try:
            for line in self.proc.stdout:
                if not self._running:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    self._response_queue.put(obj)
                except Exception:
                    pass
        except Exception:
            pass

    def _next_id(self) -> int:
        with self._lock:
            self._req_id += 1
            return self._req_id

    def _rpc(self, method: str, params: Optional[Dict] = None) -> Optional[Dict]:
        if not self.proc or not self.proc.stdin:
            return None
        rid = self._next_id()
        req = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params:
            req["params"] = params
        try:
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
        except Exception as e:
            return {"error": {"message": f"write fail: {e}"}}
        # 等响应 (匹配 id)
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                resp = self._response_queue.get(timeout=0.2)
            except queue.Empty:
                if self.proc.poll() is not None:
                    return {"error": {"message": "subprocess died"}}
                continue
            if resp.get("id") == rid:
                return resp
            # 不匹配的, 丢回队列? (简单实现: 丢弃 notification, 留 id 响应)
            if "id" in resp:
                self._response_queue.put(resp)
        return {"error": {"message": "timeout"}}

    def list_tools(self) -> List[Dict]:
        resp = self._rpc("tools/list", {})
        if not resp or "error" in resp:
            return []
        return resp.get("result", {}).get("tools", []) or []

    def call_tool(self, name: str, arguments: Optional[Dict] = None) -> Dict:
        # [v1.8 P36-b] MCP 埋点
        import time as _t
        _t0 = _t.time()
        resp = self._rpc("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        _latency = int((_t.time() - _t0) * 1000)
        try:
            from core.telemetry import emit as _emit
            if not resp:
                _emit(event="mcp_no_response",
                      fields={"tool_name": name, "server": getattr(self, "name", "?")},
                      jsonl="mcp.jsonl", latency_ms=_latency)
                return {"error": "no response"}
            if "error" in resp:
                _emit(event="mcp_error",
                      fields={"tool_name": name, "server": getattr(self, "name", "?"),
                              "error": str(resp.get("error", ""))[:200]},
                      jsonl="mcp.jsonl", latency_ms=_latency)
                return {"error": resp["error"]}
            _emit(event="mcp_call_ok",
                  fields={"tool_name": name, "server": getattr(self, "name", "?"),
                          "result_keys": sorted(list((resp.get("result") or {}).keys()))[:10]},
                  jsonl="mcp.jsonl", latency_ms=_latency)
        except Exception:
            pass
        if not resp:
            return {"error": "no response"}
        if "error" in resp:
            return {"error": resp["error"]}
        return resp.get("result", {}) or {}

    def stop(self):
        self._running = False
        if self.proc:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            except Exception:
                pass
            self.proc = None
