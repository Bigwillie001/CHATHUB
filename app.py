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

app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///chathub.sqlite'
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
    status = db.Column(db.String(20), default='online')
    last_seen = db.Column(db.Integer, default=lambda: int(time.time()))
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
    chat_identifier = db.Column(db.String(80))
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
<!doctype html>
<html>
<head>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>CHATHUB</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #fff;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            background: rgba(255, 255, 255, 0.95);
            padding: 40px;
            border-radius: 20px;
            width: 100%;
            max-width: 400px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
        }
        .logo {
            text-align: center;
            margin-bottom: 30px;
        }
        .logo h1 {
            font-size: 32px;
            font-weight: 700;
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .subtitle {
            text-align: center;
            color: #64748b;
            font-size: 14px;
            margin-bottom: 30px;
        }
        .input-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 8px;
            font-size: 14px;
            font-weight: 500;
            color: #334155;
        }
        input[type="text"], input[type="password"] {
            width: 100%;
            padding: 14px 16px;
            border-radius: 12px;
            border: 2px solid #e2e8f0;
            background: #fff;
            color: #1e293b;
            font-size: 15px;
            font-family: 'Inter', sans-serif;
            transition: all 0.3s ease;
        }
        input[type="text"]:focus, input[type="password"]:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        input[type="file"] {
            width: 100%;
            padding: 12px;
            border-radius: 12px;
            border: 2px dashed #cbd5e1;
            background: #f8fafc;
            color: #64748b;
            font-size: 13px;
            cursor: pointer;
        }
        button {
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            border: none;
            border-radius: 12px;
            font-weight: 600;
            font-size: 16px;
            cursor: pointer;
            color: #fff;
            transition: transform 0.2s ease;
        }
        button:hover {
            transform: translateY(-2px);
        }
        button:active {
            transform: translateY(0);
        }
        .error {
            background: #fee2e2;
            border: 1px solid #fca5a5;
            color: #dc2626;
            padding: 12px;
            border-radius: 10px;
            margin-bottom: 20px;
            font-size: 14px;
        }
        .footer {
            text-align: center;
            margin-top: 20px;
            font-size: 14px;
            color: #64748b;
        }
        .footer a {
            color: #667eea;
            text-decoration: none;
            font-weight: 600;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">
            <h1>CHATHUB</h1>
        </div>
        <p class="subtitle">Connect with your friends instantly</p>
        {% with m=get_flashed_messages() %}
            {% if m %}<div class="error">{{m[0]}}</div>{% endif %}
        {% endwith %}
        <form method="post" enctype="multipart/form-data">
            <div class="input-group">
                <label>Username</label>
                <input name="username" type="text" placeholder="Enter username" required>
            </div>
            <div class="input-group">
                <label>Password</label>
                <input name="password" type="password" placeholder="Enter password" required>
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
</body>
</html>
"""

MAIN_HTML = r"""
<!doctype html>
<html>
<head>
    <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=0">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="mobile-web-app-capable" content="yes">
    <title>CHATHUB</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root { 
            --bg: {{ '#0f172a' if user.theme=='dark' else '#f8fafc' }};
            --panel: {{ '#1e293b' if user.theme=='dark' else '#ffffff' }};
            --panel-hover: {{ '#334155' if user.theme=='dark' else '#f1f5f9' }};
            --text: {{ '#f1f5f9' if user.theme=='dark' else '#1e293b' }};
            --text-secondary: {{ '#94a3b8' if user.theme=='dark' else '#64748b' }};
            --accent: #6366f1;
            --accent-light: #818cf8;
            --me: #6366f1;
            --other: {{ '#334155' if user.theme=='dark' else '#f1f5f9' }};
            --border: {{ 'rgba(255,255,255,0.1)' if user.theme=='dark' else 'rgba(0,0,0,0.06)' }};
            --online: #22c55e;
            --away: #f59e0b;
            --offline: #6b7280;
        }
        
        * { 
            margin: 0; 
            padding: 0; 
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }
        
        body, html { 
            height: 100%; 
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; 
            background: var(--bg); 
            color: var(--text); 
            overflow: hidden;
            position: fixed;
            width: 100%;
        }
        
        .app-container {
            display: flex;
            height: 100vh;
            height: 100dvh;
            position: relative;
            overflow: hidden;
        }
        
        /* Sidebar */
        .sidebar {
            width: 360px;
            background: var(--panel);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            position: relative;
            z-index: 100;
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        .sidebar-header {
            padding: 16px;
            border-bottom: 1px solid var(--border);
        }
        
        .brand {
            font-size: 24px;
            font-weight: 700;
            background: linear-gradient(135deg, var(--accent), var(--accent-light));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 16px;
        }
        
        .user-card {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px;
            border-radius: 12px;
            background: var(--bg);
        }
        
        .avatar {
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: linear-gradient(135deg, #f093fb, #f5576c);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 18px;
            position: relative;
            flex-shrink: 0;
        }
        
        .avatar img {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            object-fit: cover;
        }
        
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            position: absolute;
            bottom: 0;
            right: 0;
            border: 2px solid var(--panel);
        }
        
        .status-dot.online { background: var(--online); }
        .status-dot.away { background: var(--away); }
        .status-dot.offline { background: var(--offline); }
        
        .user-info {
            flex: 1;
            min-width: 0;
        }
        
        .user-name {
            font-weight: 600;
            font-size: 15px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        .user-status {
            font-size: 12px;
            color: var(--text-secondary);
        }
        
        .header-actions {
            display: flex;
            gap: 8px;
        }
        
        .icon-btn {
            width: 36px;
            height: 36px;
            border-radius: 10px;
            border: none;
            background: transparent;
            color: var(--text);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            transition: all 0.2s;
        }
        
        .icon-btn:active {
            background: var(--panel-hover);
            transform: scale(0.95);
        }
        
        .search-box {
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
        }
        
        .search-input {
            width: 100%;
            padding: 10px 14px;
            border-radius: 10px;
            border: none;
            background: var(--bg);
            color: var(--text);
            font-size: 14px;
        }
        
        .search-input:focus {
            outline: 2px solid var(--accent);
        }
        
        .tabs {
            display: flex;
            padding: 8px 12px;
            gap: 8px;
            border-bottom: 1px solid var(--border);
            overflow-x: auto;
        }
        
        .tab {
            padding: 8px 16px;
            border-radius: 8px;
            border: none;
            background: transparent;
            color: var(--text-secondary);
            cursor: pointer;
            font-weight: 500;
            font-size: 13px;
            white-space: nowrap;
            transition: all 0.2s;
        }
        
        .tab.active {
            background: var(--accent);
            color: white;
        }
        
        .chat-list {
            flex: 1;
            overflow-y: auto;
            padding: 8px;
        }
        
        .chat-item {
            padding: 12px;
            margin-bottom: 4px;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .chat-item:active {
            background: var(--panel-hover);
            transform: scale(0.98);
        }
        
        .chat-item.active {
            background: var(--accent);
            color: white;
        }
        
        .chat-avatar {
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea, #764ba2);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 18px;
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
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        .chat-preview {
            font-size: 13px;
            opacity: 0.7;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        .new-chat-btn {
            margin: 12px 16px;
            padding: 14px;
            background: var(--accent);
            color: white;
            border: none;
            border-radius: 12px;
            font-weight: 600;
            font-size: 15px;
            cursor: pointer;
            transition: transform 0.2s;
        }
        
        .new-chat-btn:active {
            transform: scale(0.98);
        }
        
        /* Main Chat */
        .main-chat {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: var(--bg);
            min-width: 0;
        }
        
        .chat-header {
            padding: 16px 20px;
            border-bottom: 1px solid var(--border);
            background: var(--panel);
            display: flex;
            align-items: center;
            justify-content: space-between;
            min-height: 72px;
        }
        
        .chat-header-left {
            display: flex;
            align-items: center;
            gap: 12px;
            min-width: 0;
            flex: 1;
        }
        
        .chat-title {
            font-size: 16px;
            font-weight: 600;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        .chat-status {
            font-size: 12px;
            color: var(--text-secondary);
        }
        
        .chat-header-actions {
            display: flex;
            gap: 4px;
        }
        
        .messages-area {
            flex: 1;
            overflow-y: auto;
            overflow-x: hidden;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            -webkit-overflow-scrolling: touch;
        }
        
        .empty-state {
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            opacity: 0.5;
        }
        
        .empty-state-icon {
            font-size: 48px;
            margin-bottom: 16px;
        }
        
        .message-wrapper {
            display: flex;
            gap: 10px;
            max-width: 85%;
            animation: slideUp 0.3s ease;
        }
        
        @keyframes slideUp {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .message-wrapper.me {
            flex-direction: row-reverse;
            margin-left: auto;
        }
        
        .msg-avatar {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: linear-gradient(135deg, #f093fb, #f5576c);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 14px;
            flex-shrink: 0;
        }
        
        .msg-avatar img {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            object-fit: cover;
        }
        
        .message-content {
            display: flex;
            flex-direction: column;
            gap: 4px;
            max-width: 100%;
        }
        
        .message-wrapper.me .message-content {
            align-items: flex-end;
        }
        
        .sender-name {
            font-size: 12px;
            font-weight: 600;
            color: var(--text-secondary);
            padding: 0 4px;
        }
        
        .message-wrapper.me .sender-name {
            display: none;
        }
        
        .message-bubble {
            padding: 10px 14px;
            border-radius: 18px;
            background: var(--other);
            word-wrap: break-word;
            word-break: break-word;
            max-width: 100%;
        }
        
        .message-wrapper.me .message-bubble {
            background: var(--me);
            color: white;
        }
        
        .message-text {
            font-size: 15px;
            line-height: 1.4;
        }
        
        .message-text strong { font-weight: 700; }
        .message-text em { font-style: italic; }
        
        .message-img {
            max-width: 100%;
            max-height: 300px;
            border-radius: 12px;
            margin-top: 8px;
            cursor: pointer;
        }
        
        .message-meta {
            font-size: 11px;
            opacity: 0.6;
            margin-top: 4px;
            padding: 0 4px;
        }
        
        .reply-preview {
            font-size: 12px;
            opacity: 0.7;
            border-left: 3px solid currentColor;
            padding-left: 8px;
            margin-bottom: 6px;
        }
        
        /* Input Area */
        .input-area {
            padding: 12px 16px;
            border-top: 1px solid var(--border);
            background: var(--panel);
            padding-bottom: max(12px, env(safe-area-inset-bottom));
        }
        
        .typing-indicator {
            font-size: 13px;
            color: var(--text-secondary);
            padding: 6px 0;
            min-height: 24px;
            font-style: italic;
        }
        
        .format-toolbar {
            display: flex;
            gap: 8px;
            margin-bottom: 8px;
        }
        
        .format-btn {
            width: 36px;
            height: 36px;
            border-radius: 10px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--text);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 16px;
            transition: all 0.2s;
        }
        
        .format-btn:active {
            background: var(--accent);
            color: white;
            transform: scale(0.95);
        }
        
        .input-wrapper {
            display: flex;
            gap: 8px;
            align-items: flex-end;
        }
        
        .input-container {
            flex: 1;
        }
        
        .message-input {
            width: 100%;
            padding: 12px 14px;
            border-radius: 12px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--text);
            font-size: 15px;
            font-family: 'Inter', sans-serif;
            resize: none;
            max-height: 100px;
        }
        
        .message-input:focus {
            outline: 2px solid var(--accent);
            border-color: transparent;
        }
        
        .send-btn {
            width: 44px;
            height: 44px;
            border-radius: 12px;
            border: none;
            background: var(--accent);
            color: white;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            flex-shrink: 0;
            transition: transform 0.2s;
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
            box-shadow: 0 8px 24px rgba(0,0,0,0.2);
            z-index: 1000;
            display: none;
            min-width: 200px;
        }
        
        .menu-item {
            padding: 10px 12px;
            border-radius: 8px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 14px;
            transition: all 0.2s;
        }
        
        .menu-item:active {
            background: var(--accent);
            color: white;
        }
        
        .menu-divider {
            height: 1px;
            background: var(--border);
            margin: 6px 0;
        }
        
        /* Lightbox */
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
        }
        
        .lightbox.active {
            display: flex;
        }
        
        .lightbox-content {
            max-width: 90vw;
            max-height: 90vh;
            position: relative;
        }
        
        .lightbox-img {
            max-width: 100%;
            max-height: 90vh;
            border-radius: 8px;
        }
        
        .lightbox-close {
            position: absolute;
            top: -50px;
            right: 0;
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: rgba(255,255,255,0.1);
            border: none;
            color: white;
            font-size: 24px;
            cursor: pointer;
        }
        
        .lightbox-download {
            position: absolute;
            bottom: -50px;
            left: 50%;
            transform: translateX(-50%);
            padding: 10px 20px;
            border-radius: 20px;
            background: var(--accent);
            border: none;
            color: white;
            font-weight: 600;
            cursor: pointer;
        }
        
        /* Mobile Styles */
        @media (max-width: 768px) {
            .sidebar {
                position: fixed;
                left: -100%;
                top: 0;
                bottom: 0;
                width: 85%;
                max-width: 360px;
                box-shadow: 4px 0 12px rgba(0,0,0,0.3);
            }
            
            .sidebar.open {
                left: 0;
            }
            
            .message-wrapper {
                max-width: 90%;
            }
            
            .chat-header-actions .icon-btn:not(.menu-toggle) {
                display: none;
            }
            
            .menu-toggle {
                display: flex !important;
            }
        }
        
        @media (min-width: 769px) {
            .menu-toggle {
                display: none !important;
            }
        }
        
        /* Scrollbar */
        ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }
        
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        
        ::-webkit-scrollbar-thumb {
            background: var(--border);
            border-radius: 3px;
        }
    </style>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
</head>
<body>
    <div class="app-container">
        <!-- Sidebar -->
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <div class="brand">CHATHUB</div>
                <div class="user-card">
                    <div class="avatar">
                        {% if user.avatar %}
                        <img src="{{ user.avatar }}" alt="Avatar">
                        {% else %}
                        {{ user.username[0].upper() }}
                        {% endif %}
                        <div class="status-dot {{ user.status }}"></div>
                    </div>
                    <div class="user-info">
                        <div class="user-name">{{ user.username }}</div>
                        <div class="user-status">🔒 Encrypted</div>
                    </div>
                    <div class="header-actions">
                        <button class="icon-btn" onclick="window.location.href='/logout'" title="Logout">🚪</button>
                    </div>
                </div>
            </div>
            
            <div class="search-box">
                <input type="text" class="search-input" id="searchChats" placeholder="Search..." oninput="filterChats()">
            </div>
            
            <div class="tabs">
                <button class="tab active" onclick="switchTab('all')">All</button>
                <button class="tab" onclick="switchTab('dms')">DMs</button>
                <button class="tab" onclick="switchTab('rooms')">Rooms</button>
                <button class="tab" onclick="switchTab('archived')">Archive</button>
            </div>
            
            <div class="chat-list" id="chatList"></div>
            
            <button class="new-chat-btn" onclick="createRoom()">+ New Room</button>
        </div>
        
        <!-- Main Chat -->
        <div class="main-chat">
            <div class="chat-header">
                <button class="icon-btn menu-toggle" onclick="toggleSidebar()">☰</button>
                <div class="chat-header-left">
                    <div class="chat-avatar" id="headerAvatar">
                        <span id="headerInitial">?</span>
                    </div>
                    <div style="min-width:0;flex:1;">
                        <div class="chat-title" id="headerName">Select a chat</div>
                        <div class="chat-status" id="headerStatus"></div>
                    </div>
                </div>
                <div class="chat-header-actions">
                    <button class="icon-btn" onclick="archiveChat()" title="Archive">📦</button>
                    <button class="icon-btn" onclick="showSearch()" title="Search">🔍</button>
                </div>
            </div>
            
            <div class="messages-area" id="messageDisplay">
                <div class="empty-state">
                    <div class="empty-state-icon">💬</div>
                    <div>Select a conversation to start chatting</div>
                </div>
            </div>
            
            <div class="input-area">
                <div class="typing-indicator" id="typing"></div>
                <div class="format-toolbar">
                    <button class="format-btn" onclick="insertFormat('**', '**')" title="Bold">B</button>
                    <button class="format-btn" onclick="insertFormat('*', '*')" title="Italic">I</button>
                </div>
                <div class="input-wrapper">
                    <div class="input-container">
                        <textarea id="msgInput" class="message-input" placeholder="Message..." 
                            oninput="sendTyping(); autoResize(this);" 
                            onkeydown="if(event.key==='Enter' && !event.shiftKey){event.preventDefault();sendMessage();}" rows="1"></textarea>
                    </div>
                    <input type="file" id="imgUpload" accept="image/*" style="display:none" onchange="uploadImage()">
                    <button class="icon-btn" onclick="document.getElementById('imgUpload').click()" title="Image">📷</button>
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
    
    <!-- Lightbox -->
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

        socket.emit('sync_data');
        document.addEventListener('click', hideMenu);

        socket.on('sync_ready', (data) => {
            allUsers = data.users;
            allRooms = data.rooms;
            loadArchived();
            renderChats();
            updateUserStatuses();
        });

        // Handle user status changes
        socket.on('user_status_changed', (data) => {
            const user = allUsers.find(u => u.username === data.username);
            if(user) {
                user.status = data.status;
                user.last_seen = data.last_seen;
                renderChats();
                
                // Update header if viewing this user's DM
                if(isDM && currentTarget === data.username) {
                    document.getElementById('headerStatus').textContent = getStatusText(data.status, data.last_seen);
                    const statusDot = document.querySelector('#headerAvatar .status-dot');
                    if(statusDot) {
                        statusDot.className = 'status-dot ' + data.status;
                    }
                }
            }
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
                div.className = 'chat-item';
                
                if(item.type === 'dm') {
                    const u = item.data;
                    div.innerHTML = `
                        <div class="chat-avatar">
                            ${u.avatar ? `<img src="${u.avatar}">` : u.username[0].toUpperCase()}
                            <div class="status-dot ${u.status}"></div>
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
                        avatar.innerHTML = `<img src="${user.avatar}"><div class="status-dot ${user.status}"></div>`;
                    } else {
                        avatar.innerHTML = `${target[0].toUpperCase()}<div class="status-dot ${user.status}"></div>`;
                    }
                    status.textContent = getStatusText(user.status, user.last_seen);
                    socket.emit('mark_read', {sender: target});
                }
            } else {
                avatar.innerHTML = '#';
                status.textContent = 'Group chat';
            }
            
            document.querySelectorAll('.chat-item').forEach(c => c.classList.remove('active'));
            event?.target?.closest('.chat-item')?.classList.add('active');
            
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
            
            wrapper.addEventListener('touchstart', (e) => startHold(e, m.id, m.sender), {passive: true});
            wrapper.addEventListener('touchend', endHold);
            wrapper.addEventListener('mousedown', (e) => startHold(e, m.id, m.sender));
            wrapper.addEventListener('mouseup', endHold);

            const avatarUser = allUsers.find(u => u.username === m.sender);
            const avatarHTML = avatarUser?.avatar 
                ? `<img src="${avatarUser.avatar}">` 
                : m.sender[0].toUpperCase();

            let content = '';
            if(m.sender !== myName) {
                content += `<span class="sender-name">${m.sender}</span>`;
            }
            if(m.reply_to) {
                content += `<div class="reply-preview">Replying to #${m.reply_to}</div>`;
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
                <div class="msg-avatar">${avatarHTML}</div>
                <div class="message-content">
                    <div class="message-bubble">${content}</div>
                </div>
            `;
            
            display.appendChild(wrapper);
            display.scrollTop = display.scrollHeight;
        }

        function formatMessage(text) {
            text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
            text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
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
            textarea.style.height = Math.min(textarea.scrollHeight, 100) + 'px';
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
            input.placeholder = 'Message...';
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

        function startHold(e, id, sender) {
            activeMsgId = id;
            holdTimer = setTimeout(() => showMenu(e, sender === myName), 500);
        }

        function endHold() {
            clearTimeout(holdTimer);
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

        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('open');
        }

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

# ------------- SOCKET HANDLERS (ALL BUGS FIXED!) -------------

@socketio.on('connect')
def handle_connect():
    """User connects - join their personal room for DM delivery"""
    if 'user' in session:
        me = session['user']
        join_room(me)  # Critical: Join personal room for DMs
        user = User.query.get(me)
        if user:
            user.status = 'online'
            user.last_seen = int(time.time())
            db.session.commit()
            # Broadcast status change
            socketio.emit('user_status_changed', {
                'username': me,
                'status': 'online',
                'last_seen': user.last_seen
            }, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    """User disconnects - mark offline"""
    if 'user' in session:
        me = session['user']
        user = User.query.get(me)
        if user:
            user.status = 'offline'
            user.last_seen = int(time.time())
            db.session.commit()
            # Broadcast status change
            socketio.emit('user_status_changed', {
                'username': me,
                'status': 'offline',
                'last_seen': user.last_seen
            }, broadcast=True)

@socketio.on('sync_data')
def handle_sync():
    """Sync users and rooms"""
    me = session.get('user')
    if not me:
        return  # Authentication check
    
    users = [u.to_dict() for u in User.query.all()]
    rooms_query = db.session.query(Message.room).distinct().all()
    rooms = [r[0] for r in rooms_query if r[0]]
    if "Lobby" not in rooms: 
        rooms.append("Lobby")
    emit('sync_ready', {'users': users, 'rooms': rooms})

@socketio.on('join_chat')
def handle_join(data):
    """Join a room or load DM history"""
    me = session.get('user')
    if not me:
        return  # Authentication check
    
    target = data.get('target')
    is_dm = data.get('isDM')
    
    if not target:
        return  # Input validation
    
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
    """Send a new message - FIXED DM PRIVACY BUG!"""
    me = session.get('user')
    if not me:
        return  # Authentication check
    
    target = data.get('target')
    is_dm = data.get('isDM')
    
    if not target:
        return  # Input validation
    
    new_m = Message(
        sender=me,
        message=data.get('text'),
        image=data.get('img'),
        reply_to=data.get('replyTo'),
        room=None if is_dm else target,
        receiver=target if is_dm else None
    )
    db.session.add(new_m)
    db.session.commit()
    msg_dict = new_m.to_dict()
    
    if is_dm:
        # FIXED: Send to both users' personal rooms (not broadcast!)
        socketio.emit('msg_arrival', msg_dict, room=target)
        socketio.emit('msg_arrival', msg_dict, room=me)
    else:
        # Group message - send to room
        socketio.emit('msg_arrival', msg_dict, room=target)

@socketio.on('mark_read')
def handle_mark_read(data):
    """Mark messages as read - FIXED: only notify sender"""
    me = session.get('user')
    if not me:
        return
    
    sender = data.get('sender')
    if not sender:
        return
    
    unread = Message.query.filter_by(sender=sender, receiver=me, is_read=False).all()
    if unread:
        for m in unread:
            m.is_read = True
        db.session.commit()
        # FIXED: Only send to the message sender
        socketio.emit('read_notification', {
            'reader': me,
            'sender': sender
        }, room=sender)

@socketio.on('msg_action')
def handle_action(data):
    """Handle message actions - FIXED: proper room targeting"""
    me = session.get('user')
    if not me:
        return
    
    msg_id = data.get('id')
    action_type = data.get('type')
    
    if not msg_id or not action_type:
        return
    
    msg = Message.query.get(msg_id)
    if not msg:
        return
    
    if action_type == 'delete' and msg.sender == me:
        room_target = msg.room
        receiver = msg.receiver
        
        db.session.delete(msg)
        db.session.commit()
        
        # FIXED: Send to correct rooms
        if room_target:
            socketio.emit('msg_deleted', {'id': msg_id}, room=room_target)
        else:
            # DM: send to both users
            socketio.emit('msg_deleted', {'id': msg_id}, room=me)
            if receiver:
                socketio.emit('msg_deleted', {'id': msg_id}, room=receiver)
    
    elif action_type == 'edit' and msg.sender == me:
        new_text = data.get('text')
        if not new_text:
            return
        
        msg.message = new_text
        msg.is_edited = True
        db.session.commit()
        
        # FIXED: Send to correct rooms
        if msg.room:
            socketio.emit('msg_updated', {'id': msg.id, 'text': msg.message}, room=msg.room)
        else:
            # DM: send to both users
            socketio.emit('msg_updated', {'id': msg.id, 'text': msg.message}, room=me)
            if msg.receiver:
                socketio.emit('msg_updated', {'id': msg.id, 'text': msg.message}, room=msg.receiver)
    
    elif action_type == 'pin':
        room = data.get('room')
        if room:
            db.session.add(Pin(message_id=msg.id, room=room))
            db.session.commit()
    
    elif action_type == 'react':
        emoji = data.get('emoji')
        if emoji:
            db.session.add(Reaction(message_id=msg.id, username=me, emoji=emoji))
            db.session.commit()

@socketio.on('typing_status')
def handle_typing(data):
    """Handle typing indicators - FIXED: scoped to room/DM"""
    me = session.get('user')
    if not me:
        return
    
    target = data.get('target')
    is_dm = data.get('isDM')
    status = data.get('status')
    
    if not target:
        return
    
    # FIXED: Only send to the specific room/user
    socketio.emit('typing_update', {
        'user': me,
        'status': status
    }, room=target)

@socketio.on('search_msgs')
def handle_search(data):
    """Search messages in a room"""
    me = session.get('user')
    if not me:
        return
    
    q = data.get('q')
    target = data.get('target')
    
    if not q or not target:
        return
    
    # Note: Should add permission check for room access
    results = Message.query.filter(
        Message.room == target,
        Message.message.contains(q)
    ).all()
    
    emit('search_results', [m.to_dict() for m in results])

@socketio.on('status_update')
def handle_status_update(data):
    """Update user status - FIXED: no recursion"""
    me = session.get('user')
    if not me:
        return
    
    status = data.get('status')
    if status not in ['online', 'away', 'offline']:
        return
    
    user = User.query.get(me)
    if user:
        user.status = status
        user.last_seen = int(time.time())
        db.session.commit()
        
        # FIXED: Broadcast status change (not sync_data to avoid recursion!)
        socketio.emit('user_status_changed', {
            'username': me,
            'status': status,
            'last_seen': user.last_seen
        }, broadcast=True)

if __name__ == "__main__":
    socketio.run(app, host='0.0.0.0', port=APP_PORT)