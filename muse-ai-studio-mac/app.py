#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Muse AI Studio - ban viet lai chay tren macOS (va Linux/Windows voi Python).

Day KHONG phai ban dich nguoc file .exe goc, cung khong copy ma nguon
hay khoa cap phep cua ban goc. Day la ban viet moi, dua tren phan tich
hanh vi ben ngoai cua ban goc:
  - Giao dien web chay local, mo bang trinh duyet.
  - Dieu huong: Ban quyen, Tai khoan, Du an, Thu vien, Tao video
    Affiliate, Lam phim, Image-to-Video, Ghep video (ffmpeg), Cai dat.
  - Luong cap phep theo HUONG_DAN.txt cua ban goc: dang ky bang so
    dien thoai + mat khau de nhan dung thu, dang nhap de mo khoa tinh
    nang, nang cap Pro bang ma dang AIP-PRO-... hoac do admin nang cap.
  - Ban Mac nay chay cap phep LOCAL (khong noi toi Google Sheet cua
    ban goc, tru khi user tu cau hinh endpoint trong Cai dat). Khoa
    Pro o day chi duoc kiem tra dinh dang va luu local; khong co
    khoa that nao duoc nhung san trong code.

Chay tren MacBook:
    python3 app.py
  Sau do mo http://127.0.0.1:8765 tren trinh duyet (app se tu mo neu
  duoc). Ghep video can ffmpeg: brew install ffmpeg.

Chay tren Render (Docker, xem Dockerfile o goc repo):
  Render tu dat bien PORT (va RENDER=true); app bind 0.0.0.0 va khong
  tu mo trinh duyet. Co the ghi de dia chi bind bang bien HOST.
  Du lieu local (tai khoan/du an/task) luu trong container nen se mat
  khi service restart tren goi Free neu khong gan Disk tra phi.

Chi dung thu vien chuan cua Python, khong can pip install gi them.
Du lieu luu o ~/.muse-ai-studio-mac/ (config, tai khoan, du an, task).
Mat khau chi luu dang hash SHA-256 + salt, khong luu plaintext.
API key (neu user nhap trong Cai dat) chi luu local, khong gui di dau
tru khi user bam chay mot tac vu can toi API do.
"""

import hashlib
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "1.0.0-mac"
PORT = 8765
TRIAL_DAYS_DEFAULT = 7

DATA_DIR = Path.home() / ".muse-ai-studio-mac"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- state


def _load(name, default):
    p = DATA_DIR / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(name, obj):
    p = DATA_DIR / name
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _now():
    return int(time.time())


def _hash_pw(password, salt):
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


PRO_KEY_RE = re.compile(r"^AIP-PRO-[A-Z0-9]{4,}(-[A-Z0-9]{4,})*$")


class Store:
    """Kho du lieu local don gian, co khoa de tranh race giua cac request."""

    def __init__(self):
        self.lock = threading.Lock()
        self.settings = _load("settings.json", {
            "trial_days": TRIAL_DAYS_DEFAULT,
            "sheet_endpoint": "",
            "muse_api_key": "",
            "ffmpeg_path": shutil.which("ffmpeg") or "",
            "output_dir": str(Path.home() / "Muse AI Studio"),
        })
        self.accounts = _load("accounts.json", [])   # [{phone, salt, pw, created, trial_until, pro, pro_key}]
        self.session = _load("session.json", {"phone": None})
        self.projects = _load("projects.json", [])   # [{id, name, kind, created, notes}]
        self.tasks = _load("tasks.json", [])         # [{id, project_id, kind, prompt, status, created, result}]
        self._seq = _load("seq.json", {"n": 0})

    def persist(self):
        _save("settings.json", self.settings)
        _save("accounts.json", self.accounts)
        _save("session.json", self.session)
        _save("projects.json", self.projects)
        _save("tasks.json", self.tasks)
        _save("seq.json", self._seq)

    def next_id(self):
        self._seq["n"] += 1
        return self._seq["n"]

    # ------------------------------------------------------------ license
    def current_account(self):
        phone = self.session.get("phone")
        for acc in self.accounts:
            if acc["phone"] == phone:
                return acc
        return None

    def license_status(self):
        acc = self.current_account()
        if not acc:
            return {"logged_in": False, "plan": "locked",
                    "detail": "Chua dang nhap / Het han. Dang nhap tai khoan de su dung."}
        if acc.get("pro"):
            return {"logged_in": True, "plan": "pro", "phone": acc["phone"],
                    "detail": "Ban quyen PRO da kich hoat (local)."}
        trial_until = acc.get("trial_until") or 0
        if trial_until > _now():
            days = (trial_until - _now()) // 86400 + 1
            return {"logged_in": True, "plan": "trial", "phone": acc["phone"],
                    "trial_days_left": days,
                    "detail": f"Goi Mien phi (Dung thu, con khoang {days} ngay)."}
        return {"logged_in": True, "plan": "expired", "phone": acc["phone"],
                "detail": "Het han dung thu. Nhap ma Key Pro de nang cap."}

    def unlocked(self):
        return self.license_status()["plan"] in ("trial", "pro")


STORE = Store()

# ---------------------------------------------------------------- helpers


def send_json(handler, obj, status=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler):
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        length = 0
    raw = handler.rfile.read(length) if length > 0 else b""
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def ffmpeg_ok():
    path = STORE.settings.get("ffmpeg_path") or shutil.which("ffmpeg") or ""
    return bool(path and Path(path).exists()) if path else False

# ---------------------------------------------------------------- handler

UI_HTML = r"""<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Muse AI Studio 1.0.0 (ban Mac)</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0;background:#111827;color:#f3f4f6}
header{background:#1f2937;padding:14px 20px;display:flex;gap:12px;align-items:center}
header b{font-size:18px} header span{color:#9ca3af;font-size:13px}
nav{display:flex;gap:6px;padding:10px 20px;background:#1f2937;border-top:1px solid #374151;flex-wrap:wrap}
nav button{background:#374151;color:#fff;border:0;padding:8px 12px;border-radius:8px;cursor:pointer}
nav button.on{background:#ec4899}
main{padding:20px;max-width:900px}
section{display:none} section.on{display:block}
input,textarea,select{width:100%;padding:9px;margin:6px 0;border-radius:8px;border:1px solid #374151;background:#1f2937;color:#fff;box-sizing:border-box}
button.act{background:#ec4899;color:#fff;border:0;padding:10px 16px;border-radius:8px;cursor:pointer;margin-top:8px}
button.sec{background:#374151;color:#fff;border:0;padding:8px 12px;border-radius:8px;cursor:pointer}
.card{background:#1f2937;border:1px solid #374151;border-radius:12px;padding:14px;margin:12px 0}
.mut{color:#9ca3af;font-size:13px} .ok{color:#34d399} .err{color:#f87171}
table{width:100%;border-collapse:collapse} td,th{border-bottom:1px solid #374151;padding:8px;text-align:left;font-size:14px}
</style></head><body>
<header><b>🔥 Muse AI Studio</b><span>v1.0.0-mac &middot; ban viet lai chay tren MacBook</span><span id="lic" style="margin-left:auto"></span></header>
<nav id="nav">
<button data-s="license" class="on">Bản quyền</button><button data-s="accounts">Tài khoản</button>
<button data-s="projects">Dự án</button><button data-s="library">Thư viện</button>
<button data-s="aff">Tạo video Affiliate</button><button data-s="film">Làm phim</button>
<button data-s="i2v">Image-to-Video</button><button data-s="merge">Ghép video</button>
<button data-s="settings">Cài đặt</button></nav>
<main>
<section id="license" class="on"><h2>Bản quyền</h2><p id="licDetail" class="mut"></p>
<div class="card"><h3>Đăng ký mới (Nhận dùng thử)</h3>
<input id="regPhone" placeholder="Số điện thoại"><input id="regPw" type="password" placeholder="Mật khẩu">
<button class="act" onclick="register()">Đăng ký &amp; nhận dùng thử</button><p id="regMsg"></p></div>
<div class="card"><h3>Đăng nhập</h3>
<input id="logPhone" placeholder="Số điện thoại"><input id="logPw" type="password" placeholder="Mật khẩu">
<button class="act" onclick="login()">Đăng nhập</button>
<button class="sec" onclick="logout()">Đăng xuất</button><p id="logMsg"></p></div>
<div class="card"><h3>Nâng cấp Key Pro</h3>
<input id="proKey" placeholder="AIP-PRO-....">
<button class="act" onclick="activatePro()">Kích hoạt Pro</button><p id="proMsg" class="mut"></p>
<p class="mut">Ban Mac chay cap phep local. Neu ban co he thong Google Sheet rieng, nhap endpoint trong Cai dat; neu khong, ma Pro chi duoc kiem tra dinh dang va luu local.</p></div></section>
<section id="accounts"><h2>Tài khoản</h2><div id="accList"></div>
<p class="mut">Tai khoan o day la tai khoan cap phep local (so dien thoai). Tai khoan AI rieng (vi du Muse) nhap API key trong Cai dat.</p></section>
<section id="projects"><h2>Dự án</h2>
<div class="card"><input id="pjName" placeholder="Tên dự án">
<select id="pjKind"><option value="affiliate">Video Affiliate</option><option value="film">Phim lồng tiếng</option><option value="i2v">Image-to-Video</option></select>
<textarea id="pjNotes" placeholder="Ghi chú / kịch bản / prompt"></textarea>
<button class="act" onclick="createProject()">Tạo dự án</button></div><div id="pjList"></div></section>
<section id="library"><h2>Thư viện</h2><p class="mut">Thu muc output mac dinh: <span id="outDir"></span></p><div id="libList"></div>
<button class="sec" onclick="refreshAll()">Làm mới</button></section>
<section id="aff"><h2>Tạo video Affiliate</h2>
<p class="mut">Tao task video affiliate (anh san pham + caption + nhac). Backend AI can cau hinh API key trong Cai dat; neu chua cau hinh, task se luu lai o trang thai cho.</p>
<div class="card"><input id="afProduct" placeholder="Link / tên sản phẩm (vd: shopee.vn/...)"><textarea id="afPrompt" placeholder="Mô tả video muốn tạo"></textarea>
<button class="act" onclick="createTask('affiliate')">Tạo task</button></div><div id="taskList"></div></section>
<section id="film"><h2>Làm phim (lồng tiếng)</h2>
<div class="card"><textarea id="fmLines" placeholder="Mỗi dòng một câu thoại / câu thuyết minh"></textarea>
<button class="act" onclick="createTask('film')">Tạo task lồng tiếng</button></div><div id="taskList2"></div></section>
<section id="i2v"><h2>Image-to-Video</h2>
<div class="card"><input id="i2vImg" placeholder="Đường dẫn ảnh trên máy (vd: ~/Pictures/anh.jpg)"><textarea id="i2vPrompt" placeholder="Mô tả chuyển động"></textarea>
<button class="act" onclick="createTask('i2v')">Tạo task</button></div><div id="taskList3"></div></section>
<section id="merge"><h2>Ghép video bằng ffmpeg</h2><p id="ffInfo" class="mut"></p>
<div class="card"><textarea id="mgFiles" placeholder="Mỗi dòng một đường dẫn file video, theo thứ tự cần ghép"></textarea>
<input id="mgOut" placeholder="File output (vd: ~/Movies/ghep.mp4)">
<button class="act" onclick="merge()">Ghép ngay</button><p id="mgMsg"></p></div></section>
<section id="settings"><h2>Cài đặt</h2><div class="card">
<label class="mut">Số ngày dùng thử mặc định</label><input id="stTrial" type="number">
<label class="mut">Endpoint Google Sheet (tùy chọn, để trống = chỉ dùng local)</label><input id="stSheet" placeholder="https://...">
<label class="mut">API key Muse (chỉ lưu local, tùy chọn)</label><input id="stKey" type="password" placeholder="Để trống nếu chưa có">
<label class="mut">Đường dẫn ffmpeg</label><input id="stFfmpeg" placeholder="vd: /opt/homebrew/bin/ffmpeg">
<label class="mut">Thư mục output</label><input id="stOut">
<button class="act" onclick="saveSettings()">Lưu cài đặt</button><p id="stMsg"></p></div></section>
</main>
<script>
const $=s=>document.querySelector(s);
document.querySelectorAll('#nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('#nav button').forEach(x=>x.classList.remove('on')); b.classList.add('on');
  document.querySelectorAll('section').forEach(s=>s.classList.remove('on'));
  document.getElementById(b.dataset.s).classList.add('on'); refreshAll(); });
async function api(p,m,b){const r=await fetch(p,{method:m||'GET',headers:{'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});return r.json();}
async function refreshAll(){
  const st=await api('/api/state');
  $('#lic').textContent=st.license.plan.toUpperCase()+(st.license.phone?' · '+st.license.phone:'');
  $('#licDetail').textContent=st.license.detail;
  $('#ffInfo').textContent='ffmpeg: '+(st.ffmpeg_ok?'san sang':'chua thay — cai bang: brew install ffmpeg');
  $('#outDir').textContent=st.settings.output_dir;
  $('#accList').innerHTML='<table><tr><th>SĐT</th><th>Gói</th><th>Dùng thử đến</th></tr>'+st.accounts.map(a=>`<tr><td>${a.phone}</td><td>${a.pro?'PRO':'Dùng thử'}</td><td>${a.trial_until?new Date(a.trial_until*1000).toLocaleDateString('vi-VN'):'—'}</td></tr>`).join('')+'</table>';
  $('#pjList').innerHTML=st.projects.map(p=>`<div class="card"><b>${p.name}</b> <span class="mut">${p.kind} · ${new Date(p.created*1000).toLocaleString('vi-VN')}</span><br>${p.notes||''}</div>`).join('')||'<p class="mut">Chưa có dự án.</p>';
  const tl=st.tasks.map(t=>`<div class="card"><b>${t.kind}</b> · ${t.status}<br><span class="mut">${t.prompt||''}</span>${t.result?`<br>${t.result}`:''}</div>`).join('')||'<p class="mut">Chưa có task.</p>';
  ['#taskList','#taskList2','#taskList3'].forEach(id=>{const el=$(id); if(el) el.innerHTML=tl;});
  $('#libList').innerHTML=st.library.length?st.library.map(f=>`<div>${f}</div>`).join(''):'<p class="mut">Thư mục output chưa có file.</p>';
  $('#stTrial').value=st.settings.trial_days; $('#stSheet').value=st.settings.sheet_endpoint||'';
  $('#stFfmpeg').value=st.settings.ffmpeg_path||''; $('#stOut').value=st.settings.output_dir;
}
async function register(){const r=await api('/api/license/activate','POST',{mode:'register',phone:$('#regPhone').value.trim(),password:$('#regPw').value});$('#regMsg').textContent=r.message||'';$('#regMsg').className=r.ok?'ok':'err';refreshAll();}
async function login(){const r=await api('/api/license/activate','POST',{mode:'login',phone:$('#logPhone').value.trim(),password:$('#logPw').value});$('#logMsg').textContent=r.message||'';$('#logMsg').className=r.ok?'ok':'err';refreshAll();}
async function logout(){await api('/api/license/logout','POST',{});refreshAll();}
async function activatePro(){const r=await api('/api/license/activate','POST',{mode:'pro',key:$('#proKey').value.trim()});$('#proMsg').textContent=r.message||'';$('#proMsg').className=r.ok?'ok':'err';refreshAll();}
async function createProject(){const r=await api('/api/projects','POST',{name:$('#pjName').value.trim(),kind:$('#pjKind').value,notes:$('#pjNotes').value});if(!r.ok)alert(r.message);refreshAll();}
async function createTask(kind){let prompt='';
  if(kind==='affiliate')prompt='SP: '+$('#afProduct').value+' | '+$('#afPrompt').value;
  if(kind==='film')prompt=$('#fmLines').value;
  if(kind==='i2v')prompt='Anh: '+$('#i2vImg').value+' | '+$('#i2vPrompt').value;
  const r=await api('/api/tasks','POST',{kind,prompt});
  if(!r.ok)alert(r.message);refreshAll();}
async function merge(){const r=await api('/api/merge','POST',{files:$('#mgFiles').value.split('\n').map(s=>s.trim()).filter(Boolean),output:$('#mgOut').value.trim()});$('#mgMsg').textContent=r.message||'';$('#mgMsg').className=r.ok?'ok':'err';refreshAll();}
async function saveSettings(){const r=await api('/api/settings','POST',{trial_days:parseInt($('#stTrial').value||'7'),sheet_endpoint:$('#stSheet').value.trim(),muse_api_key:$('#stKey').value,ffmpeg_path:$('#stFfmpeg').value.trim(),output_dir:$('#stOut').value.trim()});$('#stMsg').textContent=r.ok?'Đã lưu.':'Lưu thất bại.';refreshAll();}
refreshAll();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # giu log sach, khong spam console
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/":
            body = UI_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/state":
            with STORE.lock:
                out_dir = Path(os.path.expanduser(STORE.settings["output_dir"]))
                library = []
                if out_dir.exists():
                    library = sorted(p.name for p in out_dir.iterdir() if p.is_file())[:100]
                send_json(self, {
                    "version": VERSION,
                    "license": STORE.license_status(),
                    "settings": {k: ("***" if k == "muse_api_key" and v else v)
                                 for k, v in STORE.settings.items()},
                    "accounts": [{"phone": a["phone"], "pro": bool(a.get("pro")),
                                  "trial_until": a.get("trial_until")} for a in STORE.accounts],
                    "projects": STORE.projects,
                    "tasks": STORE.tasks[-50:],
                    "library": library,
                    "ffmpeg_ok": ffmpeg_ok(),
                })
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        data = read_json(self)
        with STORE.lock:
            if path == "/api/license/activate":
                send_json(self, self._license_activate(data))
            elif path == "/api/license/logout":
                STORE.session["phone"] = None
                STORE.persist()
                send_json(self, {"ok": True, "message": "Da dang xuat."})
            elif path == "/api/settings":
                for key in ("trial_days", "sheet_endpoint", "ffmpeg_path", "output_dir"):
                    if key in data and data[key] != "":
                        STORE.settings[key] = data[key]
                if data.get("muse_api_key"):
                    STORE.settings["muse_api_key"] = data["muse_api_key"]
                STORE.persist()
                send_json(self, {"ok": True})
            elif path == "/api/projects":
                if not STORE.unlocked():
                    send_json(self, {"ok": False, "message": "Can dang nhap (ban dung thu hoac Pro) moi tao duoc du an."}, 403)
                elif not data.get("name"):
                    send_json(self, {"ok": False, "message": "Thieu ten du an."}, 400)
                else:
                    proj = {"id": STORE.next_id(), "name": data["name"], "kind": data.get("kind", "affiliate"),
                            "notes": data.get("notes", ""), "created": _now()}
                    STORE.projects.append(proj)
                    STORE.persist()
                    send_json(self, {"ok": True, "project": proj})
            elif path == "/api/tasks":
                if not STORE.unlocked():
                    send_json(self, {"ok": False, "message": "Can dang nhap (ban dung thu hoac Pro) moi tao duoc task."}, 403)
                elif not data.get("prompt"):
                    send_json(self, {"ok": False, "message": "Thieu noi dung task."}, 400)
                else:
                    task = {"id": STORE.next_id(), "kind": data.get("kind", "affiliate"),
                            "prompt": data["prompt"], "status": "cho",
                            "created": _now(), "result": ""}
                    if not STORE.settings.get("muse_api_key"):
                        task["result"] = ("Chua cau hinh API key trong Cai dat nen task moi chi duoc luu lai, "
                                          "chua chay tao video that.")
                    else:
                        task["result"] = ("Da luu task. Backend tao video theo API key da cau hinh can duoc "
                                          "gan them theo tung dich vu cu the (Muse API).")
                    STORE.tasks.append(task)
                    STORE.persist()
                    send_json(self, {"ok": True, "task": task})
            elif path == "/api/merge":
                send_json(self, self._merge(data))
            else:
                send_json(self, {"ok": False, "message": "Unknown endpoint."}, 404)

    # ------------------------------------------------------------ license
    def _license_activate(self, data):
        mode = data.get("mode")
        if mode == "register":
            phone, password = (data.get("phone") or "").strip(), data.get("password") or ""
            if not phone or len(password) < 4:
                return {"ok": False, "message": "Can so dien thoai va mat khau (toi thieu 4 ky tu)."}
            if any(a["phone"] == phone for a in STORE.accounts):
                return {"ok": False, "message": "So dien thoai nay da dang ky. Hay dang nhap."}
            salt = secrets.token_hex(8)
            trial_days = int(STORE.settings.get("trial_days") or TRIAL_DAYS_DEFAULT)
            acc = {"phone": phone, "salt": salt, "pw": _hash_pw(password, salt),
                   "created": _now(), "trial_until": _now() + trial_days * 86400,
                   "pro": False, "pro_key": ""}
            STORE.accounts.append(acc)
            STORE.session["phone"] = phone
            STORE.persist()
            return {"ok": True, "message": f"Dang ky thanh cong! Goi mien phi (dung thu {trial_days} ngay) da duoc kich hoat."}
        if mode == "login":
            phone, password = (data.get("phone") or "").strip(), data.get("password") or ""
            for acc in STORE.accounts:
                if acc["phone"] == phone and acc["pw"] == _hash_pw(password, acc["salt"]):
                    STORE.session["phone"] = phone
                    STORE.persist()
                    return {"ok": True, "message": "Dang nhap thanh cong!"}
            return {"ok": False, "message": "Sai so dien thoai hoac mat khau."}
        if mode == "pro":
            acc = STORE.current_account()
            if not acc:
                return {"ok": False, "message": "Can dang nhap truoc khi nang cap Pro."}
            key = (data.get("key") or "").strip().upper()
            if not PRO_KEY_RE.match(key):
                return {"ok": False, "message": "Ma Key Pro khong dung dinh dang AIP-PRO-...."}
            acc["pro"] = True
            acc["pro_key"] = key
            STORE.persist()
            return {"ok": True, "message": "Kich hoat Key Pro thanh cong (local)."}
        return {"ok": False, "message": "Mode khong hop le."}

    # ------------------------------------------------------------ merge
    def _merge(self, data):
        files = [os.path.expanduser(f) for f in (data.get("files") or [])]
        output = os.path.expanduser(data.get("output") or "")
        if len(files) < 2:
            return {"ok": False, "message": "Can it nhat 2 file video de ghep."}
        if not output:
            return {"ok": False, "message": "Thieu duong dan file output."}
        missing = [f for f in files if not Path(f).exists()]
        if missing:
            return {"ok": False, "message": "Khong thay file: " + ", ".join(missing)}
        ff = STORE.settings.get("ffmpeg_path") or shutil.which("ffmpeg") or ""
        if not ff or not Path(ff).exists():
            return {"ok": False, "message": "Chua thay ffmpeg. Tren Mac cai bang: brew install ffmpeg, roi nhap duong dan trong Cai dat."}
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        list_file = DATA_DIR / "concat.txt"
        list_file.write_text("".join(f"file '{f}'\n" for f in files), encoding="utf-8")
        cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
               "-c", "copy", str(out_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode == 0 and out_path.exists():
            size = out_path.stat().st_size
            size_txt = f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{size / 1024:.1f} KB"
            return {"ok": True, "message": f"Da ghep xong: {out_path} ({size_txt})."}
        return {"ok": False, "message": "ffmpeg bao loi: " + (proc.stderr or "")[-500:]}

# ---------------------------------------------------------------- main

def main():
    # Render (va moi truong container) dat bien PORT; chay local mac dinh 8765.
    # Bien RENDER=true do Render tu dat (Dockerfile cung dat) -> bind 0.0.0.0
    # va khong tu mo trinh duyet. Bien HOST cho phep ghi de dia chi bind.
    on_render = bool(os.environ.get("RENDER"))
    port = PORT
    if os.environ.get("PORT"):
        try:
            port = int(os.environ["PORT"])
        except ValueError:
            pass
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    host = os.environ.get("HOST", "0.0.0.0" if on_render else "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"Muse AI Studio (ban Mac) dang chay tai {url}")
    print(f"Du lieu local: {DATA_DIR}")
    print("Nhan Ctrl+C de dung.")
    if not on_render:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDa dung.")
        server.shutdown()


if __name__ == "__main__":
    main()
