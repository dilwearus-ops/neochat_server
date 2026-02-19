import asyncio
import json
import websockets
import re
import sqlite3
import html
import time
import hashlib
import os
import secrets
import threading
from datetime import datetime, timedelta
from collections import defaultdict
from http.server import HTTPServer, SimpleHTTPRequestHandler

# --- КОНФИГУРАЦИЯ ---
DB_NAME = "chat_v10_ultimate.db"
MAX_MEDIA_SIZE = 20 * 1024 * 1024  # 20 MB
NICK_RE = re.compile(r"^[A-Za-z0-9_\-]{3,20}$")

# Анти-спам
SPAM_LIMIT = 10  # сообщений в минуту
SPAM_WINDOW = 60  # секунд

# Стоп-слова для автомодерации
BLOCKED_WORDS = ["ban", "spam", "abuse"]  # пример

class Database:
    def __init__(self, db_name):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self.init_db()

    def init_db(self):
        # 1. Пользователи
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT,
                avatar TEXT,
                bio TEXT,
                user_status TEXT DEFAULT 'Привет, я здесь',
                created_at REAL
            )
        ''')
        # 2. Комнаты
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS rooms (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE,
                creator TEXT,
                type TEXT,
                avatar TEXT,
                pinned_msg_id INTEGER,
                created_at REAL
            )
        ''')
        # 3. Баны
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS bans (
                room_id TEXT,
                username TEXT,
                PRIMARY KEY (room_id, username),
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            )
        ''')
        # 4. Сообщения
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target TEXT,
                context TEXT,
                sender TEXT,
                mtype TEXT,
                text TEXT,
                media_data TEXT,
                filename TEXT,
                reply_to_json TEXT,
                thread_id INTEGER,
                is_edited INTEGER DEFAULT 0,
                is_read INTEGER DEFAULT 0,
                is_bookmarked INTEGER DEFAULT 0,
                scheduled_time REAL,
                timestamp REAL
            )
        ''')
        # 5. Реакции
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS reactions (
                message_id INTEGER,
                sender TEXT,
                emoji TEXT,
                PRIMARY KEY (message_id, sender, emoji)
            )
        ''')
        # 6. Голоса
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS votes (
                message_id INTEGER,
                username TEXT,
                option_index INTEGER,
                PRIMARY KEY (message_id, username)
            )
        ''')
        # 7. Закладки
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS bookmarks (
                username TEXT,
                message_id INTEGER,
                created_at REAL,
                PRIMARY KEY (username, message_id)
            )
        ''')
        # 8. Приглашения для групп
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS invite_codes (
                code TEXT PRIMARY KEY,
                room_id TEXT,
                creator TEXT,
                created_at REAL,
                expires_at REAL,
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            )
        ''')
        # 9. Члены групп с ролями
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS room_members (
                room_id TEXT,
                username TEXT,
                role TEXT DEFAULT 'member',
                joined_at REAL,
                PRIMARY KEY (room_id, username),
                FOREIGN KEY (room_id) REFERENCES rooms(id)
            )
        ''')
        # 10. Удаленные сообщения (для администраторов)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS deleted_messages (
                id INTEGER PRIMARY KEY,
                message_id INTEGER,
                deleted_by TEXT,
                reason TEXT,
                deleted_at REAL
            )
        ''')
        # 11. Запланированные сообщения
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target TEXT,
                context TEXT,
                sender TEXT,
                mtype TEXT,
                text TEXT,
                scheduled_time REAL,
                sent INTEGER DEFAULT 0
            )
        ''')
        self.conn.commit()

    # --- AUTH ---
    def register_user(self, username, password):
        try:
            phash = hashlib.sha256(password.encode()).hexdigest()
            self.cursor.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)", 
                (username, phash, time.time())
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def check_login(self, username, password):
        phash = hashlib.sha256(password.encode()).hexdigest()
        self.cursor.execute(
            "SELECT * FROM users WHERE username=? AND password_hash=?", 
            (username, phash)
        )
        return self.cursor.fetchone() is not None

    def get_user_info(self, username):
        self.cursor.execute(
            "SELECT username, avatar, bio, user_status FROM users WHERE username=?", 
            (username,)
        )
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def update_profile(self, username, avatar, bio):
        self.cursor.execute(
            "UPDATE users SET avatar=?, bio=? WHERE username=?", 
            (avatar, bio, username)
        )
        self.conn.commit()

    def update_user_status(self, username, status):
        self.cursor.execute(
            "UPDATE users SET user_status=? WHERE username=?", 
            (status, username)
        )
        self.conn.commit()

    def get_contacts(self, username):
        self.cursor.execute('''
            SELECT DISTINCT sender as c FROM messages WHERE context='pm' AND target=?
            UNION
            SELECT DISTINCT target as c FROM messages WHERE context='pm' AND sender=?
        ''', (username, username))
        return [row['c'] for row in self.cursor.fetchall()]

    # --- ROOMS & MEMBERS ---
    def create_room(self, name, creator, rtype):
        try:
            # Генерируем ID как @название (но в нижнем регистре без пробелов)
            room_id = "@" + name.lower().replace(" ", "")
            self.cursor.execute(
                "INSERT INTO rooms (id, name, creator, type, created_at) VALUES (?, ?, ?, ?, ?)", 
                (room_id, name, creator, rtype, time.time())
            )
            self.cursor.execute(
                "INSERT INTO room_members (room_id, username, role, joined_at) VALUES (?, ?, ?, ?)",
                (room_id, creator, "admin", time.time())
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_rooms(self, username=None):
        """Get rooms where user is a member. If username is None, return all rooms."""
        if username:
            self.cursor.execute("""
                SELECT r.* FROM rooms r
                INNER JOIN room_members rm ON r.id = rm.room_id
                WHERE rm.username = ?
                ORDER BY r.created_at DESC
            """, (username,))
        else:
            self.cursor.execute("SELECT * FROM rooms ORDER BY created_at DESC")
        
        rooms = [dict(row) for row in self.cursor.fetchall()]
        # Add member count to each room
        for room in rooms:
            self.cursor.execute("SELECT COUNT(*) as count FROM room_members WHERE room_id=?", (room['id'],))
            room['member_count'] = self.cursor.fetchone()['count']
        return rooms

    def get_room_info(self, room_id):
        # Попытаемся найти по ID сначала, потом по имени (для обратной совместимости)
        self.cursor.execute("SELECT * FROM rooms WHERE id=?", (room_id,))
        row = self.cursor.fetchone()
        if not row:
            self.cursor.execute("SELECT * FROM rooms WHERE name=?", (room_id,))
            row = self.cursor.fetchone()
        return dict(row) if row else None

    def get_room_members(self, room_id):
        self.cursor.execute(
            "SELECT username, role, joined_at FROM room_members WHERE room_id=? ORDER BY joined_at ASC", 
            (room_id,)
        )
        members = [dict(row) for row in self.cursor.fetchall()]
        # Get avatar for each member
        for member in members:
            user = self.get_user_info(member['username'])
            if user:
                member['avatar'] = user.get('avatar')
        return members

    def search_rooms(self, query):
        """Search rooms by ID (starts with @) or name"""
        if query.startswith('@'):
            # Search by ID
            query = query[1:]  # Remove @
            self.cursor.execute("SELECT * FROM rooms WHERE id LIKE ? ORDER BY created_at DESC", (f'@{query}%',))
        else:
            # Search by name
            self.cursor.execute("SELECT * FROM rooms WHERE name LIKE ? ORDER BY created_at DESC", (f'%{query}%',))
        
        rooms = [dict(row) for row in self.cursor.fetchall()]
        # Add member count
        for room in rooms:
            self.cursor.execute("SELECT COUNT(*) as count FROM room_members WHERE room_id=?", (room['id'],))
            room['member_count'] = self.cursor.fetchone()['count']
        return rooms

    def join_room(self, room_id, username):
        """Add user to room if not already member"""
        try:
            self.cursor.execute(
                "INSERT INTO room_members (room_id, username, role, joined_at) VALUES (?, ?, ?, ?)",
                (room_id, username, "member", time.time())
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def search_users(self, query, exclude_username=None):
        """Search users by username, optionally excluding current user"""
        if exclude_username:
            self.cursor.execute(
                "SELECT username, avatar, bio, user_status FROM users WHERE username LIKE ? AND username != ? LIMIT 20", 
                (f'%{query}%', exclude_username)
            )
        else:
            self.cursor.execute(
                "SELECT username, avatar, bio, user_status FROM users WHERE username LIKE ? LIMIT 20", 
                (f'%{query}%',)
            )
        return [dict(row) for row in self.cursor.fetchall()]

    def get_recent_contacts(self, username, limit=15):
        """Get recent contacts from message history"""
        # Find unique users from recent PM conversations (target is the other user for PMs)
        self.cursor.execute("""
            SELECT DISTINCT CASE 
                WHEN sender = ? THEN target 
                ELSE sender 
            END as contact_user
            FROM messages 
            WHERE context = 'pm' AND (sender = ? OR target = ?)
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (username, username, username, limit))
        
        contacts = []
        seen = set()
        for row in self.cursor.fetchall():
            contact_name = row['contact_user']
            if contact_name and contact_name not in seen:
                seen.add(contact_name)
                user = self.get_user_info(contact_name)
                if user:
                    contacts.append(user)
        return contacts

    def add_room_member(self, room_id, username, role='member'):
        try:
            self.cursor.execute(
                "INSERT INTO room_members (room_id, username, role, joined_at) VALUES (?, ?, ?, ?)",
                (room_id, username, role, time.time())
            )
            self.conn.commit()
            return True
        except:
            return False

    def get_user_role(self, room_id, username):
        self.cursor.execute(
            "SELECT role FROM room_members WHERE room_id=? AND username=?",
            (room_id, username)
        )
        row = self.cursor.fetchone()
        return row['role'] if row else None

    # --- INVITE CODES ---
    def create_invite_code(self, room_id, creator, hours=24):
        code = secrets.token_urlsafe(8)
        expires_at = time.time() + (hours * 3600)
        try:
            self.cursor.execute(
                "INSERT INTO invite_codes (code, room_id, creator, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (code, room_id, creator, time.time(), expires_at)
            )
            self.conn.commit()
            return code
        except:
            return None

    def use_invite_code(self, code, username):
        self.cursor.execute(
            "SELECT room_id, expires_at FROM invite_codes WHERE code=?", 
            (code,)
        )
        row = self.cursor.fetchone()
        if not row:
            return None, "Код не найден"
        
        if row['expires_at'] < time.time():
            return None, "Код истёк"
        
        room_id = row['room_id']
        if self.add_room_member(room_id, username):
            return room_id, "OK"
        return None, "Ошибка добавления в группу"

    # --- MESSAGES & SEARCH ---
    def save_message(self, data):
        context = 'room' if 'room_name' in data else 'pm'
        target = data.get('room_name') if context == 'room' else data.get('recipient')
        reply_json = json.dumps(data.get("replyTo")) if data.get("replyTo") else None
        ts = data.get('timestamp', time.time())
        thread_id = data.get('thread_id')
        scheduled_time = data.get('scheduled_time')
        
        media = data.get("data")
        if data.get("type") == "poll":
            media = json.dumps(data.get("options"))

        self.cursor.execute('''
            INSERT INTO messages (target, context, sender, mtype, text, media_data, filename, 
                                 reply_to_json, thread_id, timestamp, scheduled_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            target, context, data.get("sender"), data.get("type"), 
            data.get("text"), media, data.get("filename"), 
            reply_json, thread_id, ts, scheduled_time
        ))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_message(self, msg_id):
        self.cursor.execute("SELECT * FROM messages WHERE id=?", (msg_id,))
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def search_messages(self, context, target, viewer, query, start_date=None, end_date=None, limit=100):
        sql = "SELECT * FROM messages WHERE context=? AND "
        params = [context]
        
        if context == 'room':
            sql += "target = ?"
            params.append(target)
        else:
            sql += "((sender = ? AND target = ?) OR (sender = ? AND target = ?))"
            params.extend([viewer, target, target, viewer])

        sql += " AND (text LIKE ?"
        params.append(f"%{query}%")
        
        if start_date:
            sql += " AND timestamp >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND timestamp <= ?"
            params.append(end_date)
        
        sql += ") ORDER BY id DESC LIMIT ?"
        params.append(limit)
        
        self.cursor.execute(sql, tuple(params))
        rows = self.cursor.fetchall()
        return [self._format_message(row) for row in reversed(rows)]

    def _format_message(self, row):
        msg_id = row['id']
        reactions = self.get_reactions(msg_id)
        sender_info = self.get_user_info(row["sender"])
        
        msg_obj = {
            "id": msg_id,
            "type": row["mtype"],
            "sender": row["sender"],
            "sender_avatar": sender_info['avatar'] if sender_info else None,
            "text": row["text"],
            "data": row["media_data"],
            "filename": row["filename"],
            "timestamp": row["timestamp"],
            "is_edited": row["is_edited"],
            "is_read": row["is_read"],
            "is_bookmarked": row["is_bookmarked"],
            "thread_id": row["thread_id"],
            "replyTo": json.loads(row["reply_to_json"]) if row["reply_to_json"] else None,
            "reactions": reactions
        }
        if row["mtype"] == "poll":
            msg_obj["poll_results"] = self.get_poll_results(msg_id)
        return msg_obj

    def edit_message(self, msg_id, sender, new_text):
        self.cursor.execute(
            "UPDATE messages SET text=?, is_edited=1 WHERE id=? AND sender=?", 
            (new_text, msg_id, sender)
        )
        self.conn.commit()
        return self.cursor.rowcount > 0

    def delete_message(self, msg_id, sender, is_admin=False, reason=""):
        self.cursor.execute("SELECT sender FROM messages WHERE id=?", (msg_id,))
        row = self.cursor.fetchone()
        if not row:
            return False
        
        if is_admin or row['sender'] == sender:
            self.cursor.execute("DELETE FROM messages WHERE id=?", (msg_id,))
            self.cursor.execute(
                "INSERT INTO deleted_messages (message_id, deleted_by, reason, deleted_at) VALUES (?, ?, ?, ?)",
                (msg_id, sender, reason, time.time())
            )
            self.conn.commit()
            return True
        return False

    def mark_read(self, sender, recipient):
        self.cursor.execute(
            "UPDATE messages SET is_read=1 WHERE context='pm' AND sender=? AND target=? AND is_read=0", 
            (sender, recipient)
        )
        self.conn.commit()
        return self.cursor.rowcount > 0

    def get_history(self, context, target, viewer, limit=100):
        query = "SELECT * FROM messages WHERE context=? AND "
        params = [context]
        
        if context == 'room':
            query += "target = ?"
            params.append(target)
        else:
            query += "((sender = ? AND target = ?) OR (sender = ? AND target = ?))"
            params.extend([viewer, target, target, viewer])

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        
        self.cursor.execute(query, tuple(params))
        rows = self.cursor.fetchall()
        return [self._format_message(row) for row in reversed(rows)]

    def get_thread_messages(self, thread_id, limit=50):
        self.cursor.execute(
            "SELECT * FROM messages WHERE thread_id=? ORDER BY id ASC LIMIT ?",
            (thread_id, limit)
        )
        rows = self.cursor.fetchall()
        return [self._format_message(row) for row in rows]

    def toggle_bookmark(self, username, message_id):
        self.cursor.execute(
            "SELECT * FROM bookmarks WHERE username=? AND message_id=?",
            (username, message_id)
        )
        if self.cursor.fetchone():
            self.cursor.execute(
                "DELETE FROM bookmarks WHERE username=? AND message_id=?",
                (username, message_id)
            )
        else:
            self.cursor.execute(
                "INSERT INTO bookmarks (username, message_id, created_at) VALUES (?, ?, ?)",
                (username, message_id, time.time())
            )
        self.cursor.execute(
            "UPDATE messages SET is_bookmarked=1 WHERE id=? AND EXISTS(SELECT 1 FROM bookmarks WHERE message_id=?)",
            (message_id, message_id)
        )
        self.conn.commit()

    # --- VOTES & REACTIONS ---
    def vote_poll(self, msg_id, username, option_index):
        self.cursor.execute(
            "REPLACE INTO votes (message_id, username, option_index) VALUES (?, ?, ?)", 
            (msg_id, username, option_index)
        )
        self.conn.commit()
        return self.get_poll_results(msg_id)

    def get_poll_results(self, msg_id):
        self.cursor.execute("SELECT option_index, username FROM votes WHERE message_id=?", (msg_id,))
        results = {}
        for row in self.cursor.fetchall():
            idx = row['option_index']
            if idx not in results: 
                results[idx] = []
            results[idx].append(row['username'])
        return results

    def toggle_reaction(self, msg_id, sender, emoji):
        self.cursor.execute(
            "SELECT * FROM reactions WHERE message_id=? AND sender=? AND emoji=?", 
            (msg_id, sender, emoji)
        )
        if self.cursor.fetchone():
            self.cursor.execute(
                "DELETE FROM reactions WHERE message_id=? AND sender=? AND emoji=?", 
                (msg_id, sender, emoji)
            )
        else:
            self.cursor.execute(
                "INSERT INTO reactions (message_id, sender, emoji) VALUES (?, ?, ?)", 
                (msg_id, sender, emoji)
            )
        self.conn.commit()
        return self.get_reactions(msg_id)

    def get_reactions(self, msg_id):
        self.cursor.execute("SELECT emoji, sender FROM reactions WHERE message_id=?", (msg_id,))
        result = {}
        for row in self.cursor.fetchall():
            emoji = row['emoji']
            if emoji not in result: 
                result[emoji] = []
            result[emoji].append(row['sender'])
        return result

    def pin_message(self, room_id, msg_id):
        self.cursor.execute("UPDATE rooms SET pinned_msg_id=? WHERE id=?", (msg_id, room_id))
        self.conn.commit()

    def ban_user(self, room_id, username):
        try:
            self.cursor.execute(
                "INSERT INTO bans (room_id, username) VALUES (?, ?)", 
                (room_id, username)
            )
            self.conn.commit()
        except:
            pass

    def is_banned(self, room_id, username):
        self.cursor.execute(
            "SELECT 1 FROM bans WHERE room_id=? AND username=?", 
            (room_id, username)
        )
        return self.cursor.fetchone() is not None

    def get_deleted_message_log(self, room_id):
        self.cursor.execute(
            """SELECT dm.*, m.text FROM deleted_messages dm 
               LEFT JOIN messages m ON dm.message_id = m.id 
               WHERE EXISTS(SELECT 1 FROM messages WHERE id=dm.message_id AND target=?)""",
            (room_id,)
        )
        return [dict(row) for row in self.cursor.fetchall()]


class ChatServer:
    def __init__(self):
        self.clients = {}
        self.db = Database(DB_NAME)
        self.user_last_seen = {}  # {username: timestamp}
        self.spam_tracker = defaultdict(list)  # {username: [timestamps]}
        self.last_activity = {}  # {username: timestamp}
        
        # Запуск фонового потока для запланированных сообщений
        threading.Thread(target=self.scheduled_message_worker, daemon=True).start()

    def is_spam(self, username):
        """Проверка анти-спама"""
        now = time.time()
        self.spam_tracker[username] = [ts for ts in self.spam_tracker[username] 
                                       if now - ts < SPAM_WINDOW]
        
        if len(self.spam_tracker[username]) >= SPAM_LIMIT:
            return True
        
        self.spam_tracker[username].append(now)
        return False

    def check_content(self, text):
        """Проверка контента на запрещенные слова"""
        text_lower = text.lower()
        for word in BLOCKED_WORDS:
            if word in text_lower:
                return False
        return True

    def scheduled_message_worker(self):
        """Фоновый рабочий для отправки запланированных сообщений"""
        while True:
            time.sleep(10)  # проверка каждые 10 секунд
            self.db.cursor.execute(
                "SELECT * FROM scheduled_messages WHERE sent=0 AND scheduled_time <= ?",
                (time.time(),)
            )
            for row in self.db.cursor.fetchall():
                # Здесь можно добавить логику отправки
                self.db.cursor.execute(
                    "UPDATE scheduled_messages SET sent=1 WHERE id=?",
                    (row['id'],)
                )
                self.db.conn.commit()

    async def broadcast(self, message, exclude=None):
        if not self.clients: return
        msg_json = json.dumps(message)
        recipients = [ws for ws in self.clients.values() if ws != exclude]
        if recipients:
            await asyncio.gather(*[ws.send(msg_json) for ws in recipients], return_exceptions=True)

    async def send_to_user(self, nick, message):
        if nick in self.clients:
            await self.clients[nick].send(json.dumps(message))

    async def broadcast_presence(self):
        online_users = list(self.clients.keys())
        for user, ws in self.clients.items():
            contacts_list = self.db.get_contacts(user)
            final_list = []
            processed = set()
            for contact_nick in contacts_list:
                processed.add(contact_nick)
                u_info = self.db.get_user_info(contact_nick)
                if u_info:
                    final_list.append({
                        "nick": contact_nick,
                        "avatar": u_info['avatar'],
                        "bio": u_info['bio'],
                        "user_status": u_info['user_status'],
                        "online": contact_nick in online_users,
                        "last_seen": self.user_last_seen.get(contact_nick)
                    })
            for on_user in online_users:
                if on_user not in processed and on_user != user:
                    u_info = self.db.get_user_info(on_user)
                    if u_info:
                        final_list.append({
                            "nick": on_user,
                            "avatar": u_info['avatar'],
                            "bio": u_info['bio'],
                            "user_status": u_info['user_status'],
                            "online": True,
                            "last_seen": None
                        })
            await ws.send(json.dumps({"type": "contacts_list", "users": final_list}))

    async def handler(self, websocket):
        nick = None
        try:
            # AUTH
            msg_str = await asyncio.wait_for(websocket.recv(), timeout=60)
            auth_data = json.loads(msg_str)
            
            if auth_data.get('type') == 'auth_req':
                username = auth_data['username']
                password = auth_data['password']
                action = auth_data['action']

                if not NICK_RE.match(username):
                    await websocket.send(json.dumps({"type": "auth_error", "text": "Ник некорректен."}))
                    return

                success = False
                if action == 'register': 
                    success = self.db.register_user(username, password)
                elif action == 'login': 
                    success = self.db.check_login(username, password)

                if success:
                    if action == 'login' and username in self.clients:
                         await websocket.send(json.dumps({"type": "auth_error", "text": "Уже в сети."}))
                         return
                    
                    profile = self.db.get_user_info(username)
                    await websocket.send(json.dumps({
                        "type": "auth_success", 
                        "nick": username, 
                        "avatar": profile['avatar'], 
                        "bio": profile['bio'],
                        "user_status": profile['user_status']
                    }))
                    nick = username
                    self.clients[nick] = websocket
                    self.user_last_seen[nick] = time.time()
                    print(f"[+] {nick} connected")
                else:
                    await websocket.send(json.dumps({"type": "auth_error", "text": "Ошибка входа."}))
                    return
            else:
                return

            await websocket.send(json.dumps({"type": "rooms_list", "rooms": self.db.get_rooms(nick)}))
            await self.broadcast_presence()

            # MAIN LOOP
            async for message_str in websocket:
                data = json.loads(message_str)
                mtype = data.get("type")
                
                if "text" in data and data["text"]: 
                    data["text"] = html.escape(data["text"])

                # Обновить last_seen
                self.user_last_seen[nick] = time.time()

                # Анти-спам проверка
                if mtype in ["msg", "image", "video", "audio", "file", "poll"]:
                    if self.is_spam(nick):
                        await websocket.send(json.dumps({
                            "type": "error", 
                            "text": "Вы отправляете слишком много сообщений"
                        }))
                        continue

                if mtype in ["msg", "image", "video", "audio", "file", "poll", "sticker"]:
                    # Проверка контента
                    if data.get("text") and not self.check_content(data["text"]):
                        await websocket.send(json.dumps({
                            "type": "error", 
                            "text": "Сообщение содержит недопустимый контент"
                        }))
                        continue
                    
                    # Проверка: нельзя писать самому себе
                    if 'recipient' in data and data['recipient'] == nick:
                        await websocket.send(json.dumps({"type": "error", "text": "Нельзя писать сообщения самому себе"}))
                        continue
                    
                    if 'room_name' in data and self.db.is_banned(data['room_name'], nick):
                        await websocket.send(json.dumps({"type": "error", "text": "Вы забанены в этой группе."}))
                        continue
                    
                    if 'room_name' in data:
                        info = self.db.get_room_info(data['room_name'])
                        role = self.db.get_user_role(data['room_name'], nick)
                        if info and info['type'] == 'channel' and role not in ['admin']:
                            continue

                    data['sender'] = nick
                    data['timestamp'] = time.time()
                    u_info = self.db.get_user_info(nick)
                    data['sender_avatar'] = u_info['avatar'] if u_info else None
                    data['is_read'] = 0
                    data['is_edited'] = 0

                    if mtype == "poll": 
                        data['poll_results'] = {}

                    msg_id = self.db.save_message(data)
                    data['id'] = msg_id
                    data['reactions'] = {}

                    if 'room_name' in data: 
                        await self.broadcast(data)
                    elif 'recipient' in data:
                        await self.send_to_user(data['recipient'], data)
                        await websocket.send(json.dumps(data))

                # --- ПОИСК СООБЩЕНИЙ ---
                elif mtype == "search_messages":
                    results = self.db.search_messages(
                        data['context'],
                        data['target'],
                        nick,
                        data.get('query', ''),
                        data.get('start_date'),
                        data.get('end_date'),
                        data.get('limit', 100)
                    )
                    await websocket.send(json.dumps({
                        "type": "search_results",
                        "results": results
                    }))

                # --- ЗАКЛАДКИ ---
                elif mtype == "toggle_bookmark":
                    self.db.toggle_bookmark(nick, data['message_id'])
                    await websocket.send(json.dumps({
                        "type": "bookmark_toggled",
                        "id": data['message_id']
                    }))

                # --- ПЕРЕСЫЛКА СООБЩЕНИЯ ---
                elif mtype == "forward_msg":
                    orig_msg = self.db.get_message(data['message_id'])
                    if orig_msg:
                        fwd_data = {
                            "type": data['target_type'],
                            "sender": nick,
                            "text": f"[Пересланное] {orig_msg['text']}",
                            "timestamp": time.time(),
                            "sender_avatar": self.db.get_user_info(nick)['avatar']
                        }
                        if data['target_type'] == 'room':
                            fwd_data['room_name'] = data['target']
                            await self.broadcast(fwd_data)
                        else:
                            fwd_data['recipient'] = data['target']
                            await self.send_to_user(data['target'], fwd_data)
                            await websocket.send(json.dumps(fwd_data))

                # --- СТАТУС ПОЛЬЗОВАТЕЛЯ ---
                elif mtype == "update_status":
                    self.db.update_user_status(nick, data.get('status', ''))
                    await self.broadcast_presence()

                # --- THREADS ---
                elif mtype == "get_thread":
                    thread_msgs = self.db.get_thread_messages(data['thread_id'])
                    await websocket.send(json.dumps({
                        "type": "thread_messages",
                        "messages": thread_msgs
                    }))

                # --- POLL VOTE ---
                elif mtype == "vote_poll":
                    results = self.db.vote_poll(data['message_id'], nick, data['option_index'])
                    update = {"type": "poll_update", "id": data['message_id'], "results": results}
                    
                    if 'room_name' in data: 
                        await self.broadcast(update)
                    elif 'recipient' in data:
                        sender = self.db.get_message(data['message_id'])['sender']
                        await self.send_to_user(sender, update)
                        await self.send_to_user(data['recipient'], update)
                        await websocket.send(json.dumps(update))

                # --- WEBRTC SIGNALING ---
                elif mtype == "signal":
                    payload = {
                        "type": "signal",
                        "sender": nick,
                        "sender_avatar": self.db.get_user_info(nick)['avatar'],
                        "data": data['data']
                    }
                    if 'room_name' in data:
                        payload['room_name'] = data['room_name']
                        await self.broadcast(payload, exclude=websocket)
                    elif 'target' in data:
                        await self.send_to_user(data['target'], payload)

                elif mtype == "typing":
                    if 'room_name' in data: 
                        await self.broadcast(data, exclude=websocket)
                    elif 'recipient' in data: 
                        await self.send_to_user(data['recipient'], data)

                elif mtype == "reaction":
                    new_r = self.db.toggle_reaction(data['message_id'], nick, data['emoji'])
                    await self.broadcast({"type": "reaction_update", "id": data['message_id'], "reactions": new_r})

                elif mtype == "mark_read":
                    if self.db.mark_read(data['sender'], nick):
                        await self.send_to_user(data['sender'], {"type": "msgs_read_by_user", "reader": nick})

                elif mtype == "edit_msg":
                    if self.db.edit_message(data['id'], nick, data['text']):
                        upd = {"type": "msg_edited", "id": data['id'], "text": data['text']}
                        if 'room_name' in data: 
                            await self.broadcast(upd)
                        else:
                            await self.send_to_user(data['recipient'], upd)
                            await websocket.send(json.dumps(upd))

                elif mtype == "delete_msg":
                    is_admin = False
                    if 'room_name' in data:
                        role = self.db.get_user_role(data['room_name'], nick)
                        if role in ['admin', 'moderator']: 
                            is_admin = True
                    
                    if self.db.delete_message(data['id'], nick, is_admin, data.get('reason', '')):
                        upd = {"type": "msg_deleted", "id": data['id']}
                        if 'room_name' in data: 
                            await self.broadcast(upd)
                        else:
                            await self.send_to_user(data['recipient'], upd)
                            await websocket.send(json.dumps(upd))

                elif mtype == "pin_msg":
                    role = self.db.get_user_role(data['room_name'], nick)
                    if role in ['admin']:
                        self.db.pin_message(data['room_name'], data['id'])
                        pinned_msg = self.db.get_message(data['id'])
                        await self.broadcast({
                            "type": "pinned_update", 
                            "room_name": data['room_name'], 
                            "msg": pinned_msg
                        })

                elif mtype == "create_invite":
                    role = self.db.get_user_role(data['room_name'], nick)
                    if role in ['admin', 'moderator']:
                        code = self.db.create_invite_code(data['room_name'], nick)
                        if code:
                            await websocket.send(json.dumps({
                                "type": "invite_created",
                                "code": code,
                                "link": f"join/{code}"
                            }))

                elif mtype == "join_with_invite":
                    room_name, status = self.db.use_invite_code(data['code'], nick)
                    await websocket.send(json.dumps({
                        "type": "join_result",
                        "success": status == "OK",
                        "room_name": room_name,
                        "message": status
                    }))
                    if status == "OK":
                        await self.broadcast_presence()

                elif mtype == "kick_user":
                    role = self.db.get_user_role(data['room_name'], nick)
                    if role in ['admin']:
                        await self.broadcast({
                            "type": "info", 
                            "text": f"{data['user']} кикнут из {data['room_name']}"
                        })

                elif mtype == "ban_user":
                    role = self.db.get_user_role(data['room_name'], nick)
                    if role in ['admin']:
                        self.db.ban_user(data['room_name'], data['user'])
                        await self.broadcast({
                            "type": "info", 
                            "text": f"{data['user']} забанен в {data['room_name']}"
                        })

                elif mtype == "update_profile":
                    self.db.update_profile(nick, data['avatar'], html.escape(data['bio']))
                    await self.broadcast_presence()
                    await websocket.send(json.dumps({"type": "info", "text": "Профиль обновлен"}))

                elif mtype == "create_room":
                    if self.db.create_room(html.escape(data['name']), nick, data['rtype']):
                        # Send updated rooms list only to the user who created the room
                        await websocket.send(json.dumps({"type": "rooms_list", "rooms": self.db.get_rooms(nick)}))

                elif mtype == "search_rooms":
                    query = data.get('query', '').strip()
                    if query:
                        results = self.db.search_rooms(query)
                        # Check which ones user is already member of
                        for room in results:
                            is_member = self.db.cursor.execute(
                                "SELECT 1 FROM room_members WHERE room_id=? AND username=?",
                                (room['id'], nick)
                            ).fetchone() is not None
                            room['is_member'] = is_member
                        await websocket.send(json.dumps({"type": "search_results", "results": results}))

                elif mtype == "join_room":
                    room_id = data.get('room_id')
                    if self.db.join_room(room_id, nick):
                        # Send confirmation and updated rooms list
                        await websocket.send(json.dumps({
                            "type": "room_joined",
                            "room_id": room_id,
                            "message": "Вы присоединились к группе"
                        }))
                        await websocket.send(json.dumps({
                            "type": "rooms_list",
                            "rooms": self.db.get_rooms(nick)
                        }))
                    else:
                        await websocket.send(json.dumps({
                            "type": "error",
                            "text": "Не удалось присоединиться к группе"
                        }))

                elif mtype == "search_users":
                    query = data.get('query', '').strip()
                    if query:
                        # Исключаем текущего пользователя из результатов
                        results = self.db.search_users(query, exclude_username=nick)
                        await websocket.send(json.dumps({"type": "search_users_results", "results": results}))

                elif mtype == "get_recent_contacts":
                    contacts = self.db.get_recent_contacts(nick)
                    await websocket.send(json.dumps({"type": "recent_contacts", "contacts": contacts}))

                elif mtype == "rename_room":
                    role = self.db.get_user_role(data['room_name'], nick)
                    if role in ['admin']:
                        # Обновляем только название, ID остаётся прежним
                        self.db.cursor.execute("UPDATE rooms SET name=? WHERE id=?", 
                                              (data['new_name'], data['room_name']))
                        self.db.conn.commit()
                        # Send updated rooms list to all connected clients
                        for client_nick in self.clients:
                            await self.clients[client_nick].send(json.dumps({
                                "type": "rooms_list",
                                "rooms": self.db.get_rooms(client_nick)
                            }))
                elif mtype == "update_room_avatar":
                    role = self.db.get_user_role(data['room_name'], nick)
                    if role in ['admin']:
                        # Сохраняем аватар в БД
                        self.db.cursor.execute("UPDATE rooms SET avatar=? WHERE id=?", 
                                              (data['avatar'], data['room_name']))
                        self.db.conn.commit()
                        # Send updated rooms list to all connected clients
                        for client_nick in self.clients:
                            await self.clients[client_nick].send(json.dumps({
                                "type": "rooms_list",
                                "rooms": self.db.get_rooms(client_nick)
                            }))

                elif mtype == "change_member_role":
                    role = self.db.get_user_role(data['room_name'], nick)
                    if role in ['admin']:
                        self.db.cursor.execute("UPDATE room_members SET role=? WHERE room_id=? AND username=?",
                                              (data['role'], data['room_name'], data['username']))
                        self.db.conn.commit()
                        await self.broadcast({
                            "type": "info",
                            "text": f"Роль {data['username']} в группе {data['room_name']} изменена на {data['role']}"
                        })

                elif mtype == "history_req":
                    context = data['context']
                    target = data['target']
                    if context == 'pm':
                        self.db.mark_read(target, nick)
                        await self.send_to_user(target, {"type": "msgs_read_by_user", "reader": nick})
                    hist = self.db.get_history(context, target, nick)
                    room_info = self.db.get_room_info(target) if context == 'room' else None
                    
                    # Добавить информацию о членах группы и количество участников
                    if room_info:
                        members = self.db.get_room_members(target)
                        room_info['members'] = members
                        room_info['member_count'] = len(members)
                    
                    pinned = None
                    if room_info and room_info['pinned_msg_id']:
                        pinned = self.db.get_message(room_info['pinned_msg_id'])
                    await websocket.send(json.dumps({
                        "type": "history", "history": hist, "context": context, "target": target, 
                        "room_info": room_info, "pinned": pinned
                    }))

        except websockets.ConnectionClosed: 
            pass
        except Exception as e: 
            print(f"Err {nick}: {e}")
        finally:
            if nick in self.clients: 
                del self.clients[nick]
            if nick in self.user_last_seen:
                del self.user_last_seen[nick]
            await self.broadcast_presence()
            print(f"[-] {nick} disconnected")

async def main(host, port):
    server = ChatServer()
    print(f"🚀 NEOCHAT SERVER V10 ULTIMATE running on {host}:{port}")
    
    # Запустить HTTP сервер для обслуживания файлов в отдельном потоке
    def run_http_server():
        class MyHandler(SimpleHTTPRequestHandler):
            def do_GET(self):
                # Если запрос к корню или без расширения - отдаём index.html
                if self.path == '/' or '.' not in self.path.split('/')[-1]:
                    self.path = '/index.html'
                return super().do_GET()
            
            def log_message(self, format, *args):
                # Подавляем стандартные логи HTTP
                pass
        
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        http_server = HTTPServer(("0.0.0.0", port), MyHandler)
        http_server.serve_forever()
    
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    # WebSocket на том же порту, что и HTTP
    async with websockets.serve(server.handler, host, port, max_size=MAX_MEDIA_SIZE):
        await asyncio.Future()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    asyncio.run(main("0.0.0.0", port))
