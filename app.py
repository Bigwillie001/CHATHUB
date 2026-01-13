import eventlet
# vital
eventlet.monkey_patch()

import os
import time
import base64
from functools import wraps
from flask import Flask, request, redirect, url_for, render_template_string, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy

# ------------- Config -------------
APP_PORT = int(os.environ.get("PORT", 5000))
SECRET = os.environ.get("CHATHUB_SECRET", "chathub_secret_for_prod")
UPLOAD_LIMIT = 2 * 1024 * 1024  # 2 MB
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET
app.config["MAX_CONTENT_LENGTH"] = UPLOAD_LIMIT

# --- DATABASE CONFIGURATION ---
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///chathub_complete.sqlite'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# ------------- Database Models -------------
class User(db.Model):
    username = db.Column(db.String(80), primary_key=True)
    password = db.Column(db.String(200))
    avatar = db.Column(db.Text)
    theme = db.Column(db.String(20), default='dark')

    def to_dict(self):
        return {"username": self.username, "avatar": self.avatar, "theme": self.theme}

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(80))
    sender = db.Column(db.String(80))
    receiver = db.Column(db.String(80))
    message = db.Column(db.Text)
    image = db.Column(db.Text)
    reply_to = db.Column(db.Integer)
    timestamp = db.Column(db.Integer)

    def to_dict(self):
        return {
            "id": self.id, "room": self.room, "sender": self.sender,
            "receiver": self.receiver, "message": self.message,
            "image": self.image, "reply_to": self.reply_to,
            "timestamp": self.timestamp
        }

class Reaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer)
    username = db.Column(db.String(80))
    emoji = db.Column(db.String(10))

class Pin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer)
    room = db.Column(db.String(80))

with app.app_context():
    db.create_all()

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

def persist_message(room, sender, receiver, message_text, image_dataurl=None, reply_to=None):
    ts = int(time.time())
    new_msg = Message(room=room, sender=sender, receiver=receiver, message=message_text, 
                      image=image_dataurl, reply_to=reply_to, timestamp=ts)
    db.session.add(new_msg)
    db.session.commit()
    return new_msg.to_dict()

# ------------- Presence maps -------------
user_to_sid = {}
sid_to_user = {}

# ------------- Routes -------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        password = request.form.get("password","")
        if not username or not password:
            flash("Credentials required")
            return redirect(url_for("register"))
        if User.query.get(username):
            flash("Username taken")
            return redirect(url_for("register"))
        avatar = None
        f = request.files.get("avatar")
        if f and allowed_file(f.filename):
            avatar = file_to_dataurl(f)
        new_u = User(username=username, password=generate_password_hash(password), avatar=avatar)
        db.session.add(new_u)
        db.session.commit()
        session["user"] = username
        return redirect(url_for("chat"))
    return render_template_string(REG_HTML)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        u = User.query.get(username)
        if u and check_password_hash(u.password, request.form.get("password","")):
            session["user"] = username
            return redirect(url_for("chat"))
        flash("Invalid login")
    return render_template_string(LOGIN_HTML)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/chat")
@login_required
def chat():
    u = User.query.get(session["user"])
    return render_template_string(MAIN_HTML, username=u.username, theme=u.theme)

@app.route("/upload_avatar", methods=["POST"])
@login_required
def upload_avatar():
    f = request.files.get("avatar")
    if f and allowed_file(f.filename):
        u = User.query.get(session["user"])
        u.avatar = file_to_dataurl(f)
        db.session.commit()
    return redirect(url_for("chat"))

@app.route("/set_theme", methods=["POST"])
@login_required
def set_theme_route():
    u = User.query.get(session["user"])
    u.theme = request.form.get("theme", "dark")
    db.session.commit()
    return redirect(url_for("chat"))

@app.route("/")
def index():
    return redirect(url_for("chat") if "user" in session else url_for("login"))

# ------------- Inline Templates -------------
REG_HTML = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Register</title></head><body style="font-family:sans-serif;padding:20px;"><h2>Register</h2>{% with m=get_flashed_messages() %}{% if m %}<ul>{% for msg in m %}<li style="color:red">{{msg}}</li>{% endfor %}</ul>{% endif %}{% endwith %}<form method="post" enctype="multipart/form-data"><input name="username" placeholder="Username" required><br><br><input name="password" type="password" placeholder="Password" required><br><br><label>Avatar</label><br><input type="file" name="avatar" accept="image/*"><br><br><button type="submit">Join</button></form><p><a href="{{url_for('login')}}">Login</a></p></body></html>"""
LOGIN_HTML = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Login</title></head><body style="font-family:sans-serif;padding:20px;"><h2>Login</h2>{% with m=get_flashed_messages() %}{% if m %}<ul>{% for msg in m %}<li style="color:red">{{msg}}</li>{% endfor %}</ul>{% endif %}{% endwith %}<form method="post"><input name="username" placeholder="Username" required><br><br><input name="password" type="password" placeholder="Password" required><br><br><button type="submit">Login</button></form><p><a href="{{url_for('register')}}">Register</a></p></body></html>"""

MAIN_HTML = r"""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=0">
<title>CHATHUB</title>
<style>
:root{ --bg-dark:#0f1720; --bg-green:#063; --bg-white:#fff; --text-dark:#e7eef8; --text-light:#111; --me:#00c3ff; --other:#263044; }
html,body{height:100%;margin:0;font-family:Inter,Arial,sans-serif; overflow-x: hidden; -webkit-tap-highlight-color: transparent;}
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
.messages{ flex:1; overflow:auto; padding:12px; border-radius:8px; background:rgba(0,0,0,0.06); max-height:60vh; position:relative; }
.msg{ display:flex; gap:8px; margin:6px 0; align-items:flex-start; max-width:85%; user-select: none; transition: transform 0.2s cubic-bezier(0.1, 0.5, 0.5, 1); }
.msg.me{ margin-left:auto; background:var(--me); color:#000; padding:8px; border-radius:12px; }
.msg.other{ margin-right:auto; background:var(--other); color:inherit; padding:8px; border-radius:12px; }
.msg.holding { transform: scale(0.96); filter: brightness(0.8); }
.msg .meta{ font-size:0.8em; margin-bottom:6px; color:rgba(255,255,255,0.9); }
.controls-row{ display:flex; gap:8px; align-items:center; margin-top:8px; }
.small{ font-size:0.85em; color:rgba(255,255,255,0.75); }
.img-preview{ max-width:220px; border-radius:8px; margin-top:6px; }
.reply-box{ border-left:2px solid rgba(255,255,255,0.08); padding-left:6px; margin-bottom:6px; color:rgba(255,255,255,0.85); }

/* --- Action Menu Animation --- */
#actionMenu {
    display: none; position: fixed; z-index: 1000; background: #2c3e50; border-radius: 14px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.5); overflow: hidden; min-width: 180px;
    backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.1);
    transform: scale(0.5); opacity: 0; transition: transform 0.2s cubic-bezier(0.175, 0.885, 0.32, 1.275), opacity 0.2s;
}
#actionMenu.show { display: block; transform: scale(1); opacity: 1; }
.menu-item { padding: 14px 18px; border-bottom: 1px solid rgba(255,255,255,0.05); cursor: pointer; display: flex; align-items: center; gap: 12px; font-weight: 600; }
.menu-item:active { background: rgba(255,255,255,0.1); }
.menu-reacts { display: flex; justify-content: space-around; padding: 10px; border-bottom: 1px solid rgba(255,255,255,0.05); }
.react-opt { font-size: 1.5rem; cursor: pointer; }

@media (max-width:820px){ .left{ max-width:100%; } .layout{ flex-direction:column; } .messages{ max-height:50vh; } }
</style>
</head>
<body onclick="closeMenu()">
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
      <div id="roomsList" style="margin-top:10px;"></div>
      <div style="margin-top:8px;">
        <input id="newRoom" placeholder="New room" style="width:100px" />
        <button onclick="createRoom()">Create</button>
      </div>
      <div style="margin-top:15px; font-weight:700;">Pinned</div>
      <div id="pinnedList"></div>
    </div>

    <div class="main">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <b id="viewLabel">Lobby</b>
        <div class="small" id="typingIndicator"></div>
      </div>
      <div id="messages" class="messages"></div>
      <div class="controls-row">
        <input id="toInput" placeholder="DM to..." style="width:80px" />
        <input id="messageInput" placeholder="Type..." style="flex:1" oninput="typing()" />
        <input id="imageInput" type="file" accept="image/*" style="width:100px" />
        <button onclick="sendMessage()">Send</button>
      </div>
    </div>
  </div>
</div>

<div id="actionMenu">
    <div class="menu-reacts">
        <span class="react-opt" onclick="react(activeMsgId, '👍')">👍</span>
        <span class="react-opt" onclick="react(activeMsgId, '❤️')">❤️</span>
        <span class="react-opt" onclick="react(activeMsgId, '😂')">😂</span>
        <span class="react-opt" onclick="react(activeMsgId, '🔥')">🔥</span>
    </div>
    <div class="menu-item" onclick="replyTo(activeMsgId)">↩️ Reply</div>
    <div class="menu-item" onclick="pinMsg(activeMsgId)">📌 Pin</div>
    <div class="menu-item" id="editOption" onclick="editMsg(activeMsgId)">✏️ Edit</div>
    <div class="menu-item" id="deleteOption" style="color:red" onclick="delMsg(activeMsgId)">🗑️ Delete</div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.min.js"></script>
<script>
const socket = io();
const user = {{ username | tojson }};
let currentRoom = "Lobby";
let mode = "room";
let activeMsgId = null;
let pressTimer = null;
let replyToId = null;

function defaultIdent(name){
  const initials = (name||"?").substring(0,2).toUpperCase();
  return "data:image/svg+xml;base64,"+btoa(`<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64'><rect width='100%' height='100%' fill='#888'/><text x='50%' y='55%' font-size='28' text-anchor='middle' fill='white' font-family='Arial' dy='.3em'>${initials}</text></svg>`);
}

function escapeHtml(s){ return s ? s.replace(/[&<>"']/g, m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])) : ""; }

socket.on('connect', ()=> { socket.emit('fetch_initial'); });

socket.on('initial', (data) => {
  renderUsers(data.users);
  renderRooms(data.rooms);
  switchToRoom("Lobby");
});

function renderUsers(users){
  const el = document.getElementById("usersList"); el.innerHTML="";
  users.forEach(u=>{
    const d = document.createElement("div"); d.className="user-row";
    d.innerHTML = `<img class="avatar" src="${u.avatar || defaultIdent(u.username)}"><b>${u.username}</b>`;
    d.onclick = ()=> openDM(u.username);
    el.appendChild(d);
  });
}

function renderRooms(rooms){
  const el = document.getElementById("roomsList"); el.innerHTML="";
  rooms.forEach(r=>{
    const d = document.createElement("div"); d.style.padding="5px"; d.style.cursor="pointer";
    d.innerText = "# "+r; d.onclick = ()=> switchToRoom(r);
    el.appendChild(d);
  });
}

function switchToRoom(r){
  mode="room"; currentRoom=r;
  document.getElementById("viewLabel").innerText = r;
  socket.emit("join_room", {username:user, room:r});
}

function openDM(other){
  mode='dm'; dmWith=other; document.getElementById('viewLabel').innerText = 'DM: '+other;
  socket.emit('load_dm',{username:user, other:other});
}

function renderMessage(m){
  const wrap = document.getElementById("messages");
  const div = document.createElement("div");
  div.className = "msg " + (m.sender===user ? "me":"other");
  div.dataset.id = m.id;
  
  div.onmousedown = (e) => startPress(e, m.id, m.sender, div);
  div.ontouchstart = (e) => startPress(e, m.id, m.sender, div);
  div.onmouseup = () => cancelPress(div);
  div.ontouchend = () => cancelPress(div);

  const t = new Date((m.timestamp||0)*1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  const replyPart = m.reply_to ? `<div class='reply-box'>Reply to #${m.reply_to}</div>` : "";
  
  let inner = `<div style='display:flex;flex-direction:column;'><div class='meta'><b>${m.sender}</b> <span class='small'>${t}</span></div>${replyPart}<div>${escapeHtml(m.message)}</div>`;
  if(m.image) inner += `<img class='img-preview' src='${m.image}'>`;
  inner += `<div id='reactions-${m.id}' class='small' style='margin-top:4px'></div></div>`;
  
  div.innerHTML = inner;
  wrap.appendChild(div);
  wrap.scrollTop = wrap.scrollHeight;
  socket.emit("request_reactions", {message_id: m.id});
}

function startPress(e, id, sender, el) {
    activeMsgId = id;
    el.classList.add("holding");
    pressTimer = setTimeout(() => {
        showMenu(e, sender === user);
    }, 600);
}

function cancelPress(el) { 
    clearTimeout(pressTimer); 
    el.classList.remove("holding");
}

function showMenu(e, isMe) {
    const menu = document.getElementById("actionMenu");
    let x = e.pageX || (e.touches ? e.touches[0].pageX : 0);
    let y = e.pageY || (e.touches ? e.touches[0].pageY : 0);
    
    if (x + 180 > window.innerWidth) x = window.innerWidth - 190;
    if (y + 200 > window.innerHeight) y = window.innerHeight - 210;

    menu.style.left = x + "px"; menu.style.top = y + "px";
    menu.style.display = "block";
    setTimeout(() => menu.classList.add("show"), 10);

    document.getElementById("editOption").style.display = isMe ? "flex" : "none";
    document.getElementById("deleteOption").style.display = isMe ? "flex" : "none";
    if (navigator.vibrate) navigator.vibrate(40);
}

function closeMenu() {
    const menu = document.getElementById("actionMenu");
    menu.classList.remove("show");
    setTimeout(() => { if(!menu.classList.contains("show")) menu.style.display = "none"; }, 200);
}

function sendMessage(){
  const text = document.getElementById("messageInput").value.trim();
  const f = document.getElementById("imageInput").files[0];
  const to = document.getElementById("toInput").value.trim();
  if(!text && !f) return;
  const payload = {username:user, message:text, reply_to:replyToId};
  if(to) { payload.to = to; socket.emit("send_dm", payload); }
  else { payload.room = currentRoom; socket.emit("send_message", payload); }
  document.getElementById("messageInput").value="";
}

function replyTo(id){ replyToId = id; closeMenu(); }
function editMsg(id){ closeMenu(); const t = prompt('Edit:'); if(t) socket.emit('edit_message', {id, message:t}); }
function delMsg(id){ closeMenu(); if(confirm('Delete?')) socket.emit('delete_message', {id}); }
function pinMsg(id){ closeMenu(); socket.emit('pin_message', {id, room:currentRoom}); }
function react(id, emoji){ socket.emit('react', {message_id:id, username:user, emoji}); closeMenu(); }

socket.on("load_room_messages", (msgs)=>{ document.getElementById("messages").innerHTML=""; msgs.forEach(renderMessage); });
socket.on("new_message_room", (m)=>{ if(m.room===currentRoom) renderMessage(m); });
socket.on("reactions_update", (d)=>{ const el=document.getElementById('reactions-'+d.message_id); if(el) el.innerHTML = Object.entries(d.reactions).map(([e,c])=>`${e}${c}`).join(" "); });
socket.on("user_list", (users)=> renderUsers(users));

</script>
</body>
</html>
"""

# ------------- Socket Handlers -------------
@socketio.on("fetch_initial")
def on_fetch_initial():
    users = [u.to_dict() for u in User.query.all()]
    rooms = [m.room for m in Message.query.with_entities(Message.room).distinct().all() if m.room]
    emit("initial", {"users": users, "rooms": "Lobby" if not rooms else rooms})

@socketio.on("join_room")
def on_join_room(data):
    user_to_sid[data['username']] = request.sid
    sid_to_user[request.sid] = data['username']
    join_room(data['room'])
    msgs = Message.query.filter_by(room=data['room'], receiver=None).order_by(Message.id.asc()).limit(100).all()
    emit("load_room_messages", [m.to_dict() for m in msgs])

@socketio.on("send_message")
def on_send_message(data):
    msg = persist_message(data['room'], data['username'], None, data['message'], data.get('image'), data.get('reply_to'))
    socketio.emit("new_message_room", msg, room=data['room'])

@socketio.on("react")
def on_react(data):
    existing = Reaction.query.filter_by(message_id=data['message_id'], username=data['username'], emoji=data['emoji']).first()
    if existing: db.session.delete(existing)
    else: db.session.add(Reaction(message_id=data['message_id'], username=data['username'], emoji=data['emoji']))
    db.session.commit()
    reacts = Reaction.query.filter_by(message_id=data['message_id']).all()
    summary = {}
    for r in reacts: summary[r.emoji] = summary.get(r.emoji, 0) + 1
    socketio.emit("reactions_update", {"message_id": data['message_id'], "reactions": summary})

@socketio.on("delete_message")
def on_delete_message(data):
    msg = Message.query.get(data['id'])
    if msg and msg.sender == sid_to_user.get(request.sid):
        db.session.delete(msg)
        db.session.commit()
        socketio.emit("delete_message", {"id": data['id']})

if __name__ == "__main__":
    socketio.run(app, host='0.0.0.0', port=APP_PORT)