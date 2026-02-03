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
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///chathub_v2_pro.sqlite'
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
    status = db.Column(db.String(20), default='online')  # NEW: online/away/offline
    last_seen = db.Column(db.Integer, default=lambda: int(time.time()))  # NEW
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def to_dict(self):
        return {
            "username": self.username, 
            "avatar": self.avatar, 
            "theme": self.theme,
            "status": self.status,
            "last_seen": self.last_seen
        }

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

class Archive(db.Model):
    __tablename__ = 'archives'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80))
    chat_identifier = db.Column(db.String(80))  # room name or DM partner username
    is_dm = db.Column(db.Boolean, default=False)
    archived_at = db.Column(db.Integer, default=lambda: int(time.time()))

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
            # Update status to online
            u.status = 'online'
            u.last_seen = int(time.time())
            db.session.commit()
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
    if "user" in session:
        u = User.query.get(session["user"])
        if u:
            u.status = 'offline'
            u.last_seen = int(time.time())
            db.session.commit()
    session.clear()
    return redirect(url_for("login"))

# ------------- TEMPLATES -------------
AUTH_HTML = """
<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&display=swap" rel="stylesheet">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'DM Sans', sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: #fff;
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100vh;
    overflow: hidden;
    position: relative;
}
body::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background: url("data:image/svg+xml,%3Csvg width='60' height='60' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M0 0h60v60H0z' fill='none'/%3E%3Cpath d='M30 0v60M0 30h60' stroke='%23fff' stroke-width='0.5' opacity='0.1'/%3E%3C/svg%3E");
    opacity: 0.3;
}
.container {
    background: rgba(255, 255, 255, 0.1);
    backdrop-filter: blur(20px);
    padding: 50px 40px;
    border-radius: 24px;
    width: 400px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2), 0 0 0 1px rgba(255,255,255,0.1);
    position: relative;
    z-index: 1;
    animation: slideIn 0.6s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes slideIn {
    from { opacity: 0; transform: translateY(30px); }
    to { opacity: 1; transform: translateY(0); }
}
h2 {
    font-size: 32px;
    font-weight: 700;
    margin-bottom: 10px;
    text-align: center;
    letter-spacing: -0.5px;
}
.subtitle {
    text-align: center;
    opacity: 0.8;
    margin-bottom: 30px;
    font-size: 14px;
}
.input-group {
    margin-bottom: 20px;
}
label {
    display: block;
    margin-bottom: 8px;
    font-size: 13px;
    font-weight: 500;
    opacity: 0.9;
}
input[type="text"], input[type="password"] {
    width: 100%;
    padding: 14px 16px;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.2);
    background: rgba(255, 255, 255, 0.15);
    color: #fff;
    font-size: 15px;
    font-family: 'DM Sans', sans-serif;
    transition: all 0.3s ease;
}
input[type="text"]:focus, input[type="password"]:focus {
    outline: none;
    background: rgba(255, 255, 255, 0.25);
    border-color: rgba(255, 255, 255, 0.4);
    box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.1);
}
input[type="text"]::placeholder, input[type="password"]::placeholder {
    color: rgba(255, 255, 255, 0.6);
}
input[type="file"] {
    width: 100%;
    padding: 12px;
    border-radius: 12px;
    border: 2px dashed rgba(255, 255, 255, 0.3);
    background: rgba(255, 255, 255, 0.05);
    color: #fff;
    font-size: 13px;
    cursor: pointer;
    transition: all 0.3s ease;
}
input[type="file"]:hover {
    border-color: rgba(255, 255, 255, 0.5);
    background: rgba(255, 255, 255, 0.1);
}
button {
    width: 100%;
    padding: 16px;
    background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
    border: none;
    border-radius: 12px;
    font-weight: 700;
    font-size: 16px;
    cursor: pointer;
    color: #fff;
    box-shadow: 0 4px 15px rgba(245, 87, 108, 0.4);
    transition: all 0.3s ease;
    font-family: 'DM Sans', sans-serif;
}
button:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(245, 87, 108, 0.5);
}
button:active {
    transform: translateY(0);
}
.error {
    background: rgba(248, 113, 113, 0.2);
    border: 1px solid rgba(248, 113, 113, 0.4);
    color: #fca5a5;
    padding: 12px;
    border-radius: 10px;
    margin-bottom: 20px;
    font-size: 14px;
    text-align: center;
}
.footer {
    text-align: center;
    margin-top: 25px;
    font-size: 14px;
    opacity: 0.8;
}
.footer a {
    color: #fff;
    text-decoration: none;
    font-weight: 600;
    border-bottom: 2px solid rgba(255, 255, 255, 0.3);
    transition: border-color 0.3s ease;
}
.footer a:hover {
    border-bottom-color: rgba(255, 255, 255, 0.8);
}
</style>
</head>
<body>
<div class="container">
    <h2>{{mode}}</h2>
    <p class="subtitle">Welcome to ChatHub V2</p>
    {% with m=get_flashed_messages() %}
        {% if m %}<div class="error">{{m[0]}}</div>{% endif %}
    {% endwith %}
    <form method="post" enctype="multipart/form-data">
        <div class="input-group">
            <label>Username</label>
            <input name="username" type="text" placeholder="Enter your username" required>
        </div>
        <div class="input-group">
            <label>Password</label>
            <input name="password" type="password" placeholder="Enter your password" required>
        </div>
        {% if mode == 'Register' %}
        <div class="input-group">
            <label>Avatar (Optional)</label>
            <input type="file" name="avatar" accept="image/*">
        </div>
        {% endif %}
        <button type="submit">{{mode}}</button>
    </form>
    <div class="footer">
        {% if mode == 'Login' %}
            Don't have an account? <a href="/register">Sign Up</a>
        {% else %}
            Already have an account? <a href="/login">Sign In</a>
        {% endif %}
    </div>
</div>
</body></html>
"""

MAIN_HTML = r"""
<!doctype html>
<html>
<head>
    <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=0">
    <title>ChatHub V2 Pro</title>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root { 
            --bg: {{ '#0a0e27' if user.theme=='dark' else '#022c22' if user.theme=='green' else '#f8fafc' }};
            --panel: {{ '#151b3d' if user.theme=='dark' else '#064e3b' if user.theme=='green' else '#ffffff' }};
            --panel-hover: {{ '#1e2749' if user.theme=='dark' else '#065f46' if user.theme=='green' else '#f8fafc' }};
            --text: {{ '#e2e8f0' if user.theme!='white' else '#1e293b' }};
            --text-secondary: {{ '#94a3b8' if user.theme!='white' else '#64748b' }};
            --accent: #8b5cf6;
            --accent-light: #a78bfa;
            --me: linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%);
            --other: {{ '#1e293b' if user.theme=='dark' else '#134e4a' if user.theme=='green' else '#f1f5f9' }};
            --border: {{ 'rgba(255,255,255,0.1)' if user.theme!='white' else 'rgba(0,0,0,0.1)' }};
            --shadow: {{ 'rgba(0,0,0,0.3)' if user.theme!='white' else 'rgba(0,0,0,0.08)' }};
            --online: #10b981;
            --away: #f59e0b;
            --offline: #6b7280;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body, html { 
            height: 100%; 
            font-family: 'DM Sans', sans-serif; 
            background: var(--bg); 
            color: var(--text); 
            overflow: hidden;
        }
        
        .app-container {
            display: flex;
            height: 100vh;
            position: relative;
        }
        
        /* Sidebar */
        .sidebar {
            width: 320px;
            background: var(--panel);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            position: relative;
            z-index: 10;
        }
        
        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid var(--border);
            background: linear-gradient(135deg, var(--accent) 0%, var(--accent-light) 100%);
            color: white;
        }
        
        .user-profile {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 15px;
        }
        
        .avatar {
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 20px;
            position: relative;
            border: 3px solid rgba(255,255,255,0.3);
        }
        
        .avatar img {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            object-fit: cover;
        }
        
        .status-indicator {
            width: 14px;
            height: 14px;
            border-radius: 50%;
            position: absolute;
            bottom: -2px;
            right: -2px;
            border: 3px solid var(--panel);
        }
        
        .status-indicator.online { background: var(--online); }
        .status-indicator.away { background: var(--away); }
        .status-indicator.offline { background: var(--offline); }
        
        .user-info h3 {
            font-size: 18px;
            font-weight: 700;
            margin-bottom: 4px;
        }
        
        .user-status {
            font-size: 13px;
            opacity: 0.9;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        
        .search-box {
            padding: 15px;
            border-bottom: 1px solid var(--border);
        }
        
        .search-input {
            width: 100%;
            padding: 12px 16px;
            border-radius: 12px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--text);
            font-size: 14px;
            font-family: 'DM Sans', sans-serif;
            transition: all 0.2s ease;
        }
        
        .search-input:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.1);
        }
        
        .tabs {
            display: flex;
            padding: 15px;
            gap: 10px;
            border-bottom: 1px solid var(--border);
        }
        
        .tab {
            flex: 1;
            padding: 10px;
            border-radius: 10px;
            border: none;
            background: transparent;
            color: var(--text-secondary);
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            transition: all 0.2s ease;
            font-family: 'DM Sans', sans-serif;
        }
        
        .tab.active {
            background: var(--accent);
            color: white;
        }
        
        .chat-list {
            flex: 1;
            overflow-y: auto;
            padding: 10px;
        }
        
        .chat-item {
            padding: 14px;
            margin-bottom: 6px;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 12px;
            position: relative;
        }
        
        .chat-item:hover {
            background: var(--panel-hover);
        }
        
        .chat-item.active {
            background: var(--accent);
            color: white;
        }
        
        .chat-item.archived {
            opacity: 0.5;
        }
        
        .chat-avatar {
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 16px;
            position: relative;
            flex-shrink: 0;
        }
        
        .chat-avatar img {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            object-fit: cover;
        }
        
        .chat-info {
            flex: 1;
            min-width: 0;
        }
        
        .chat-name {
            font-weight: 600;
            font-size: 15px;
            margin-bottom: 4px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .chat-preview {
            font-size: 13px;
            opacity: 0.7;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .unread-badge {
            background: #ef4444;
            color: white;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 700;
        }
        
        /* Main Chat Area */
        .main-chat {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: var(--bg);
        }
        
        .chat-header {
            padding: 20px 24px;
            border-bottom: 1px solid var(--border);
            background: var(--panel);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        
        .chat-header-left {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        
        .chat-header-actions {
            display: flex;
            gap: 10px;
        }
        
        .icon-btn {
            width: 40px;
            height: 40px;
            border-radius: 10px;
            border: none;
            background: var(--bg);
            color: var(--text);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            transition: all 0.2s ease;
        }
        
        .icon-btn:hover {
            background: var(--accent);
            color: white;
            transform: scale(1.05);
        }
        
        .messages-area {
            flex: 1;
            overflow-y: auto;
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }
        
        .message-wrapper {
            display: flex;
            gap: 12px;
            animation: messageSlide 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
        
        @keyframes messageSlide {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .message-wrapper.me {
            flex-direction: row-reverse;
        }
        
        .message-avatar {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 14px;
            flex-shrink: 0;
        }
        
        .message-avatar img {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            object-fit: cover;
        }
        
        .message-content {
            max-width: 65%;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        
        .message-wrapper.me .message-content {
            align-items: flex-end;
        }
        
        .message-bubble {
            padding: 12px 16px;
            border-radius: 18px;
            background: var(--other);
            position: relative;
            word-wrap: break-word;
            box-shadow: 0 2px 8px var(--shadow);
        }
        
        .message-wrapper.me .message-bubble {
            background: var(--me);
            color: white;
        }
        
        .message-bubble.holding {
            transform: scale(0.98);
            opacity: 0.8;
        }
        
        .sender-name {
            font-weight: 600;
            font-size: 13px;
            margin-bottom: 6px;
            opacity: 0.9;
        }
        
        .message-wrapper.me .sender-name {
            display: none;
        }
        
        .message-text {
            font-size: 15px;
            line-height: 1.5;
        }
        
        /* Message Formatting */
        .message-text strong {
            font-weight: 700;
        }
        
        .message-text em {
            font-style: italic;
        }
        
        .message-text code {
            background: rgba(0,0,0,0.2);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
        }
        
        .message-text a {
            color: #60a5fa;
            text-decoration: underline;
        }
        
        .message-img {
            max-width: 300px;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.2s ease;
            margin-top: 8px;
        }
        
        .message-img:hover {
            transform: scale(1.02);
            box-shadow: 0 4px 15px rgba(0,0,0,0.3);
        }
        
        .message-meta {
            font-size: 11px;
            opacity: 0.6;
            display: flex;
            align-items: center;
            gap: 6px;
            margin-top: 4px;
        }
        
        .reply-preview {
            font-size: 12px;
            opacity: 0.7;
            border-left: 3px solid currentColor;
            padding-left: 10px;
            margin-bottom: 8px;
            font-style: italic;
        }
        
        /* Input Area */
        .input-area {
            padding: 20px 24px;
            border-top: 1px solid var(--border);
            background: var(--panel);
        }
        
        .typing-indicator {
            font-size: 13px;
            opacity: 0.7;
            padding: 8px 0;
            min-height: 28px;
            font-style: italic;
        }
        
        .input-wrapper {
            display: flex;
            gap: 12px;
            align-items: flex-end;
        }
        
        .format-toolbar {
            display: flex;
            gap: 8px;
            margin-bottom: 10px;
        }
        
        .format-btn {
            width: 32px;
            height: 32px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--text);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            font-weight: 700;
            transition: all 0.2s ease;
            font-family: 'JetBrains Mono', monospace;
        }
        
        .format-btn:hover {
            background: var(--accent);
            color: white;
            border-color: var(--accent);
        }
        
        .input-container {
            flex: 1;
            display: flex;
            flex-direction: column;
        }
        
        .message-input {
            width: 100%;
            padding: 14px 16px;
            border-radius: 12px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--text);
            font-size: 15px;
            font-family: 'DM Sans', sans-serif;
            resize: none;
            max-height: 120px;
            transition: all 0.2s ease;
        }
        
        .message-input:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.1);
        }
        
        .send-btn {
            width: 50px;
            height: 50px;
            border-radius: 12px;
            border: none;
            background: var(--accent);
            color: white;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            transition: all 0.2s ease;
            flex-shrink: 0;
        }
        
        .send-btn:hover {
            background: var(--accent-light);
            transform: scale(1.05);
        }
        
        .send-btn:active {
            transform: scale(0.95);
        }
        
        /* Context Menu */
        .context-menu {
            position: fixed;
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 8px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.3);
            z-index: 1000;
            display: none;
            min-width: 200px;
        }
        
        .menu-item {
            padding: 10px 14px;
            border-radius: 8px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 14px;
            transition: all 0.2s ease;
        }
        
        .menu-item:hover {
            background: var(--accent);
            color: white;
        }
        
        .menu-divider {
            height: 1px;
            background: var(--border);
            margin: 6px 0;
        }
        
        /* Image Lightbox */
        .lightbox {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.95);
            z-index: 2000;
            display: none;
            align-items: center;
            justify-content: center;
            backdrop-filter: blur(10px);
        }
        
        .lightbox.active {
            display: flex;
            animation: fadeIn 0.3s ease;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }
        
        .lightbox-content {
            max-width: 90vw;
            max-height: 90vh;
            position: relative;
            animation: zoomIn 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
        
        @keyframes zoomIn {
            from { opacity: 0; transform: scale(0.8); }
            to { opacity: 1; transform: scale(1); }
        }
        
        .lightbox-img {
            max-width: 100%;
            max-height: 90vh;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.5);
        }
        
        .lightbox-close {
            position: absolute;
            top: -50px;
            right: 0;
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: rgba(255,255,255,0.1);
            border: none;
            color: white;
            font-size: 24px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
        }
        
        .lightbox-close:hover {
            background: rgba(255,255,255,0.2);
            transform: scale(1.1);
        }
        
        .lightbox-download {
            position: absolute;
            bottom: -50px;
            left: 50%;
            transform: translateX(-50%);
            padding: 12px 24px;
            border-radius: 24px;
            background: var(--accent);
            border: none;
            color: white;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            font-family: 'DM Sans', sans-serif;
        }
        
        .lightbox-download:hover {
            background: var(--accent-light);
            transform: translateX(-50%) scale(1.05);
        }
        
        /* Scrollbar */
        ::-webkit-scrollbar {
            width: 8px;
        }
        
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        
        ::-webkit-scrollbar-thumb {
            background: var(--border);
            border-radius: 4px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: var(--accent);
        }
        
        /* Mobile Responsive */
        @media (max-width: 768px) {
            .sidebar {
                position: fixed;
                left: -100%;
                top: 0;
                bottom: 0;
                width: 85%;
                transition: left 0.3s ease;
                z-index: 100;
                box-shadow: 4px 0 12px rgba(0,0,0,0.3);
            }
            
            .sidebar.open {
                left: 0;
            }
            
            .message-content {
                max-width: 85%;
            }
        }
        
        /* Encryption Badge */
        .encryption-badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 4px 8px;
            border-radius: 6px;
            background: rgba(16, 185, 129, 0.1);
            color: #10b981;
            font-size: 11px;
            font-weight: 600;
        }
    </style>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
</head>
<body>
    <div class="app-container">
        <!-- Sidebar -->
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <div class="user-profile">
                    <div class="avatar">
                        {% if user.avatar %}
                        <img src="{{ user.avatar }}" alt="Avatar">
                        {% else %}
                        {{ user.username[0].upper() }}
                        {% endif %}
                        <div class="status-indicator {{ user.status }}"></div>
                    </div>
                    <div class="user-info">
                        <h3>{{ user.username }}</h3>
                        <div class="user-status">
                            <span class="encryption-badge">🔒 Encrypted</span>
                        </div>
                    </div>
                </div>
                <div style="display:flex;gap:10px;">
                    <button class="icon-btn" onclick="window.location.href='/logout'" title="Logout">🚪</button>
                    <button class="icon-btn" onclick="toggleTheme()" title="Toggle Theme">🌓</button>
                </div>
            </div>
            
            <div class="search-box">
                <input type="text" class="search-input" id="searchChats" placeholder="Search conversations..." oninput="filterChats()">
            </div>
            
            <div class="tabs">
                <button class="tab active" onclick="switchTab('all')">All</button>
                <button class="tab" onclick="switchTab('dms')">DMs</button>
                <button class="tab" onclick="switchTab('rooms')">Rooms</button>
                <button class="tab" onclick="switchTab('archived')">Archive</button>
            </div>
            
            <div class="chat-list" id="chatList">
                <!-- Chats will be populated here -->
            </div>
            
            <div style="padding:15px;border-top:1px solid var(--border);">
                <button class="tab" style="width:100%;background:var(--accent);color:white;" onclick="createRoom()">+ New Room</button>
            </div>
        </div>
        
        <!-- Main Chat -->
        <div class="main-chat">
            <div class="chat-header">
                <div class="chat-header-left">
                    <div class="chat-avatar" id="headerAvatar">
                        <span id="headerInitial">?</span>
                    </div>
                    <div>
                        <h3 id="headerName">Select a chat</h3>
                        <div id="headerStatus" class="user-status"></div>
                    </div>
                </div>
                <div class="chat-header-actions">
                    <button class="icon-btn" onclick="archiveChat()" title="Archive">📦</button>
                    <button class="icon-btn" onclick="showSearch()" title="Search">🔍</button>
                    <button class="icon-btn" onclick="toggleSidebar()" title="Menu" style="display:none;" id="menuBtn">☰</button>
                </div>
            </div>
            
            <div class="messages-area" id="messageDisplay">
                <div style="text-align:center;opacity:0.5;margin-top:40%;">Select a conversation to start chatting</div>
            </div>
            
            <div class="input-area">
                <div class="typing-indicator" id="typing"></div>
                <div class="format-toolbar">
                    <button class="format-btn" onclick="insertFormat('**', '**')" title="Bold">B</button>
                    <button class="format-btn" onclick="insertFormat('*', '*')" title="Italic">I</button>
                    <button class="format-btn" onclick="insertFormat('`', '`')" title="Code">&lt;/&gt;</button>
                </div>
                <div class="input-wrapper">
                    <div class="input-container">
                        <textarea id="msgInput" class="message-input" placeholder="Type a message..." 
                            oninput="sendTyping(); autoResize(this);" 
                            onkeydown="if(event.key==='Enter' && !event.shiftKey){event.preventDefault();sendMessage();}"></textarea>
                    </div>
                    <input type="file" id="imgUpload" accept="image/*" style="display:none" onchange="uploadImage()">
                    <button class="icon-btn" onclick="document.getElementById('imgUpload').click()" title="Upload Image">📷</button>
                    <button class="send-btn" onclick="sendMessage()">➤</button>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Context Menu -->
    <div class="context-menu" id="actionMenu" onclick="event.stopPropagation()">
        <div class="menu-item" onclick="react('❤️'); hideMenu();">❤️ React</div>
        <div class="menu-item" onclick="react('👍'); hideMenu();">👍 Like</div>
        <div class="menu-item" onclick="react('😂'); hideMenu();">😂 Laugh</div>
        <div class="menu-divider"></div>
        <div class="menu-item" onclick="prepareReply(); hideMenu();">↩️ Reply</div>
        <div class="menu-item" onclick="copyMessage(); hideMenu();">📋 Copy</div>
        <div class="menu-divider"></div>
        <div class="menu-item" id="editBtn" onclick="prepareEdit(); hideMenu();">✏️ Edit</div>
        <div class="menu-item" id="delBtn" onclick="deleteMsg(); hideMenu();" style="color:#ef4444;">🗑️ Delete</div>
    </div>
    
    <!-- Image Lightbox -->
    <div class="lightbox" id="lightbox" onclick="closeLightbox()">
        <div class="lightbox-content" onclick="event.stopPropagation()">
            <button class="lightbox-close" onclick="closeLightbox()">×</button>
            <img id="lightboxImg" class="lightbox-img" src="">
            <button class="lightbox-download" onclick="downloadImage()">⬇ Download</button>
        </div>
    </div>

    <script>
        const socket = io();
        const myName = "{{ user.username }}";
        let currentTarget = null;
        let isDM = false;
        let activeMsgId = null;
        let holdTimer = null;
        let typingTimer = null;
        let replyToId = null;
        let allUsers = [];
        let allRooms = [];
        let archivedChats = [];
        let currentTab = 'all';

        // Initialize
        socket.emit('sync_data');
        document.addEventListener('click', hideMenu);
        
        // Check mobile
        if(window.innerWidth <= 768) {
            document.getElementById('menuBtn').style.display = 'flex';
        }

        socket.on('sync_ready', (data) => {
            allUsers = data.users;
            allRooms = data.rooms;
            loadArchived();
            renderChats();
            updateUserStatuses();
        });

        function loadArchived() {
            const stored = localStorage.getItem('archived_' + myName);
            archivedChats = stored ? JSON.parse(stored) : [];
        }

        function saveArchived() {
            localStorage.setItem('archived_' + myName, JSON.stringify(archivedChats));
        }

        function renderChats() {
            const list = document.getElementById('chatList');
            list.innerHTML = '';
            
            const dms = allUsers.filter(u => u.username !== myName);
            const rooms = allRooms;
            
            let items = [];
            
            if(currentTab === 'all' || currentTab === 'dms') {
                dms.forEach(u => {
                    if(!isArchived(u.username, true)) {
                        items.push({type: 'dm', data: u});
                    }
                });
            }
            
            if(currentTab === 'all' || currentTab === 'rooms') {
                rooms.forEach(r => {
                    if(!isArchived(r, false)) {
                        items.push({type: 'room', data: r});
                    }
                });
            }
            
            if(currentTab === 'archived') {
                archivedChats.forEach(a => {
                    if(a.is_dm) {
                        const user = allUsers.find(u => u.username === a.chat_identifier);
                        if(user) items.push({type: 'dm', data: user, archived: true});
                    } else {
                        items.push({type: 'room', data: a.chat_identifier, archived: true});
                    }
                });
            }
            
            items.forEach(item => {
                const div = document.createElement('div');
                div.className = 'chat-item' + (item.archived ? ' archived' : '');
                
                if(item.type === 'dm') {
                    const u = item.data;
                    div.innerHTML = `
                        <div class="chat-avatar">
                            ${u.avatar ? `<img src="${u.avatar}">` : u.username[0].toUpperCase()}
                            <div class="status-indicator ${u.status}"></div>
                        </div>
                        <div class="chat-info">
                            <div class="chat-name">${u.username}</div>
                            <div class="chat-preview">${getStatusText(u.status, u.last_seen)}</div>
                        </div>
                    `;
                    div.onclick = () => join(u.username, true);
                } else {
                    const r = item.data;
                    div.innerHTML = `
                        <div class="chat-avatar">#</div>
                        <div class="chat-info">
                            <div class="chat-name">${r}</div>
                            <div class="chat-preview">Group chat</div>
                        </div>
                    `;
                    div.onclick = () => join(r, false);
                }
                
                list.appendChild(div);
            });
        }

        function isArchived(identifier, is_dm) {
            return archivedChats.some(a => a.chat_identifier === identifier && a.is_dm === is_dm);
        }

        function archiveChat() {
            if(!currentTarget) return;
            
            const exists = archivedChats.findIndex(a => a.chat_identifier === currentTarget && a.is_dm === isDM);
            
            if(exists >= 0) {
                archivedChats.splice(exists, 1);
                alert('Chat unarchived!');
            } else {
                archivedChats.push({chat_identifier: currentTarget, is_dm: isDM});
                alert('Chat archived!');
            }
            
            saveArchived();
            renderChats();
        }

        function switchTab(tab) {
            currentTab = tab;
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            renderChats();
        }

        function filterChats() {
            const query = document.getElementById('searchChats').value.toLowerCase();
            document.querySelectorAll('.chat-item').forEach(item => {
                const name = item.querySelector('.chat-name').textContent.toLowerCase();
                item.style.display = name.includes(query) ? 'flex' : 'none';
            });
        }

        function getStatusText(status, lastSeen) {
            if(status === 'online') return 'Online';
            if(status === 'away') return 'Away';
            const diff = Math.floor((Date.now()/1000) - lastSeen);
            if(diff < 60) return 'Just now';
            if(diff < 3600) return Math.floor(diff/60) + 'm ago';
            if(diff < 86400) return Math.floor(diff/3600) + 'h ago';
            return Math.floor(diff/86400) + 'd ago';
        }

        function updateUserStatuses() {
            setInterval(() => {
                socket.emit('sync_data');
            }, 30000);
        }

        function join(target, is_dm) {
            currentTarget = target;
            isDM = is_dm;
            socket.emit('join_chat', {target, isDM: is_dm});
            
            // Update header
            const header = document.getElementById('headerName');
            const avatar = document.getElementById('headerAvatar');
            const status = document.getElementById('headerStatus');
            const initial = document.getElementById('headerInitial');
            
            header.textContent = target;
            initial.textContent = target[0].toUpperCase();
            
            if(is_dm) {
                const user = allUsers.find(u => u.username === target);
                if(user) {
                    if(user.avatar) {
                        avatar.innerHTML = `<img src="${user.avatar}"><div class="status-indicator ${user.status}"></div>`;
                    } else {
                        avatar.innerHTML = `${target[0].toUpperCase()}<div class="status-indicator ${user.status}"></div>`;
                    }
                    status.textContent = getStatusText(user.status, user.last_seen);
                    socket.emit('mark_read', {sender: target});
                }
            } else {
                avatar.innerHTML = '#';
                status.textContent = 'Group chat';
            }
            
            // Update active state
            document.querySelectorAll('.chat-item').forEach(c => c.classList.remove('active'));
            event?.target?.closest('.chat-item')?.classList.add('active');
            
            // Close sidebar on mobile
            if(window.innerWidth <= 768) {
                document.getElementById('sidebar').classList.remove('open');
            }
        }

        socket.on('load_history', (msgs) => {
            document.getElementById('messageDisplay').innerHTML = '';
            msgs.forEach(renderBubble);
        });

        socket.on('msg_arrival', (m) => {
            if(isDM && (m.sender === currentTarget || m.receiver === currentTarget)) {
                renderBubble(m);
                if(m.sender === currentTarget) {
                    socket.emit('mark_read', {sender: currentTarget});
                }
            } else if(!isDM && m.room === currentTarget) {
                renderBubble(m);
            }
            const d = document.getElementById('messageDisplay');
            d.scrollTop = d.scrollHeight;
        });

        function renderBubble(m) {
            const display = document.getElementById('messageDisplay');
            const wrapper = document.createElement('div');
            wrapper.className = `message-wrapper ${m.sender === myName ? 'me' : 'other'}`;
            wrapper.id = `msg-${m.id}`;
            
            wrapper.onmousedown = (e) => startHold(e, m.id, m.sender);
            wrapper.ontouchstart = (e) => startHold(e, m.id, m.sender);
            wrapper.onmouseup = endHold;
            wrapper.ontouchend = endHold;

            const avatarUser = allUsers.find(u => u.username === m.sender);
            const avatarHTML = avatarUser?.avatar 
                ? `<img src="${avatarUser.avatar}">` 
                : m.sender[0].toUpperCase();

            let content = '';
            if(m.sender !== myName) {
                content += `<span class="sender-name">${m.sender}</span>`;
            }
            if(m.reply_to) {
                content += `<div class="reply-preview">Replying to message #${m.reply_to}</div>`;
            }
            if(m.message) {
                content += `<div class="message-text">${formatMessage(m.message)}</div>`;
            }
            if(m.image) {
                content += `<img src="${m.image}" class="message-img" onclick="openLightbox('${m.image}')">`;
            }
            
            const tickColor = m.is_read ? 'color:#60a5fa;' : 'opacity:0.4;';
            const ticks = (m.sender === myName && isDM) ? `<span id="tick-${m.id}" style="${tickColor}">✓✓</span>` : '';
            
            const time = new Date(m.timestamp*1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
            content += `<div class="message-meta">${m.is_edited ? '(edited) ' : ''} ${time} ${ticks}</div>`;
            
            wrapper.innerHTML = `
                <div class="message-avatar">${avatarHTML}</div>
                <div class="message-content">
                    <div class="message-bubble">${content}</div>
                </div>
            `;
            
            display.appendChild(wrapper);
            display.scrollTop = display.scrollHeight;
        }

        function formatMessage(text) {
            // Bold: **text**
            text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
            // Italic: *text*
            text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
            // Code: `text`
            text = text.replace(/`(.+?)`/g, '<code>$1</code>');
            // Links
            text = text.replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank">$1</a>');
            return text;
        }

        function insertFormat(before, after) {
            const input = document.getElementById('msgInput');
            const start = input.selectionStart;
            const end = input.selectionEnd;
            const text = input.value;
            const selected = text.substring(start, end);
            
            input.value = text.substring(0, start) + before + selected + after + text.substring(end);
            input.focus();
            input.selectionStart = input.selectionEnd = start + before.length + selected.length;
        }

        function autoResize(textarea) {
            textarea.style.height = 'auto';
            textarea.style.height = textarea.scrollHeight + 'px';
        }

        function sendMessage() {
            const input = document.getElementById('msgInput');
            const text = input.value.trim();
            if(!text || !currentTarget) return;
            
            socket.emit('new_msg', {
                text, 
                target: currentTarget, 
                isDM, 
                replyTo: replyToId
            });
            
            input.value = '';
            input.style.height = 'auto';
            replyToId = null;
            input.placeholder = 'Type a message...';
        }

        function uploadImage() {
            const file = document.getElementById('imgUpload').files[0];
            if(!file) return;
            
            const reader = new FileReader();
            reader.onload = (e) => {
                socket.emit('new_msg', {
                    img: e.target.result,
                    target: currentTarget,
                    isDM
                });
            };
            reader.readAsDataURL(file);
        }

        // Context Menu
        function startHold(e, id, sender) {
            activeMsgId = id;
            document.getElementById(`msg-${id}`)?.classList.add('holding');
            holdTimer = setTimeout(() => showMenu(e, sender === myName), 600);
        }

        function endHold() {
            clearTimeout(holdTimer);
            document.querySelectorAll('.message-wrapper').forEach(el => el.classList.remove('holding'));
        }

        function showMenu(e, mine) {
            const menu = document.getElementById('actionMenu');
            const x = e.clientX || (e.touches ? e.touches[0].clientX : 0);
            const y = e.clientY || (e.touches ? e.touches[0].clientY : 0);
            menu.style.left = Math.min(x, window.innerWidth - 210) + "px";
            menu.style.top = Math.min(y, window.innerHeight - 300) + "px";
            menu.style.display = "block";
            document.getElementById('editBtn').style.display = mine ? 'flex' : 'none';
            document.getElementById('delBtn').style.display = mine ? 'flex' : 'none';
            if(navigator.vibrate) navigator.vibrate(40);
        }

        function hideMenu() {
            document.getElementById('actionMenu').style.display = 'none';
        }

        function react(emoji) {
            socket.emit('msg_action', {type:'react', id:activeMsgId, emoji});
        }

        function prepareReply() {
            replyToId = activeMsgId;
            document.getElementById('msgInput').placeholder = `Replying to #${activeMsgId}...`;
            document.getElementById('msgInput').focus();
        }

        function copyMessage() {
            const msgEl = document.getElementById(`msg-${activeMsgId}`);
            const text = msgEl?.querySelector('.message-text')?.textContent;
            if(text) {
                navigator.clipboard.writeText(text);
                alert('Message copied!');
            }
        }

        function prepareEdit() {
            const t = prompt("Edit message:");
            if(t) socket.emit('msg_action', {type:'edit', id:activeMsgId, text:t});
        }

        function deleteMsg() {
            if(confirm("Delete this message?")) {
                socket.emit('msg_action', {type:'delete', id:activeMsgId});
            }
        }

        socket.on('msg_deleted', (d) => {
            document.getElementById(`msg-${d.id}`)?.remove();
        });

        socket.on('msg_updated', (d) => {
            const el = document.getElementById(`msg-${d.id}`);
            if(el) {
                const textEl = el.querySelector('.message-text');
                if(textEl) textEl.innerHTML = formatMessage(d.text);
            }
        });

        socket.on('read_notification', (data) => {
            if(data.sender === myName && currentTarget === data.reader) {
                document.querySelectorAll('[id^="tick-"]').forEach(t => {
                    t.style.color = '#60a5fa';
                    t.style.opacity = '1';
                });
            }
        });

        // Typing
        function sendTyping() {
            if(!currentTarget) return;
            socket.emit('typing_status', {target: currentTarget, isDM, status: true});
            if(typingTimer) clearTimeout(typingTimer);
            typingTimer = setTimeout(() => {
                socket.emit('typing_status', {target: currentTarget, isDM, status: false});
            }, 2000);
        }

        socket.on('typing_update', (d) => {
            const el = document.getElementById('typing');
            el.textContent = (d.status && d.user !== myName) ? d.user + " is typing..." : "";
        });

        // Lightbox
        function openLightbox(src) {
            document.getElementById('lightboxImg').src = src;
            document.getElementById('lightbox').classList.add('active');
        }

        function closeLightbox() {
            document.getElementById('lightbox').classList.remove('active');
        }

        function downloadImage() {
            const img = document.getElementById('lightboxImg');
            const link = document.createElement('a');
            link.href = img.src;
            link.download = 'image_' + Date.now();
            link.click();
        }

        function createRoom() {
            const name = prompt("Enter room name:");
            if(name) join(name, false);
        }

        function showSearch() {
            const q = prompt("Search messages:");
            if(q) {
                socket.emit('search_msgs', {q, target: currentTarget});
            }
        }

        socket.on('search_results', (msgs) => {
            document.getElementById('messageDisplay').innerHTML = "<div style='text-align:center;opacity:0.5;padding:20px;'>Search Results</div>";
            msgs.forEach(renderBubble);
        });

        function toggleTheme() {
            window.location.href = '/logout';
        }

        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('open');
        }

        // Update status on visibility change
        document.addEventListener('visibilitychange', () => {
            if(document.hidden) {
                socket.emit('status_update', {status: 'away'});
            } else {
                socket.emit('status_update', {status: 'online'});
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
        socketio.emit('msg_arrival', msg_dict)
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
    elif data['type'] == 'react':
        db.session.add(Reaction(message_id=msg.id, username=me, emoji=data['emoji']))
        db.session.commit()

@socketio.on('typing_status')
def handle_typing(data):
    emit('typing_update', {'user': session.get('user'), 'status': data['status']}, broadcast=True)

@socketio.on('search_msgs')
def handle_search(data):
    q, target = data['q'], data['target']
    results = Message.query.filter(Message.room == target, Message.message.contains(q)).all()
    emit('search_results', [m.to_dict() for m in results])

@socketio.on('status_update')
def handle_status_update(data):
    me = session.get('user')
    user = User.query.get(me)
    if user:
        user.status = data['status']
        user.last_seen = int(time.time())
        db.session.commit()
        socketio.emit('sync_data')

if __name__ == "__main__":
    socketio.run(app, host='0.0.0.0', port=APP_PORT)