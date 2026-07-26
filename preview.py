# -*- coding: utf-8 -*-
"""瞎掰王 · 題庫預覽 / 審題（純預覽，看不到答案）。

在終端機執行：
    python3 preview.py       （或 uv run preview.py）
然後用瀏覽器開啟畫面顯示的網址。

畫面只顯示「瞎掰人看得到的卡片」——題目、難度、提示，看不到背面答案。
按鈕：
  下一題 →      切換到下一題
  ✕ 棄用       審題時把這題標記為棄用（會寫進「棄用清單.txt」），並自動跳下一題
  ↩ 取消棄用    若翻回已棄用的題，可取消標記
棄用結果存在同目錄的「棄用清單.txt」，可直接依它從 cards.py 移除題目。
"""

import json
import os
import random
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cards import CARDS

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8100"))
DISCARD_FILE = os.path.join(HERE, "棄用清單.txt")

# 只保留「瞎掰人看得到」的欄位：id 供標記用，絕不含 description / real_hint / source
SAFE = [
    {
        "id": c["id"],
        "topic": c["topic"],
        "difficulty": c["difficulty"],
        "hints": c.get("hints", []),
        "category": c.get("category", ""),
    }
    for c in CARDS
]
random.shuffle(SAFE)

_by_id = {c["id"]: c for c in CARDS}
_discarded = set()


def load_discarded():
    if not os.path.exists(DISCARD_FILE):
        return
    with open(DISCARD_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cid = line.split("\t")[0].split()[0]
            if cid in _by_id:
                _discarded.add(cid)


def save_discarded():
    lines = [
        "# 瞎掰王 棄用清單（審題標記，可依此從 cards.py 移除題目）",
        "# 共 %d 題" % len(_discarded),
        "# 欄位：id\\t題目\\t難度\\t類別",
        "",
    ]
    for c in CARDS:  # 依原順序輸出，方便對照
        if c["id"] in _discarded:
            lines.append(
                "%s\t%s\t%s\t%s"
                % (c["id"], c["topic"], "★" * c["difficulty"], c["category"])
            )
    with open(DISCARD_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


load_discarded()

PAGE = """<!DOCTYPE html>
<html lang="zh-Hant"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no"/>
<title>瞎掰王 · 題庫預覽 / 審題</title>
<style>
:root{--ink:#4A3B2A;--soft:#8A7658;--card:#FFFDF5;--accent:#FF9E6D;--accent2:#FFC94D;--bad:#F08A8A;--shadow:4px 4px 0 rgba(74,59,42,.18);}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
html,body{margin:0;background:#FFF7DC;color:var(--ink);
 font-family:"Chalkboard SE","Comic Sans MS","PingFang TC","Heiti TC","Noto Sans TC",system-ui,sans-serif;}
body{min-height:100vh;background-image:
 radial-gradient(circle at 12% 18%,rgba(255,201,77,.18)0,transparent 22%),
 radial-gradient(circle at 88% 8%,rgba(255,158,109,.16)0,transparent 20%);}
#wrap{max-width:520px;margin:0 auto;padding:16px 16px 40px;min-height:100vh;display:flex;flex-direction:column;}
.top{text-align:center;color:var(--soft);font-size:14px;margin:4px 0 2px;}
.bar{display:flex;justify-content:space-between;align-items:center;color:var(--soft);font-size:15px;margin-bottom:8px;}
.bar b{color:var(--bad);}
.gamecard{position:relative;background:var(--card);border:3px solid var(--ink);border-radius:26px;
 box-shadow:var(--shadow);padding:30px 20px;text-align:center;margin:6px 0 16px;}
.gamecard.dead{opacity:.55;}
.ribbon{position:absolute;top:-14px;left:50%;transform:translateX(-50%);background:var(--bad);color:#fff;
 border:3px solid var(--ink);border-radius:999px;padding:3px 16px;font-weight:800;font-size:14px;display:none;}
.gamecard.dead .ribbon{display:block;}
.cat{position:absolute;top:12px;left:16px;font-size:13px;color:var(--soft);border:2px solid var(--soft);border-radius:999px;padding:2px 10px;}
.did{position:absolute;bottom:10px;right:16px;font-size:11px;color:var(--soft);}
.diff{position:absolute;top:14px;right:16px;font-size:15px;color:var(--soft);}
.label{color:var(--soft);font-size:14px;letter-spacing:4px;margin-top:14px;}
.topic{font-size:50px;font-weight:800;margin:10px 0;letter-spacing:4px;line-height:1.15;}
.hints{margin-top:16px;padding-top:14px;border-top:2px dashed var(--soft);}
.hints .h-title{font-size:13px;color:var(--soft);margin-bottom:8px;}
.chip{display:inline-block;background:#FFF3D6;border:2px solid var(--ink);border-radius:999px;padding:6px 14px;margin:4px;font-size:17px;font-weight:700;}
.nohint{color:var(--soft);font-size:15px;margin-top:6px;}
.btnrow{display:flex;gap:12px;margin-top:auto;}
.btn{border:3px solid var(--ink);border-radius:999px;font-family:inherit;font-weight:800;padding:16px;
 cursor:pointer;box-shadow:var(--shadow);transition:transform .06s;}
.btn:active{transform:translate(3px,3px);box-shadow:1px 1px 0 rgba(74,59,42,.18);}
.btn.next{flex:2;background:var(--accent2);color:var(--ink);font-size:21px;}
.btn.kill{flex:1;background:#fff;color:var(--bad);font-size:17px;}
.btn.kill.on{background:var(--bad);color:#fff;}
.foot{text-align:center;color:var(--soft);font-size:12px;margin-top:12px;}
</style></head>
<body><div id="wrap">
 <div class="top">👀 純預覽 / 審題模式（看不到答案）</div>
 <div class="bar"><span id="count"></span><span>已棄用 <b id="dcount">0</b> 題</span></div>
 <div class="gamecard" id="card">
   <div class="ribbon">✕ 已棄用</div>
   <div class="cat" id="cat"></div>
   <div class="diff" id="diff"></div>
   <div class="label">題　目</div>
   <div class="topic" id="topic"></div>
   <div class="hints" id="hints"></div>
   <div class="did" id="cid"></div>
 </div>
 <div class="btnrow">
   <button class="btn kill" id="kill"></button>
   <button class="btn next" id="next">下一題 →</button>
 </div>
 <div class="foot">共 __N__ 題 · 隨機順序 · 棄用結果存於「棄用清單.txt」</div>
</div>
<script>
var CARDS = __DATA__;
var discarded = new Set(__DISCARDED__);
var i = 0;
function stars(n){return "★".repeat(n)+"☆".repeat(3-n);}
function render(){
  var c = CARDS[i], dead = discarded.has(c.id);
  document.getElementById("count").textContent = (i+1)+" / "+CARDS.length;
  document.getElementById("dcount").textContent = discarded.size;
  document.getElementById("cat").textContent = c.category || "";
  document.getElementById("diff").textContent = "難度 "+stars(c.difficulty);
  document.getElementById("topic").textContent = c.topic;
  document.getElementById("cid").textContent = c.id;
  var h = document.getElementById("hints");
  if(c.hints && c.hints.length){
    h.innerHTML = '<div class="h-title">提示</div>' +
      c.hints.map(function(x){return '<span class="chip">'+x+'</span>';}).join("");
  }else{
    h.innerHTML = '<div class="nohint">（這題沒有提示）</div>';
  }
  document.getElementById("card").className = "gamecard" + (dead ? " dead" : "");
  var kb = document.getElementById("kill");
  kb.textContent = dead ? "↩ 取消棄用" : "✕ 棄用";
  kb.className = "btn kill" + (dead ? " on" : "");
}
function advance(){ i = (i+1) % CARDS.length; render(); }
document.getElementById("next").onclick = advance;
document.getElementById("kill").onclick = function(){
  var c = CARDS[i], wasDead = discarded.has(c.id);
  fetch("/api/toggle", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({id: c.id})})
    .then(function(r){return r.json();})
    .then(function(j){
      if(j.discarded){ discarded.add(c.id); } else { discarded.delete(c.id); }
      document.getElementById("dcount").textContent = j.count;
      if(!wasDead){ advance(); }   // 剛棄用 → 自動跳下一題；取消棄用 → 留在原地
      else { render(); }
    });
};
render();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self.send_response(404)
            self.end_headers()
            return
        html = (
            PAGE.replace("__DATA__", json.dumps(SAFE, ensure_ascii=False))
            .replace("__DISCARDED__", json.dumps(sorted(_discarded)))
            .replace("__N__", str(len(SAFE)))
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?")[0] != "/api/toggle":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            data = {}
        cid = data.get("id")
        discarded_now = False
        if cid in _by_id:
            if cid in _discarded:
                _discarded.discard(cid)
            else:
                _discarded.add(cid)
                discarded_now = True
            save_discarded()
        self._json({"ok": True, "discarded": discarded_now, "count": len(_discarded)})


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if not (ip.startswith("198.18.") or ip.startswith("169.254.")):
            return ip
    except Exception:
        pass
    return None


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    ip = lan_ip()
    print("=" * 48)
    print("  瞎掰王 · 題庫預覽 / 審題（純預覽，無答案）")
    print(f"  題數：{len(SAFE)}　已棄用：{len(_discarded)}")
    print(f"  本機：      http://localhost:{PORT}")
    if ip:
        print(f"  手機（同網）：http://{ip}:{PORT}")
    print(f"  棄用清單：  {DISCARD_FILE}")
    print("  按 Ctrl+C 結束")
    print("=" * 48)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n再見！")


if __name__ == "__main__":
    main()
