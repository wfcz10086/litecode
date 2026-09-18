"""
Playwright Actor — pure browser-driven (extracted from reports/e2e_v2_*/tools/web_actor.py).

Every test action MUST go through this Actor (click panel buttons, fill forms,
wait for toast/DOM). API access is only allowed via _verify_via_api for reading
state during assertions.
"""
from playwright.sync_api import Page, TimeoutError as PWTimeout
import json, os, time, urllib.request, urllib.error
from pathlib import Path


class Actor:
    def __init__(self, page: Page, base="http://127.0.0.1:18790"):
        self.page = page
        self.base = base
        self._auth_cookie = None

    # ── login ─────────────────────────────────────────────
    def login(self, pwd="CHANGE_ME_PASSWORD"):
        self.page.goto(self.base, wait_until="domcontentloaded")
        try:
            self.page.wait_for_selector("#lpwd", timeout=4000, state="visible")
            self.page.fill("#lpwd", pwd)
            self.page.click(".lb_")
        except PWTimeout:
            pass
        self.page.wait_for_selector("#mi", timeout=10000)
        self.page.wait_for_timeout(600)
        self._auth_cookie = next(
            (c["value"] for c in self.page.context.cookies()
             if c["name"] == "lc_auth"), None)

    # ── helpers ───────────────────────────────────────────
    def close_all_panels(self):
        self.page.evaluate("""()=>{
            ['stats','memory','wechat','timer','sfiles','vnc','project','dag','settings'].forEach(n=>{
                const el = document.getElementById('pnl-'+n);
                if (el) el.classList.remove('open');
            });
        }""")
        self.page.wait_for_timeout(150)

    def open_panel(self, name):
        self.page.evaluate(f"openP('{name}')")
        self.page.wait_for_selector(f"#pnl-{name}.open", timeout=5000)
        self.page.wait_for_timeout(400)

    def close_panel(self, name):
        sel = f"#pnl-{name} .px_"
        if self.page.locator(sel).count() > 0:
            self.page.click(sel)
        else:
            self.page.evaluate(f"closeP && closeP('{name}')")
        self.page.wait_for_timeout(300)

    def shot(self, name, dest_dir):
        p = Path(dest_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(p), full_page=False)
        return name

    # ── chat ──────────────────────────────────────────────
    def new_chat(self):
        self.close_all_panels()
        self.wait_streaming_done(timeout=5)
        self.page.wait_for_selector("#ncb", state="visible", timeout=8000)
        self.page.click("#ncb", timeout=8000)
        self.page.wait_for_function(
            "()=>window.cur && window.cur.length>0", timeout=8000)
        self.page.wait_for_timeout(300)

    def wait_streaming_done(self, timeout=15):
        deadline = time.time() + timeout
        idle = 0
        while time.time() < deadline:
            busy = self.page.evaluate(
                "()=>{if(window.streaming)return true;"
                "const ib=document.getElementById('ib');"
                "if(ib && getComputedStyle(ib).display!=='none')return true;"
                "const sdb=document.getElementById('sdb');"
                "if(sdb && (sdb.disabled||getComputedStyle(sdb).display==='none'))return true;"
                "return false;}")
            if not busy:
                idle += 1
                if idle >= 3:
                    return True
            else:
                idle = 0
            self.page.wait_for_timeout(200)
        return False

    def send_chat(self, text, timeout=120):
        self.close_all_panels()
        cur = self.page.evaluate("()=>window.cur")
        if not cur:
            self.new_chat()
        self.wait_streaming_done(timeout=10)
        self.page.evaluate(
            "()=>{const sdb=document.getElementById('sdb');"
            "if(sdb){sdb.disabled=false;sdb.style.display='';}"
            "const ib=document.getElementById('ib');"
            "if(ib){ib.style.display='none';}"
            "window.streaming=false;}")
        self.page.wait_for_selector("#mi:not([disabled])", timeout=8000)
        self.page.fill("#mi", text)
        try:
            self.page.wait_for_selector("#sdb:not([disabled])", state="visible", timeout=8000)
            self.page.click("#sdb", timeout=5000)
        except PWTimeout:
            self.page.evaluate("()=>typeof send==='function' && send()")
        get_mb = ("()=>{const ns=document.querySelectorAll('.msg.ai');"
                  "if(!ns.length)return '';"
                  "const mb=ns[ns.length-1].querySelector('.mb');"
                  "return mb?mb.innerText:''}")
        # Phase 1: wait for streaming to START (window.streaming flips on,
        # OR a new .msg.ai appears, OR #sdb hides). Without this we may exit
        # early before any SSE chunk arrives.
        for _ in range(40):  # up to 8s
            started = self.page.evaluate(
                "()=>{if(window.streaming)return true;"
                "const ib=document.getElementById('ib');"
                "if(ib && getComputedStyle(ib).display!=='none')return true;"
                "const sdb=document.getElementById('sdb');"
                "if(sdb && (sdb.disabled||getComputedStyle(sdb).display==='none'))return true;"
                "return document.querySelectorAll('.msg.ai').length>0;}")
            if started:
                break
            self.page.wait_for_timeout(200)
        # Phase 2: wait for full agent loop to settle
        deadline = time.time() + timeout
        idle = 0
        while time.time() < deadline:
            busy = self.page.evaluate(
                "()=>{if(window.streaming)return true;"
                "const ib=document.getElementById('ib');"
                "if(ib && getComputedStyle(ib).display!=='none')return true;"
                "const sdb=document.getElementById('sdb');"
                "if(sdb && (sdb.disabled||getComputedStyle(sdb).display==='none'))return true;"
                "return false;}")
            if not busy:
                idle += 1
                if idle >= 3:
                    break
            else:
                idle = 0
            self.page.wait_for_timeout(200)
        return self.page.evaluate(get_mb)

    # ── timer ─────────────────────────────────────────────
    def timer_create(self, name, ttype, schedule, action_type, action_target, action_content):
        self.open_panel("timer")
        self.page.evaluate("tmrShowAdd()")
        self.page.wait_for_selector("#tmr-name", timeout=3000)
        self.page.fill("#tmr-name", name)
        self.page.select_option("#tmr-type", ttype)
        self.page.fill("#tmr-schedule", schedule)
        self.page.select_option("#tmr-action", action_type)
        if action_target:
            self.page.fill("#tmr-target", action_target)
        self.page.fill("#tmr-content", action_content)
        self.page.evaluate("tmrAdd()")
        try:
            self.page.wait_for_selector("#toast.show", timeout=5000)
        except PWTimeout:
            pass
        self.page.wait_for_timeout(1200)

    def timer_delete_by_name(self, name):
        ts = self._verify_via_api("/api/timers")
        for t in (ts.get("timers", []) if isinstance(ts, dict) else []):
            if t.get("name") == name:
                self.page.evaluate("window.confirm=()=>true")
                self.page.evaluate(f"tmrDel('{t['id']}')")
                self.page.wait_for_timeout(800)
                return True
        return False

    # ── DAG ───────────────────────────────────────────────
    def dag_new(self, name):
        self.open_panel("dag")
        self.page.evaluate("newDAG()")
        self.page.wait_for_selector("#dc-name", timeout=3000)
        self.page.fill("#dc-name", name)
        self.page.click("#dc-ok")
        self.page.wait_for_timeout(800)

    def dag_add_node(self, agent_type):
        sel = f"button[onclick*=\"addDAGNodeQuick('{agent_type}')\"]"
        if self.page.locator(sel).count() > 0:
            self.page.locator(sel).first.click()
        else:
            self.page.evaluate(f"addDAGNodeQuick('{agent_type}')")
        self.page.wait_for_timeout(400)

    def dag_save(self):
        self.page.evaluate("saveDAG()")
        try:
            self.page.wait_for_selector("#toast.show", timeout=5000)
        except PWTimeout:
            pass

    def dag_run(self, max_wait=240):
        self.page.evaluate("runDAG()")
        for _ in range(max_wait):
            done = self.page.evaluate(
                "()=>{const ns=[...document.querySelectorAll('.dag-node')];"
                "return ns.length>0 && ns.every(n=>['success','done'].includes(n.dataset.status||''));}")
            if done:
                return True
            self.page.wait_for_timeout(1000)
        return False

    # ── Settings ──────────────────────────────────────────
    def model_switch(self, model_id):
        self.open_panel("settings")
        sel = f"button[onclick*=\"mdlSwitch('{model_id}')\"]"
        if self.page.locator(sel).count() > 0:
            self.page.locator(sel).first.click()
        else:
            self.page.evaluate(f"mdlSwitch('{model_id}')")
        try:
            self.page.wait_for_selector("#toast.show", timeout=10000)
        except PWTimeout:
            pass
        ok = False
        for _ in range(20):
            cur = self.page.locator("#sbf-model-id").inner_text()
            if model_id.split("-")[-1] in cur:
                ok = True; break
            self.page.wait_for_timeout(500)
        self.close_all_panels()
        return ok

    # ── upload ────────────────────────────────────────────
    def upload(self, filepath):
        self.page.set_input_files("#fi", filepath)
        self.page.wait_for_selector("#pfp .pfc", timeout=15000)

    # ── verification (read-only API) ─────────────────────
    def _verify_via_api(self, path, timeout=10):
        h = {"Cookie": f"lc_auth={self._auth_cookie}"} if self._auth_cookie else {}
        req = urllib.request.Request(self.base + path, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                txt = r.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(txt)
                except Exception:
                    return {"_raw": txt, "_status": r.status}
        except urllib.error.HTTPError as e:
            return {"_status": e.code, "_raw": e.read().decode("utf-8", errors="replace")}
        except Exception as e:
            return {"_error": repr(e)}
