"""
app.py - CHATHUB (single-file, full-featured, deploy-ready)

Create requirements.txt with these lines and run:
pip install -r requirements.txt

requirements.txt:
Flask==3.1.2
Flask-SocketIO==5.5.1
gunicorn==23.0.0
eventlet==0.36.1
Pillow==10.0.0

Procfile (for Render):
web: gunicorn -w 1 -k eventlet -b 0.0.0.0:$PORT app:app
"""

import os
import sqlite3
import time
import base64
from functools import wraps
from flask import Flask, request, redirect, url_for, render_template_string, session, flash, send_file, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit, join_room, leave_room

# ------------- Config -------------
APP_PORT = int(os.environ.get("PORT", 5000))
DB_FILE = "chathub_complete.sqlite"
SECRET = os.environ.get("CHATHUB_SECRET", "chathub_secret_for_prod")
UPLOAD_LIMIT = 2 * 1024 * 1024  # 2 MB
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET
app.config["MAX_CONTENT_LENGTH"] = UPLOAD_LIMIT
app.config["UPLOAD_FOLDER"] = "uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# Use eventlet to support WebSockets in production (gunicorn + eventlet recommended)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# ------------- DB helpers (each call opens/closes a connection) -------------
def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT,
        avatar TEXT,
        theme TEXT DEFAULT 'dark'
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room TEXT,
        sender TEXT,
        receiver TEXT,
        message TEXT,
        image TEXT,
        reply_to INTEGER,
        timestamp INTEGER
    );
    CREATE TABLE IF NOT EXISTS reactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER,
        username TEXT,
        emoji TEXT
    );
    CREATE TABLE IF NOT EXISTS pins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER,
        room TEXT
    );
    """)
    conn.commit()
    conn.close()

init_db()

# ------------- Utility functions -------------
def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def file_to_dataurl(storage_file):
    raw = storage_file.read()
    mime = storage_file.mimetype or "image/png"
    return f"data:{mime};base64," + base64.b64encode(raw).decode("utf-8")

# ------------- User / profile helpers -------------
def create_user(username, password_plain, avatar_dataurl=None):
    hashed = generate_password_hash(password_plain)
    conn = get_conn()
    conn.execute("INSERT INTO users (username,password,avatar) VALUES (?, ?, ?)", (username, hashed, avatar_dataurl))
    conn.commit()
    conn.close()

def get_user(username):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None

def set_avatar(username, dataurl):
    conn = get_conn()
    conn.execute("UPDATE users SET avatar=? WHERE username=?", (dataurl, username))
    conn.commit()
    conn.close()

def get_theme(username):
    row = get_user(username)
    return row["theme"] if row and "theme" in row else "dark"

def set_theme(username, theme):
    conn = get_conn()
    conn.execute("UPDATE users SET theme=? WHERE username=?", (theme, username))
    conn.commit()
    conn.close()

# ------------- Message helpers / persistence -------------
def persist_message(room, sender, receiver, message_text, image_dataurl=None, reply_to=None):
    ts = int(time.time())
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO messages (room, sender, receiver, message, image, reply_to, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (room, sender, receiver, message_text, image_dataurl, reply_to, ts)
    )
    conn.commit()
    mid = cur.lastrowid
    row = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    conn.close()
    return dict(row) if row else None

def load_room_messages(room, limit=500):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM messages WHERE room=? AND receiver IS NULL ORDER BY id ASC LIMIT ?", (room, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def load_dm_history(user, other, limit=500):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE ((sender=? AND receiver=?) OR (sender=? AND receiver=?)) ORDER BY id ASC LIMIT ?",
        (user, other, other, user, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def edit_message(mid, username, new_text):
    conn = get_conn()
    row = conn.execute("SELECT sender FROM messages WHERE id=?", (mid,)).fetchone()
    if not row or row["sender"] != username:
        conn.close()
        return None
    conn.execute("UPDATE messages SET message=? WHERE id=?", (new_text, mid))
    conn.commit()
    updated = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    conn.close()
    return dict(updated) if updated else None

def delete_message(mid, username):
    conn = get_conn()
    row = conn.execute("SELECT sender FROM messages WHERE id=?", (mid,)).fetchone()
    if not row or row["sender"] != username:
        conn.close()
        return False
    conn.execute("DELETE FROM messages WHERE id=?", (mid,))
    conn.execute("DELETE FROM reactions WHERE message_id=?", (mid,))
    conn.commit()
    conn.close()
    return True

def toggle_reaction(mid, username, emoji):
    conn = get_conn()
    existing = conn.execute("SELECT id FROM reactions WHERE message_id=? AND username=? AND emoji=?", (mid, username, emoji)).fetchone()
    if existing:
        conn.execute("DELETE FROM reactions WHERE id=?", (existing["id"],))
        conn.commit()
        conn.close()
        return False
    conn.execute("INSERT INTO reactions (message_id, username, emoji) VALUES (?, ?, ?)", (mid, username, emoji))
    conn.commit()
    conn.close()
    return True

def reactions_summary(mid):
    conn = get_conn()
    rows = conn.execute("SELECT emoji, COUNT(*) as cnt FROM reactions WHERE message_id=? GROUP BY emoji", (mid,)).fetchall()
    conn.close()
    return {r["emoji"]: r["cnt"] for r in rows}

def pin_message(mid, room):
    conn = get_conn()
    conn.execute("INSERT INTO pins (message_id, room) VALUES (?, ?)", (mid, room))
    conn.commit()
    row = conn.execute("SELECT m.* FROM messages m WHERE m.id=?", (mid,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_pinned(room):
    conn = get_conn()
    rows = conn.execute("SELECT m.* FROM messages m JOIN pins p ON m.id=p.message_id WHERE p.room=? ORDER BY p.id DESC LIMIT 50", (room,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def search_room(room, q):
    qlike = f"%{q}%"
    conn = get_conn()
    rows = conn.execute("SELECT * FROM messages WHERE room=? AND message LIKE ? ORDER BY id DESC LIMIT 200", (room, qlike)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ------------- In-memory presence maps -------------
user_to_sid = {}
sid_to_user = {}

# ------------- Routes (auth, avatar, theme, pages) -------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","")
        if not username or not password:
            flash("Choose username and password")
            return redirect(url_for("register"))
        if get_user(username):
            flash("Username already taken")
            return redirect(url_for("register"))
        avatar_dataurl = None
        f = request.files.get("avatar")
        if f and allowed_file(f.filename):
            avatar_dataurl = file_to_dataurl(f)
        create_user(username, password, avatar_dataurl)
        session["user"] = username
        return redirect(url_for("chat"))
    return render_template_string(REG_HTML)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","")
        u = get_user(username)
        if not u or not check_password_hash(u["password"], password):
            flash("Invalid credentials")
            return redirect(url_for("login"))
        session["user"] = username
        return redirect(url_for("chat"))
    return render_template_string(LOGIN_HTML)

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))

@app.route("/")
def index():
    if "user" in session:
        return redirect(url_for("chat"))
    return redirect(url_for("login"))

@app.route("/chat")
@login_required
def chat():
    username = session["user"]
    theme = get_theme(username)
    return render_template_string(MAIN_HTML, username=username, theme=theme)

@app.route("/upload_avatar", methods=["POST"])
@login_required
def upload_avatar():
    f = request.files.get("avatar")
    if not f or not allowed_file(f.filename):
        flash("No avatar or invalid type")
        return redirect(url_for("chat"))
    dataurl = file_to_dataurl(f)
    set_avatar(session["user"], dataurl)
    flash("Avatar updated")
    return redirect(url_for("chat"))

@app.route("/set_theme", methods=["POST"])
@login_required
def set_theme_route():
    t = request.form.get("theme", "dark")
    set_theme(session["user"], t)
    return redirect(url_for("chat"))

# ------------- Inline templates -------------
REG_HTML = r"""
<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Register — CHATHUB</title></head>
<body style="font-family:Arial, sans-serif;padding:18px;">
  <h2>Register — CHATHUB</h2>
  {% with messages = get_flashed_messages() %}
    {% if messages %}<ul>{% for m in messages %}<li style="color:red">{{m}}</li>{% endfor %}</ul>{% endif %}
  {% endwith %}
  <form method="post" enctype="multipart/form-data">
    <input name="username" placeholder="username" required><br><br>
    <input name="password" type="password" placeholder="password" required><br><br>
    <label>Upload avatar (optional)</label><br>
    <input type="file" name="avatar" accept="image/*"><br><br>
    <button type="submit">Register & Join</button>
  </form>
  <p>Already registered? <a href="{{ url_for('login') }}">Login</a></p>
</body></html>
"""

LOGIN_HTML = r"""
<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Login — CHATHUB</title></head>
<body style="font-family:Arial, sans-serif;padding:18px;">
  <h2>Login — CHATHUB</h2>
  {% with messages = get_flashed_messages() %}
    {% if messages %}<ul>{% for m in messages %}<li style="color:red">{{m}}</li>{% endfor %}</ul>{% endif %}
  {% endwith %}
  <form method="post">
    <input name="username" placeholder="username" required><br><br>
    <input name="password" type="password" placeholder="password" required><br><br>
    <button type="submit">Login</button>
  </form>
  <p>New? <a href="{{ url_for('register') }}">Register</a></p>
</body></html>
"""

MAIN_HTML = r"""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CHATHUB</title>
<style>
:root{ --bg-dark:#0f1720; --bg-green:#063; --bg-white:#fff; --text-dark:#e7eef8; --text-light:#111; --me:#00c3ff; --other:#263044; }
html,body{height:100%;margin:0;font-family:Inter,Arial,sans-serif;}
body{ background: {{ 'var(--bg-dark)' if theme=='dark' else ('var(--bg-green)' if theme=='green' else 'var(--bg-white)') }}; color: {{ 'var(--text-dark)' if theme!='white' else 'var(--text-light)' }}; }
.app{ max-width:980px;margin:0 auto;padding:12px;box-sizing:border-box; }
.header{ display:flex; align-items:center; gap:12px; }
.brand{ font-weight:800; font-size:20px; }
.controls{ margin-left:auto; display:flex; gap:8px; align-items:center; }
input, button, select, textarea{ padding:8px; border-radius:8px; border:1px solid rgba(255,255,255,0.06); background:transparent; color:inherit; }
button{ cursor:pointer; background:var(--me); color:#000; border:none; font-weight:700; }
.layout{ display:flex; gap:12px; margin-top:12px; flex-wrap:wrap; }
.left{ width:100%; max-width:300px; background:rgba(255,255,255,0.03); padding:10px; border-radius:8px; box-sizing:border-box; }
.main{ flex:1; display:flex; flex-direction:column; min-width:280px; }
.users-list{ max-height:220px; overflow:auto; }
.user-row{ display:flex; gap:8px; align-items:center; padding:6px; border-radius:6px; cursor:pointer; }
.user-row:hover{ background:rgba(255,255,255,0.02); }
.avatar{ width:42px; height:42px; border-radius:50%; object-fit:cover; }
.rooms{ margin-top:10px; }
.messages{ flex:1; overflow:auto; padding:12px; border-radius:8px; background:rgba(0,0,0,0.06); max-height:60vh; }
.msg{ display:flex; gap:8px; margin:6px 0; align-items:flex-start; max-width:85%; }
.msg.me{ margin-left:auto; background:var(--me); color:#000; padding:8px; border-radius:12px; }
.msg.other{ margin-right:auto; background:var(--other); color:inherit; padding:8px; border-radius:12px; }
.msg .meta{ font-size:0.8em; margin-bottom:6px; color:rgba(255,255,255,0.9); }
.controls-row{ display:flex; gap:8px; align-items:center; margin-top:8px; }
.small{ font-size:0.85em; color:rgba(255,255,255,0.75); }
.img-preview{ max-width:220px; border-radius:8px; margin-top:6px; }
.reaction-btn{ cursor:pointer; margin-right:6px; }
.reply-box{ border-left:2px solid rgba(255,255,255,0.08); padding-left:6px; margin-bottom:6px; color:rgba(255,255,255,0.85); }
.search{ margin-top:10px; }
@media (max-width:820px){ .left{ max-width:100%; } .layout{ flex-direction:column; } .messages{ max-height:50vh; } }
</style>
</head>
<body>
<div class="app">
  <div class="header">
    <div class="brand">CHATHUB</div>
    <div class="small">You: <b>{{ username }}</b></div>
    <div class="controls">
      <form id="avatarForm" action="{{ url_for('upload_avatar') }}" method="post" enctype="multipart/form-data" style="display:inline;">
        <input type="file" name="avatar" accept="image/*" onchange="document.getElementById('avatarForm').submit();" />
      </form>
      <form id="themeForm" action="{{ url_for('set_theme_route') }}" method="post" style="display:inline;">
        <select name="theme" onchange="document.getElementById('themeForm').submit();">
          <option value="dark" {% if theme=='dark' %}selected{% endif %}>Dark</option>
          <option value="green" {% if theme=='green' %}selected{% endif %}>Green</option>
          <option value="white" {% if theme=='white' %}selected{% endif %}>White</option>
        </select>
      </form>
      <a href="{{ url_for('logout') }}"><button>Logout</button></a>
    </div>
  </div>

  <div class="layout">
    <div class="left">
      <div style="font-weight:700;margin-bottom:8px;">Online Users</div>
      <div id="usersList" class="users-list"></div>

      <div style="font-weight:700;margin-top:10px;margin-bottom:6px;">Rooms</div>
      <div id="roomsList" class="rooms"></div>
      <div style="margin-top:8px;">
        <input id="newRoom" placeholder="New room name" />
        <button onclick="createRoom()">Create</button>
      </div>

      <div class="search">
        <input id="searchInput" placeholder="Search messages..." />
        <button onclick="doSearch()">Search</button>
      </div>

      <div style="margin-top:10px;">
        <div style="font-weight:700;">Pinned</div>
        <div id="pinnedList"></div>
      </div>
    </div>

    <div class="main">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div>Viewing: <b id="viewLabel">Lobby</b></div>
        <div class="small" id="typingIndicator"></div>
      </div>

      <div id="messages" class="messages"></div>

      <div class="controls-row">
        <input id="toInput" placeholder="DM to (optional username)" />
        <input id="messageInput" placeholder="Type a message..." style="flex:1" oninput="typing()" />
        <input id="imageInput" type="file" accept="image/*" />
        <button onclick="sendMessage()">Send</button>
      </div>
    </div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.min.js"></script>
<script>
const socket = io();
const user = {{ username | tojson }};
let currentRoom = "Lobby";
let mode = "room";
let dmWith = null;
let typingTimeout = null;
let replyToId = null;

function defaultIdent(name){
  const initials = (name||"?").split(" ").map(s=>s[0]).join("").substring(0,2).toUpperCase();
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64'><rect width='100%' height='100%' fill='#888'/><text x='50%' y='55%' font-size='28' text-anchor='middle' fill='white' font-family='Arial' dy='.3em'>${initials}</text></svg>`;
  return "data:image/svg+xml;base64,"+btoa(svg);
}

function escapeHtml(s){ if(!s) return ""; return s.replace(/[&<>"'`]/g, m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;','`':'&#96;'}[m])); }
function mentionHighlight(text){ return text.replace(/@(\w+)/g, "<span style='color:lightgreen'>@$1</span>"); }

socket.on('connect', ()=> { socket.emit('fetch_initial'); });

socket.on('initial', (data) => {
  renderUsers(data.users);
  renderRooms(data.rooms);
  switchToRoom("Lobby");
});

function renderUsers(users){
  const el = document.getElementById("usersList"); el.innerHTML="";
  users.forEach(u=>{
    const avatar = u.avatar || defaultIdent(u.username);
    const d = document.createElement("div");
    d.className="user-row";
    d.innerHTML = `<img class="avatar" src="${avatar}"><div><b>${escapeHtml(u.username)}</b><div class="small">click to DM</div></div>`;
    d.onclick = ()=> openDM(u.username);
    el.appendChild(d);
  });
}

function renderRooms(rooms){
  const el = document.getElementById("roomsList"); el.innerHTML="";
  rooms.forEach(r=>{
    const d = document.createElement("div");
    d.style.padding="6px"; d.style.cursor="pointer"; d.style.borderRadius="6px";
    d.innerText = r;
    d.onclick = ()=> switchToRoom(r);
    el.appendChild(d);
  });
}

function switchToRoom(r){
  mode="room"; dmWith=null; currentRoom=r;
  document.getElementById("viewLabel").innerText = r;
  socket.emit("join_room", {username:user, room:r});
  loadRoomMessages(r);
  loadPinned(r);
}

function createRoom(){ const name=document.getElementById("newRoom").value.trim(); if(!name) return; switchToRoom(name); }

function loadRoomMessages(room){
  socket.emit("load_room_messages", {room});
}

socket.on("load_room_messages", (msgs)=>{ clearMessages(); msgs.forEach(renderMessage); });
socket.on("new_message_room", (m)=>{ if(mode==="room" && m.room===currentRoom) renderMessage(m); });
socket.on("new_message_dm", (m)=>{ if(mode==="dm" && ((m.sender===dmWith && m.receiver===user)||(m.sender===user && m.receiver===dmWith))) renderMessage(m); else if(m.receiver===user){ notify("New DM from "+m.sender); } });

socket.on("user_list", (users)=> renderUsers(users));
socket.on("rooms_list", (rooms)=> renderRooms(rooms));
socket.on("update_message", (m)=>{ const el=document.querySelector("[data-id='"+m.id+"']"); if(el){ el.querySelector('.content').innerHTML = mentionHighlight(escapeHtml(m.message)); }});
socket.on("delete_message", (info)=>{ const el=document.querySelector("[data-id='"+info.id+"']"); if(el) el.remove();});
socket.on("reactions_update", (d)=>{ const el=document.getElementById("reactions-"+d.message_id); if(el){ el.innerHTML = Object.entries(d.reactions).map(([e,c])=>`${e} ${c}`).join(" "); }});
socket.on("pinned_list", (list)=>{ const el=document.getElementById("pinnedList"); el.innerHTML=''; list.forEach(m=>{ const d=document.createElement('div'); d.className='small'; d.textContent = `#${m.id} ${m.sender}: ${m.message}`; el.appendChild(d); }); });

function renderMessage(m){
  const wrap = document.getElementById("messages");
  const div = document.createElement("div");
  div.className = "msg " + (m.sender===user ? "me":"other");
  div.dataset.id = m.id;
  const avatar = m.avatar || defaultIdent(m.sender);
  const t = new Date((m.timestamp||0)*1000);
  const timeText = isNaN(t.getTime()) ? "" : t.toLocaleTimeString();
  const replyPart = m.reply_to ? `<div class='reply-box'>Reply to #${m.reply_to}</div>` : "";
  const contentHtml = `<div class='meta'><b>${escapeHtml(m.sender)}</b> <span class='small'>${timeText}</span></div>${replyPart}<div class='content'>${mentionHighlight(escapeHtml(m.message||""))}</div>`;
  let inner = `<img class='avatar' src='${avatar}'> <div style='display:flex;flex-direction:column;'>${contentHtml}`;
  if(m.image) inner += `<img class='img-preview' src='${m.image}'>`;
  inner += `<div class='reactions' id='reactions-${m.id}'></div>`;
  inner += `<div class='controls'><span class='small'>`;
  if(m.sender===user) inner += `<a href='javascript:editMsg(${m.id})'>✏️</a> <a href='javascript:delMsg(${m.id})'>🗑️</a> `;
  inner += `<a href='javascript:replyTo(${m.id})'>↩️</a> <a href='javascript:pinMsg(${m.id})'>📌</a> <a href='javascript:react(${m.id},"👍")'>👍</a> <a href='javascript:react(${m.id},"❤️")'>❤️</a>`;
  inner += `</span></div></div>`;
  div.innerHTML = inner;
  wrap.appendChild(div);
  wrap.scrollTop = wrap.scrollHeight;
  socket.emit("request_reactions", {message_id: m.id});
}

function clearMessages(){ document.getElementById("messages").innerHTML = ""; }

function sendMessage(){
  const text = document.getElementById("messageInput").value.trim();
  const f = document.getElementById("imageInput").files[0];
  const to = document.getElementById("toInput").value.trim();
  const reply_to = replyToId || null;
  if(to){
    if(f){ const r=new FileReader(); r.onload=()=>{ socket.emit("send_dm", {username:user, to:to, message:text, image:r.result, reply_to:reply_to}); clearCompose(); }; r.readAsDataURL(f); }
    else { socket.emit("send_dm", {username:user, to:to, message:text, image:null, reply_to:reply_to}); clearCompose(); }
  } else {
    if(!currentRoom) currentRoom="Lobby";
    if(f){ const r=new FileReader(); r.onload=()=>{ socket.emit("send_message", {username:user, room:currentRoom, message:text, image:r.result, reply_to:reply_to}); clearCompose(); }; r.readAsDataURL(f); }
    else { socket.emit("send_message", {username:user, room:currentRoom, message:text, image:null, reply_to:reply_to}); clearCompose(); }
  }
}

function clearCompose(){ document.getElementById("messageInput").value=''; document.getElementById("imageInput").value=''; replyToId=null; const rb=document.getElementById('replyBox'); if(rb) rb.remove(); }

function replyTo(id){ replyToId = id; const rb=document.createElement('div'); rb.id='replyBox'; rb.className='reply-box'; rb.textContent = 'Replying to #' + id; document.querySelector('.main').prepend(rb); }

function editMsg(id){ const t = prompt('Edit message:'); if(t===null) return; socket.emit('edit_message', {id:id, message:t}); }
function delMsg(id){ if(!confirm('Delete?')) return; socket.emit('delete_message', {id:id}); }
function pinMsg(id){ socket.emit('pin_message', {id:id, room:currentRoom}); }
function react(id, emoji){ socket.emit('react', {message_id:id, username:user, emoji:emoji}); }

socket.on('reactions_update', (d)=>{ const el=document.getElementById('reactions-'+d.message_id); if(el){ el.innerHTML = Object.entries(d.reactions).map(([e,c])=>`${e} ${c}`).join(' '); } });

function openDM(other){ mode='dm'; dmWith=other; document.getElementById('viewLabel').innerText = 'DM: '+other; socket.emit('load_dm',{username:user, other:other}); clearMessages(); }

socket.on('load_dm_history', (hist)=>{ clearMessages(); hist.forEach(renderMessage); });

function doSearch(){ const q=document.getElementById('searchInput').value.trim(); if(!q) return; socket.emit('search', {room: currentRoom, query: q}); }
socket.on('search_results', (data)=>{ clearMessages(); data.results.forEach(renderMessage); });

function typing(){ socket.emit('typing', {username:user, room:currentRoom}); if(typingTimeout) clearTimeout(typingTimeout); typingTimeout = setTimeout(()=>socket.emit('stop_typing', {username:user, room:currentRoom}), 1500); }
socket.on('typing', (d)=>{ document.getElementById('typingIndicator').innerText = d.username+' typing...'; });
socket.on('stop_typing', ()=>{ document.getElementById('typingIndicator').innerText = ''; });

function loadPinned(r){ socket.emit('get_pinned', {room: r || currentRoom}); }
socket.on('pinned_list', (list)=>{ const el=document.getElementById('pinnedList'); el.innerHTML=''; list.forEach(m=>{ const d=document.createElement('div'); d.className='small'; d.textContent = `#${m.id} ${m.sender}: ${m.message}`; el.appendChild(d); }); });

socket.on('connect', ()=> { socket.emit('presence', {username:user}); });

function notify(txt){ try{ if(Notification && Notification.permission==='granted') new Notification('CHATHUB',{body:txt}); else if(Notification && Notification.permission!=='denied') Notification.requestPermission().then(p=>{ if(p==='granted') new Notification('CHATHUB',{body:txt}); }); }catch(e){} }

</script>
</body>
</html>
"""

# ------------- Socket event handlers (server) -------------
@socketio.on("fetch_initial")
def on_fetch_initial():
    conn = get_conn()
    rows = conn.execute("SELECT username, avatar FROM users").fetchall()
    users = [{"username": r["username"], "avatar": r["avatar"]} for r in rows]
    rooms_rows = conn.execute("SELECT DISTINCT room FROM messages WHERE room IS NOT NULL ORDER BY id DESC LIMIT 100").fetchall()
    rooms = [r["room"] for r in rooms_rows if r["room"]]
    if "Lobby" not in rooms:
        rooms.insert(0, "Lobby")
    conn.close()
    emit("initial", {"users": users, "rooms": rooms}, room=request.sid)

@socketio.on("join_room")
def on_join_room(data):
    username = data.get("username")
    room = data.get("room", "Lobby")
    if not username:
        return
    user_to_sid[username] = request.sid
    sid_to_user[request.sid] = username
    join_room(room)
    msgs = load_room_messages(room)
    emit("load_room_messages", msgs, room=request.sid)
    sys_msg = persist_message(room, "System", None, f"{username} joined {room}", None)
    socketio.emit("new_message_room", sys_msg, room=room)
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT room FROM messages WHERE room IS NOT NULL ORDER BY id DESC LIMIT 100").fetchall()
    rooms = [r["room"] for r in rows if r["room"]]
    if "Lobby" not in rooms:
        rooms.insert(0, "Lobby")
    conn.close()
    socketio.emit("user_list", [{"username": u, "avatar": get_user(u)["avatar"] if get_user(u) else None} for u in user_to_sid.keys()])
    socketio.emit("rooms_list", rooms)

@socketio.on("load_room_messages")
def on_load_room_messages(data):
    room = data.get("room", "Lobby")
    msgs = load_room_messages(room)
    emit("load_room_messages", msgs, room=request.sid)

@socketio.on("send_message")
def on_send_message(data):
    username = data.get("username")
    room = data.get("room")
    message = data.get("message", "")
    image = data.get("image", None)
    reply_to = data.get("reply_to", None)
    if not username or not room:
        return
    avatar = get_user(username)["avatar"] if get_user(username) else None
    msg = persist_message(room, username, None, message, image, reply_to)
    if msg:
        msg["avatar"] = avatar
        socketio.emit("new_message_room", msg, room=room)
        socketio.emit("message", msg, room=room)

@socketio.on("edit_message")
def on_edit_message(data):
    mid = data.get("id")
    new_text = data.get("message")
    user = sid_to_user.get(request.sid)
    updated = edit_message(mid, user, new_text)
    if updated:
        socketio.emit("update_message", updated)

@socketio.on("delete_message")
def on_delete_message(data):
    mid = data.get("id")
    user = sid_to_user.get(request.sid)
    ok = delete_message(mid, user)
    if ok:
        socketio.emit("delete_message", {"id": mid})

@socketio.on("react")
def on_react(data):
    mid = data.get("message_id")
    username = data.get("username")
    emoji = data.get("emoji")
    ok = toggle_reaction(mid, username, emoji)
    reactions = reactions_summary(mid)
    socketio.emit("reactions_update", {"message_id": mid, "reactions": reactions})

@socketio.on("request_reactions")
def on_request_reactions(data):
    mid = data.get("message_id")
    reactions = reactions_summary(mid)
    emit("reactions_update", {"message_id": mid, "reactions": reactions}, room=request.sid)

@socketio.on("pin_message")
def on_pin_message(data):
    mid = data.get("id")
    room = data.get("room", "Lobby")
    p = pin_message(mid, room)
    if p:
        socketio.emit("pinned_message", p)
        pinned = get_pinned(room)
        socketio.emit("pinned_list", pinned)

@socketio.on("typing")
def on_typing(data):
    socketio.emit("typing", {"username": data.get("username")}, room=data.get("room"))

@socketio.on("stop_typing")
def on_stop_typing(data):
    socketio.emit("stop_typing", {"username": data.get("username")}, room=data.get("room"))

@socketio.on("send_dm")
def on_send_dm(data):
    sender = data.get("username")
    to = data.get("to")
    message = data.get("message", "")
    image = data.get("image", None)
    reply_to = data.get("reply_to", None)
    if not sender or not to:
        return
    avatar = get_user(sender)["avatar"] if get_user(sender) else None
    msg = persist_message(None, sender, to, message, image, reply_to)
    if msg:
        msg["avatar"] = avatar
        recipient_sid = user_to_sid.get(to)
        if recipient_sid:
            socketio.emit("new_message_dm", msg, room=recipient_sid)
        socketio.emit("new_message_dm", msg, room=request.sid)

@socketio.on("load_dm")
def on_load_dm(data):
    user = data.get("username")
    other = data.get("other")
    hist = load_dm_history(user, other)
    emit("load_dm_history", hist, room=request.sid)

@socketio.on("search")
def on_search(data):
    room = data.get("room")
    q = data.get("query")
    results = search_room(room, q)
    emit("search_results", {"results": results}, room=request.sid)

@socketio.on("get_pinned")
def on_get_pinned(data):
    room = data.get("room", "Lobby")
    pinned = get_pinned(room)
    emit("pinned_list", pinned, room=request.sid)

@socketio.on("presence")
def on_presence(data):
    # no-op for now
    pass

@socketio.on("connect")
def on_connect():
    pass

@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    user = sid_to_user.get(sid)
    if user:
        user_to_sid.pop(user, None)
        sid_to_user.pop(sid, None)
    socketio.emit("user_list", [{"username": u, "avatar": get_user(u)["avatar"] if get_user(u) else None} for u in user_to_sid.keys()])

# ------------- Startup -------------
 if __name__ == "__main__":
    # Ensure Lobby has a welcome message (for first-time DB setup)
    conn = get_conn()
    cur = conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()
    if cur and cur["c"] == 0:
        persist_message("Lobby", "System", None, "Welcome to CHATHUB", None)
    conn.close()

    # Local dev only (Render/Heroku will use gunicorn instead)
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)  # local only
