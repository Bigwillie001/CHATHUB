import eventlet
# Vital
eventlet.monkey_patch()

import os
import time
import datetime
import base64
import json
from functools import wraps
from flask import Flask, request, redirect, url_for, render_template_string, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, and_

# ------------- CONFIGURATION -------------
APP_PORT = int(os.environ.get("PORT", 5000))
SECRET = os.environ.get("CHATHUB_SECRET", "chathub_ultra_secret_2026")
UPLOAD_LIMIT = 10 * 1024 * 1024  # 10MB
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET
app.config["MAX_CONTENT_LENGTH"] = UPLOAD_LIMIT

# --- DATABASE ENGINE ---
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

# Using a new filename to avoid "column missing" errors from previous versions
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///chathub_v4_final.sqlite'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# ------------- DATABASE MODELS -------------
class User(db.Model):
    __tablename__ = 'users'
    username = db.Column(db.String(80), primary_key=True)
    password = db.Column(db.String(255), nullable=False)
    avatar = db.Column(db.Text, nullable=True)
    theme = db.Column(db.String(20), default='dark')
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def to_dict(self):
        return {"username": self.username, "avatar": self.avatar, "theme": self.theme}

class Message(db.Model):
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    room = db.Column(db.String(80), index=True)      
    sender = db.Column(db.String(80), index=True)    
    receiver = db.Column(db.String(80), index=True)  
    message = db.Column(db.Text, nullable=True)
    image = db.Column(db.Text, nullable=True)
    reply_to = db.Column(db.Integer, nullable=True)
    is_edited = db.Column(db.Boolean, default=False)
    is_read = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.Integer, default=lambda: int(time.time()))

    def to_dict(self):
        return {
            "id": self.id, "room": self.room, "sender": self.sender,
            "receiver": self.receiver, "message": self.message,
            "image": self.image, "reply_to": self.reply_to,
            "is_edited": self.is_edited, "is_read": self.is_read,
            "timestamp": self.timestamp
        }

class Reaction(db.Model):
    __tablename__ = 'reactions'
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id', ondelete='CASCADE'))
    username = db.Column(db.String(80))
    emoji = db.Column(db.String(10))

class Pin(db.Model):
    __tablename__ = 'pins'
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id', ondelete='CASCADE'))
    room = db.Column(db.String(80))

with app.app_context():
    db.create_all()

# ------------- HELPERS -------------
def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user" not in session: return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def file_to_dataurl(storage_file):
    raw = storage_file.read()
    mime = storage_file.mimetype or "image/png"
    return f"data:{mime};base64," + base64.b64encode(raw).decode("utf-8")

# ------------- ROUTES -------------
@app.route("/")
def index():
    return redirect(url_for("chat") if "user" in session else url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        u_name = request.form.get("username", "").strip()
        u_pass = request.form.get("password", "")
        if User.query.get(u_name):
            flash("User already exists!")
            return redirect(url_for("register"))
        avatar_url = None
        f = request.files.get("avatar")
        if f and allowed_file(f.filename):
            avatar_url = file_to_dataurl(f)
        new_user = User(username=u_name, password=generate_password_hash(u_pass), avatar=avatar_url)
        db.session.add(new_user)
        db.session.commit()
        session["user"] = u_name
        return redirect(url_for("chat"))
    return render_template_string(AUTH_HTML, mode="Register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u_name = request.form.get("username", "").strip()
        u_pass = request.form.get("password", "")
        u = User.query.get(u_name)
        if u and check_password_hash(u.password, u_pass):
            session["user"] = u_name
            return redirect(url_for("chat"))
        flash("Invalid Credentials")
    return render_template_string(AUTH_HTML, mode="Login")

@app.route("/chat")
@login_required
def chat():
    u = User.query.get(session["user"])
    return render_template_string(MAIN_HTML, user=u)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ------------- TEMPLATES -------------
AUTH_HTML = """
<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:sans-serif;background:#0f172a;color:#fff;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;}
form{background:#1e293b;padding:30px;border-radius:12px;width:300px;box-shadow:0 4px 15px rgba(0,0,0,0.3);}
input{width:100%;padding:10px;margin:10px 0;box-sizing:border-box;border-radius:6px;border:none;background:#334155;color:#fff;}
button{width:100%;padding:10px;background:#38bdf8;border:none;border-radius:6px;font-weight:bold;cursor:pointer;}
</style></head>
<body><form method="post" enctype="multipart/form-data"><h2>{{mode}}</h2>
{% with m=get_flashed_messages() %}{% if m %}<p style="color:#f87171">{{m[0]}}</p>{% endif %}{% endwith %}
<input name="username" placeholder="Username" required>
<input name="password" type="password" placeholder="Password" required>
{% if mode == 'Register' %}<label style="font-size:12px">Avatar:</label><input type="file" name="avatar" accept="image/*">{% endif %}
<button type="submit">{{mode}}</button>
<p style="font-size:12px">{% if mode == 'Login' %}<a href="/register" style="color:#38bdf8">Create Account</a>{% else %}<a href="/login" style="color:#38bdf8">Already have one?</a>{% endif %}</p>
</form></body></html>
"""

MAIN_HTML = r"""
<!doctype html>
<html>
<head>
    <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=0">
    <title>CHATHUB PRO</title>
    <style>
        :root { 
            --bg: {{ '#0f172a' if user.theme=='dark' else '#064e3b' if user.theme=='green' else '#f8fafc' }};
            --panel: {{ '#1e293b' if user.theme=='dark' else '#065f46' if user.theme=='green' else '#ffffff' }};
            --text: {{ '#f1f5f9' if user.theme!='white' else '#1e293b' }};
            --accent: #38bdf8;
            --me: #0284c7;
            --other: #334155;
        }
        body, html { height:100%; margin:0; font-family:'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--text); overflow:hidden; }
        .flex { display:flex; }
        .app-container { height:100vh; flex-direction:column; display:flex; }
        header { padding:10px 20px; background:var(--panel); display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid rgba(255,255,255,0.05); }
        .avatar-img { width:35px; height:35px; border-radius:50%; object-fit:cover; border:2px solid var(--accent); }
        .main-layout { flex:1; overflow:hidden; display:flex; }
        aside { width:280px; background:var(--panel); border-right:1px solid rgba(255,255,255,0.05); flex-direction:column; display:flex; transition: transform 0.3s; z-index:100; }
        .sidebar-section { padding:15px; border-bottom:1px solid rgba(255,255,255,0.05); }
        .list-item { padding:10px; border-radius:8px; cursor:pointer; margin-bottom:5px; display:flex; align-items:center; gap:10px; transition:0.2s; }
        .list-item:hover { background: rgba(255,255,255,0.05); }
        .list-item.active { background: var(--accent); color: #000; font-weight:bold; }
        main { flex:1; display:flex; flex-direction:column; background:rgba(0,0,0,0.1); position:relative; }
        #messageDisplay { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:12px; }
        .msg-wrapper { max-width:75%; padding:10px 14px; border-radius:18px; position:relative; font-size:15px; line-height:1.4; transition: transform 0.1s; user-select:none; }
        .msg-wrapper.me { align-self:flex-end; background:var(--me); color:#fff; border-bottom-right-radius:2px; }
        .msg-wrapper.other { align-self:flex-start; background:var(--other); color:#fff; border-bottom-left-radius:2px; }
        .msg-wrapper.holding { transform: scale(0.96); filter: brightness(0.9); }
        .meta { font-size:10px; opacity:0.8; margin-top:4px; display:flex; align-items:center; justify-content:flex-end; gap:4px; }
        .sender-name { font-size:11px; font-weight:bold; margin-bottom:4px; display:block; color:var(--accent); }
        .msg-img { max-width:100%; border-radius:10px; margin-top:8px; display:block; }
        .input-area { padding:15px; background:var(--panel); display:flex; gap:10px; align-items:center; }
        #msgInput { flex:1; background:#334155; border:none; padding:12px; border-radius:25px; color:#fff; outline:none; }
        .icon-btn { cursor:pointer; font-size:20px; opacity:0.8; transition:0.2s; }
        .icon-btn:hover { opacity:1; transform:scale(1.1); }
        #actionMenu {
            display:none; position:fixed; z-index:2000; background:#1e293b; border-radius:15px; width:180px;
            box-shadow:0 10px 30px rgba(0,0,0,0.5); border:1px solid rgba(255,255,255,0.1); overflow:hidden;
        }
        .menu-item { padding:12px 15px; cursor:pointer; display:flex; align-items:center; gap:10px; border-bottom:1px solid rgba(255,255,255,0.05); }
        .menu-item:hover { background:rgba(255,255,255,0.1); }
        #typing { font-size:12px; font-style:italic; padding:0 20px 5px; opacity:0.7; height:18px; }
        @media (max-width: 768px) {
            aside { position:absolute; height:100%; transform: translateX(-100%); }
            aside.open { transform: translateX(0); }
        }
    </style>
</head>
<body onclick="hideMenu()">
    <div class="app-container">
        <header>
            <div class="flex" style="gap:15px; align-items:center;">
                <span class="icon-btn" onclick="toggleSidebar(event)">☰</span>
                <b style="font-size:18px; color:var(--accent)">CHATHUB PRO</b>
            </div>
            <div class="flex" style="align-items:center; gap:10px;">
                <span>{{ user.username }}</span>
                <img src="{{ user.avatar or 'https://ui-avatars.com/api/?name=' + user.username }}" class="avatar-img">
                <a href="/logout" style="color:var(--accent); text-decoration:none; font-size:13px;">Logout</a>
            </div>
        </header>

        <div class="main-layout">
            <aside id="sidebar">
                <div class="sidebar-section">
                    <input type="text" id="search" placeholder="Search..." oninput="doSearch()" style="width:100%; background:#334155; border:none; color:white; padding:8px; border-radius:5px;">
                </div>
                <div style="flex:1; overflow-y:auto; padding:10px;">
                    <small style="opacity:0.5; font-weight:bold;">CHANNELS</small>
                    <div id="roomList"></div>
                    <br>
                    <small style="opacity:0.5; font-weight:bold;">DIRECT MESSAGES</small>
                    <div id="userList"></div>
                </div>
                <div class="sidebar-section">
                    <button onclick="createRoom()" style="width:100%; background:var(--accent); border:none; padding:10px; border-radius:8px; font-weight:bold; cursor:pointer;">+ New Room</button>
                </div>
            </aside>

            <main>
                <div style="padding:12px 20px; background:rgba(255,255,255,0.03); display:flex; justify-content:space-between; align-items:center;">
                    <b id="chatTitle"># Lobby</b>
                    <span id="pinBtn" style="cursor:pointer; font-size:13px; color:var(--accent)">📌 Pins</span>
                </div>
                <div id="messageDisplay"></div>
                <div id="typing"></div>
                <div class="input-area">
                    <label class="icon-btn">🖼️<input type="file" id="imgInput" hidden accept="image/*" onchange="previewSelected()"></label>
                    <input type="text" id="msgInput" placeholder="Write something..." onkeypress="handleKey(event)" oninput="sendTyping()">
                    <span class="icon-btn" onclick="sendMsg()">🚀</span>
                </div>
            </main>
        </div>
    </div>

    <div id="actionMenu">
        <div style="display:flex; justify-content:space-around; padding:10px; background:rgba(255,255,255,0.05);">
            <span class="icon-btn" onclick="react('❤️')">❤️</span>
            <span class="icon-btn" onclick="react('😂')">😂</span>
            <span class="icon-btn" onclick="react('🔥')">🔥</span>
            <span class="icon-btn" onclick="react('👍')">👍</span>
        </div>
        <div class="menu-item" onclick="prepareReply()">↩️ Reply</div>
        <div class="menu-item" onclick="pinMsg()">📌 Pin</div>
        <div class="menu-item" id="editBtn" onclick="prepareEdit()">✏️ Edit</div>
        <div class="menu-item" id="delBtn" onclick="deleteMsg()" style="color:#f87171">🗑️ Delete</div>
    </div>

    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <script>
        const socket = io();
        const myName = "{{ user.username }}";
        let currentTarget = "Lobby";
        let isDM = false;
        let activeMsgId = null;
        let holdTimer = null;
        let replyToId = null;
        let typingTimer = null;

        function toggleSidebar(e) { e.stopPropagation(); document.getElementById('sidebar').classList.toggle('open'); }

        socket.on('connect', () => {
            socket.emit('sync_data');
            join(currentTarget, false);
        });

        socket.on('sync_ready', (data) => {
            const rList = document.getElementById('roomList');
            rList.innerHTML = data.rooms.map(r => `<div class="list-item ${currentTarget===r?'active':''}" onclick="join('${r}', false)"># ${r}</div>`).join('');
            
            const uList = document.getElementById('userList');
            uList.innerHTML = data.users.map(u => u.username === myName ? '' : `
                <div class="list-item ${currentTarget===u.username?'active':''}" onclick="join('${u.username}', true)">
                    <img src="${u.avatar || 'https://ui-avatars.com/api/?name='+u.username}" style="width:24px;height:24px;border-radius:50%"> ${u.username}
                </div>
            `).join('');
        });

        function join(target, dmFlag) {
            currentTarget = target;
            isDM = dmFlag;
            document.getElementById('chatTitle').innerText = (isDM ? "@ " : "# ") + target;
            document.getElementById('messageDisplay').innerHTML = "";
            socket.emit('join_chat', { target, isDM });
            if (isDM) socket.emit('mark_read', { sender: target });
            
            document.querySelectorAll('.list-item').forEach(el => el.classList.remove('active'));
            // Sidebar auto-close on mobile
            if (window.innerWidth < 768) document.getElementById('sidebar').classList.remove('open');
        }

        function sendMsg() {
            const text = document.getElementById('msgInput').value.trim();
            const imgFile = document.getElementById('imgInput').files[0];
            if (!text && !imgFile) return;

            if (imgFile) {
                const reader = new FileReader();
                reader.onload = () => {
                    socket.emit('new_msg', { text, img: reader.result, target: currentTarget, isDM, replyTo: replyToId });
                    clearInputs();
                };
                reader.readAsDataURL(imgFile);
            } else {
                socket.emit('new_msg', { text, target: currentTarget, isDM, replyTo: replyToId });
                clearInputs();
            }
        }

        function clearInputs() {
            document.getElementById('msgInput').value = "";
            document.getElementById('imgInput').value = "";
            replyToId = null;
        }

        function handleKey(e) { if(e.key === 'Enter') sendMsg(); }

        socket.on('msg_arrival', (m) => {
            const isRelevant = isDM ? (m.sender === currentTarget || m.sender === myName) : (m.room === currentTarget);
            if (isRelevant) renderBubble(m);
            if (isDM && m.sender === currentTarget) socket.emit('mark_read', { sender: currentTarget });
        });

        socket.on('load_history', (msgs) => {
            msgs.forEach(renderBubble);
            const d = document.getElementById('messageDisplay');
            d.scrollTop = d.scrollHeight;
        });

        function renderBubble(m) {
            const display = document.getElementById('messageDisplay');
            const div = document.createElement('div');
            div.className = `msg-wrapper ${m.sender === myName ? 'me' : 'other'}`;
            div.id = `msg-${m.id}`;
            
            div.onmousedown = (e) => startHold(e, m.id, m.sender);
            div.ontouchstart = (e) => startHold(e, m.id, m.sender);
            div.onmouseup = endHold;
            div.ontouchend = endHold;

            let html = `<span class="sender-name">${m.sender}</span>`;
            if (m.reply_to) html += `<div style="font-size:10px; opacity:0.6; border-left:2px solid #fff; padding-left:5px; margin-bottom:5px;">Replying to #${m.reply_to}</div>`;
            if (m.message) html += `<div>${m.message}</div>`;
            if (m.image) html += `<img src="${m.image}" class="msg-img">`;
            
            const tickColor = m.is_read ? 'color:#38bdf8;' : 'opacity:0.3;';
            const ticks = (m.sender === myName && isDM) ? `<span id="tick-${m.id}" style="${tickColor} font-weight:bold;">✔✔</span>` : '';
            
            html += `<span class="meta">${m.is_edited ? '(edited) ' : ''} 
                    ${new Date(m.timestamp*1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})} ${ticks}</span>`;
            
            div.innerHTML = html;
            display.appendChild(div);
            display.scrollTop = display.scrollHeight;
        }

        // --- Context Menu Logic ---
        function startHold(e, id, sender) {
            activeMsgId = id;
            document.getElementById(`msg-${id}`).classList.add('holding');
            holdTimer = setTimeout(() => showMenu(e, sender === myName), 600);
        }
        function endHold() {
            clearTimeout(holdTimer);
            document.querySelectorAll('.msg-wrapper').forEach(el => el.classList.remove('holding'));
        }
        function showMenu(e, mine) {
            const menu = document.getElementById('actionMenu');
            const x = e.clientX || (e.touches ? e.touches[0].clientX : 0);
            const y = e.clientY || (e.touches ? e.touches[0].clientY : 0);
            menu.style.left = Math.min(x, window.innerWidth - 190) + "px";
            menu.style.top = Math.min(y, window.innerHeight - 250) + "px";
            menu.style.display = "block";
            document.getElementById('editBtn').style.display = mine ? 'flex' : 'none';
            document.getElementById('delBtn').style.display = mine ? 'flex' : 'none';
            if(navigator.vibrate) navigator.vibrate(40);
        }
        function hideMenu() { document.getElementById('actionMenu').style.display = 'none'; }

        // --- Blue Ticks Update ---
        socket.on('read_notification', (data) => {
            if (data.sender === myName && currentTarget === data.reader) {
                document.querySelectorAll('[id^="tick-"]').forEach(t => {
                    t.style.color = '#38bdf8'; t.style.opacity = '1';
                });
            }
        });

        // --- Typing ---
        function sendTyping() {
            socket.emit('typing_status', { target: currentTarget, isDM, status: true });
            if(typingTimer) clearTimeout(typingTimer);
            typingTimer = setTimeout(() => socket.emit('typing_status', { target: currentTarget, isDM, status: false }), 2000);
        }
        socket.on('typing_update', (d) => {
            const el = document.getElementById('typing');
            el.innerText = (d.status && d.user !== myName) ? d.user + " is typing..." : "";
        });

        function createRoom() {
            const n = prompt("Room Name:");
            if(n) join(n, false);
        }

        function doSearch() {
            const q = document.getElementById('search').value;
            socket.emit('search_msgs', { q, target: currentTarget });
        }

        socket.on('search_results', (msgs) => {
            document.getElementById('messageDisplay').innerHTML = "<div style='text-align:center; opacity:0.5'>--- Search Results ---</div>";
            msgs.forEach(renderBubble);
        });

        // Placeholder functions for menu
        function react(emoji) { socket.emit('msg_action', { type:'react', id:activeMsgId, emoji }); }
        function pinMsg() { socket.emit('msg_action', { type:'pin', id:activeMsgId, room:currentTarget }); }
        function deleteMsg() { if(confirm("Delete?")) socket.emit('msg_action', { type:'delete', id:activeMsgId }); }
        function prepareEdit() { const t = prompt("Edit message:"); if(t) socket.emit('msg_action', {type:'edit', id:activeMsgId, text:t}); }
        function prepareReply() { replyToId = activeMsgId; document.getElementById('msgInput').placeholder = "Replying to #"+activeMsgId+"..."; document.getElementById('msgInput').focus(); }

        socket.on('msg_deleted', (d) => { document.getElementById(`msg-${d.id}`)?.remove(); });
        socket.on('msg_updated', (d) => { 
            const el = document.getElementById(`msg-${d.id}`);
            if(el) { 
                const content = el.querySelector('div:not([style])'); 
                if(content) content.innerText = d.text;
            }
        });
    </script>
</body>
</html>
"""

# ------------- SOCKET HANDLERS -------------

@socketio.on('sync_data')
def handle_sync():
    users = [u.to_dict() for u in User.query.all()]
    rooms_query = db.session.query(Message.room).distinct().all()
    rooms = [r[0] for r in rooms_query if r[0]]
    if "Lobby" not in rooms: rooms.append("Lobby")
    emit('sync_ready', {'users': users, 'rooms': rooms})

@socketio.on('join_chat')
def handle_join(data):
    target, is_dm, me = data['target'], data['isDM'], session.get('user')
    if not is_dm:
        join_room(target)
        msgs = Message.query.filter_by(room=target).order_by(Message.id.asc()).limit(50).all()
    else:
        msgs = Message.query.filter(or_(
            and_(Message.sender == me, Message.receiver == target),
            and_(Message.sender == target, Message.receiver == me)
        )).order_by(Message.id.asc()).limit(50).all()
    emit('load_history', [m.to_dict() for m in msgs])

@socketio.on('new_msg')
def handle_new_msg(data):
    me = session.get('user')
    new_m = Message(
        sender=me, message=data.get('text'), image=data.get('img'), 
        reply_to=data.get('replyTo'), room=None if data['isDM'] else data['target'],
        receiver=data['target'] if data['isDM'] else None
    )
    db.session.add(new_m)
    db.session.commit()
    msg_dict = new_m.to_dict()
    if data['isDM']:
        socketio.emit('msg_arrival', msg_dict) # Broadcast filtered by client for simplicity
    else:
        socketio.emit('msg_arrival', msg_dict, room=data['target'])

@socketio.on('mark_read')
def handle_mark_read(data):
    me, sender = session.get('user'), data.get('sender')
    unread = Message.query.filter_by(sender=sender, receiver=me, is_read=False).all()
    if unread:
        for m in unread: m.is_read = True
        db.session.commit()
        socketio.emit('read_notification', {'reader': me, 'sender': sender})

@socketio.on('msg_action')
def handle_action(data):
    me, msg = session.get('user'), Message.query.get(data['id'])
    if not msg: return
    if data['type'] == 'delete' and msg.sender == me:
        db.session.delete(msg)
        db.session.commit()
        socketio.emit('msg_deleted', {'id': data['id']})
    elif data['type'] == 'edit' and msg.sender == me:
        msg.message, msg.is_edited = data['text'], True
        db.session.commit()
        socketio.emit('msg_updated', {'id': msg.id, 'text': msg.message})
    elif data['type'] == 'pin':
        db.session.add(Pin(message_id=msg.id, room=data['room']))
        db.session.commit()

@socketio.on('typing_status')
def handle_typing(data):
    emit('typing_update', {'user': session.get('user'), 'status': data['status']}, broadcast=True)

@socketio.on('search_msgs')
def handle_search(data):
    q, target = data['q'], data['target']
    results = Message.query.filter(Message.room == target, Message.message.contains(q)).all()
    emit('search_results', [m.to_dict() for m in results])

if __name__ == "__main__":
    socketio.run(app, host='0.0.0.0', port=APP_PORT)