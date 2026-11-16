"""Remote view: a small web page that mirrors the screen and turns clicks into taps.

Open http://<pi-address>:<port>/ from a browser on the same network.  There is no login;
set [remote] enabled = false in the config to turn it off.  A click is delivered to the
UI as a touch at the same coordinates, so every on-screen button works.
"""
from __future__ import annotations

import http.server
import io
import json
import threading

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pingaroo Remote</title>
<style>
 body{margin:0;background:#0a0d14;color:#f2f4f8;font-family:system-ui,Segoe UI,Arial,sans-serif;text-align:center}
 h1{font-size:18px;margin:14px 0 6px}
 /* height:auto is required: the img's height attribute would otherwise pin the height
    while the width scales, stretching the picture. */
 #s{width:min(96vw,960px,calc((100vh - 110px) * __W__ / __H__));height:auto;aspect-ratio:__W__/__H__;
    border:1px solid #2c3648;border-radius:8px;cursor:pointer;image-rendering:auto;background:#000;
    touch-action:manipulation}
 #st{font-size:13px;color:#969eac;margin:8px}
 .ok{color:#48d26e}.bad{color:#f05448}
</style></head><body>
<h1>Pingaroo Remote</h1>
<img id="s" alt="Pingaroo Screen" width="__W__" height="__H__">
<div id="st">Connecting</div>
<script>
const W=__W__,H=__H__,img=document.getElementById('s'),st=document.getElementById('st');
let busy=false,last=0;
function tick(){
  if(busy)return; busy=true;
  const n=new Image();
  n.onload=()=>{img.src=n.src;last=Date.now();busy=false;st.innerHTML='<span class="ok">Live</span> \\u00b7 Click The Screen To Tap';};
  n.onerror=()=>{busy=false;st.innerHTML='<span class="bad">No Connection</span>'+(last?' \\u00b7 Last Update '+Math.round((Date.now()-last)/1000)+' s Ago':'');};
  n.src='/screen.png?'+Date.now();
}
img.addEventListener('click',e=>{
  const r=img.getBoundingClientRect();
  const x=Math.max(0,Math.min(W-1,Math.round((e.clientX-r.left)/r.width*W)));
  const y=Math.max(0,Math.min(H-1,Math.round((e.clientY-r.top)/r.height*H)));
  fetch('/tap',{method:'POST',headers:{'Content-Type':'application/json','X-Pingaroo':'1'},body:JSON.stringify({x:x,y:y})})
    .then(()=>setTimeout(tick,150)).catch(()=>{});
});
setInterval(tick,700); tick();
</script></body></html>
"""


class RemoteView:
    def __init__(self, bind: str, port: int, width: int, height: int, get_frame, send_tap) -> None:
        """get_frame() -> (sequence number, PIL image or None).  send_tap(x, y) queues a tap."""
        self.bind, self.port, self.w, self.h = bind, int(port), int(width), int(height)
        self.get_frame, self.send_tap = get_frame, send_tap
        self._srv: http.server.ThreadingHTTPServer | None = None
        self._lock = threading.Lock()
        self._png: tuple[int, bytes] = (-1, b"")
        self.taps = 0

    def _frame_png(self) -> bytes:
        seq, img = self.get_frame()
        if img is None:
            return b""
        with self._lock:
            if self._png[0] != seq:
                buf = io.BytesIO()
                img.save(buf, "PNG")
                self._png = (seq, buf.getvalue())
            return self._png[1]

    def start(self) -> str:
        """Start serving.  Returns "" or the reason it could not."""
        view = self

        class Handler(http.server.BaseHTTPRequestHandler):
            server_version = "Pingaroo"

            def log_message(self, *args) -> None:
                pass

            def _send(self, code: int, ctype: str = "text/plain", body: bytes = b"") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    page = PAGE.replace("__W__", str(view.w)).replace("__H__", str(view.h))
                    self._send(200, "text/html; charset=utf-8", page.encode())
                elif path == "/screen.png":
                    png = view._frame_png()
                    if png:
                        self._send(200, "image/png", png)
                    else:
                        self._send(503, body=b"no frame yet")
                else:
                    self._send(404, body=b"not found")

            def do_POST(self) -> None:
                if self.path.split("?", 1)[0] != "/tap":
                    self._send(404, body=b"not found")
                    return
                # A page on another site cannot set this header without a CORS preflight,
                # which is never answered, so only the remote page itself can tap.
                if self.headers.get("X-Pingaroo") != "1":
                    self._send(403, body=b"forbidden")
                    return
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    if not 0 < n <= 200:
                        raise ValueError("length")
                    data = json.loads(self.rfile.read(n).decode("utf-8"))
                    x, y = int(data["x"]), int(data["y"])
                    if not (0 <= x < view.w and 0 <= y < view.h):
                        raise ValueError("range")
                except (ValueError, KeyError, TypeError):
                    self._send(400, body=b"bad tap")
                    return
                view.taps += 1
                view.send_tap(x, y)
                self._send(204)

        try:
            self._srv = http.server.ThreadingHTTPServer((self.bind, self.port), Handler)
        except OSError as exc:
            return f"cannot listen on {self.bind}:{self.port}: {exc.strerror or exc}"
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, name="pingaroo-remote", daemon=True).start()
        return ""

    def stop(self) -> None:
        if self._srv is not None:
            try:
                self._srv.shutdown()
                self._srv.server_close()
            except Exception:
                pass
            self._srv = None
