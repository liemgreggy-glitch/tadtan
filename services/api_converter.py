"""
services.api_converter - API format conversion with web verification server
"""
import asyncio
import base64
import json
import logging
import os
import secrets
import sqlite3
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.constants import BEIJING_TZ

logger = logging.getLogger(__name__)

try:
    from flask import Flask, request, jsonify, Response
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

try:
    from telethon import TelegramClient, events
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False

try:
    from opentele.api import API, UseCurrentSession
    from opentele.td import TDesktop
    OPENTELE_AVAILABLE = True
except ImportError:
    OPENTELE_AVAILABLE = False
    API = None
    UseCurrentSession = None
    TDesktop = None

try:
    import tdata as _tdata_module
    config = _tdata_module.config
    PasswordDetector = _tdata_module.PasswordDetector
    _find_available_port = _tdata_module._find_available_port
except ImportError:
    from core.config import Config
    config = Config()
    PasswordDetector = None
    def _find_available_port(port): return port

class APIFormatConverter:
    def __init__(self, *args, **kwargs):
        """
        支持无参/带参：
          APIFormatConverter()
          APIFormatConverter(db)
          APIFormatConverter(db, base_url)
          APIFormatConverter(db=db, base_url=base_url)
        """
        db = kwargs.pop('db', None)
        base_url = kwargs.pop('base_url', None)
        if len(args) >= 1 and db is None:
            db = args[0]
        if len(args) >= 2 and base_url is None:
            base_url = args[1]

        self.db = db
        self.base_url = (base_url or os.getenv("BASE_URL") or "http://127.0.0.1:8080").rstrip('/')

        # 运行态
        self.flask_app = None
        self.active_sessions = {}
        self.code_watchers: Dict[str, threading.Thread] = {}
        self.fresh_watch: Dict[str, bool] = {}          # 是否 fresh（由刷新触发）
        self.history_window_sec: Dict[str, int] = {}    # fresh 时回扫窗口（秒）

        # DB 表结构
        try:
            self.init_api_database()
        except Exception as e:
            print("⚠️ 初始化API数据库时出错: %s" % e)

        print("🔗 API格式转换器已初始化，BASE_URL=%s, db=%s" % (self.base_url, "OK" if self.db else "None"))

    # ---------- DB 初始化/迁移 ----------
    def init_api_database(self):
        import sqlite3
        conn = sqlite3.connect(self.db.db_name)
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS api_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT UNIQUE,
                api_key TEXT UNIQUE,
                verification_url TEXT,
                two_fa_password TEXT,
                session_data TEXT,
                tdata_path TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT,
                last_used TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS verification_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT,
                code TEXT,
                code_type TEXT,
                received_at TEXT,
                used INTEGER DEFAULT 0,
                expires_at TEXT
            )
        """)

        # 迁移缺列
        def ensure_col(col, ddl):
            c.execute("PRAGMA table_info(api_accounts)")
            cols = [r[1] for r in c.fetchall()]
            if col not in cols:
                c.execute("ALTER TABLE api_accounts ADD COLUMN %s" % ddl)

        ensure_col("verification_url", "verification_url TEXT")
        ensure_col("two_fa_password", "two_fa_password TEXT")
        ensure_col("session_data", "session_data TEXT")
        ensure_col("tdata_path", "tdata_path TEXT")
        ensure_col("status", "status TEXT DEFAULT 'active'")
        ensure_col("created_at", "created_at TEXT")
        ensure_col("last_used", "last_used TEXT")

        conn.commit()
        conn.close()
        print("✅ API数据库表检查/迁移完成")

    # ---------- 工具 ----------
    def mark_all_codes_used(self, phone: str):
        import sqlite3
        conn = sqlite3.connect(self.db.db_name)
        c = conn.cursor()
        c.execute("UPDATE verification_codes SET used = 1 WHERE phone = ? AND used = 0", (phone,))
        conn.commit()
        conn.close()

    def generate_api_key(self, phone: str) -> str:
        import hashlib, uuid
        data = "%s_%s" % (phone, uuid.uuid4())
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    def generate_verification_url(self, api_key: str) -> str:
        return "%s/verify/%s" % (self.base_url, api_key)

    def save_api_account(
        self,
        phone: str,
        api_key: str,
        verification_url: str,
        two_fa_password: str,
        session_data: str,
        tdata_path: str,
        account_info: dict
    ):
        import sqlite3
        conn = sqlite3.connect(self.db.db_name)
        c = conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO api_accounts
            (phone, api_key, verification_url, two_fa_password, session_data, tdata_path, status, created_at, last_used)
            VALUES(?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """, (
            phone, api_key, verification_url, two_fa_password or "", session_data or "", tdata_path or "",
            datetime.now(BEIJING_TZ).isoformat(), datetime.now(BEIJING_TZ).isoformat()
        ))
        conn.commit()
        conn.close()

    def get_account_by_api_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        import sqlite3
        conn = sqlite3.connect(self.db.db_name)
        c = conn.cursor()
        c.execute("""
            SELECT phone, api_key, verification_url, two_fa_password, session_data, tdata_path, status
            FROM api_accounts WHERE api_key=?
        """, (api_key,))
        row = c.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "phone": row[0],
            "api_key": row[1],
            "verification_url": row[2],
            "two_fa_password": row[3] or "",
            "session_data": row[4] or "",
            "tdata_path": row[5] or "",
            "status": row[6] or "active"
        }

    def save_verification_code(self, phone: str, code: str, code_type: str):
        import sqlite3
        conn = sqlite3.connect(self.db.db_name)
        c = conn.cursor()
        # 去重：5分钟内同手机号同code不重复插入
        cutoff = (datetime.now(BEIJING_TZ) - timedelta(minutes=5)).isoformat()
        c.execute("""
            SELECT id FROM verification_codes
            WHERE phone=? AND code=? AND received_at > ?
            LIMIT 1
        """, (phone, code, cutoff))
        if c.fetchone():
            conn.close()
            print("📱 验证码已存在(跳过): %s - %s" % (phone, code))
            return
        expires_at = (datetime.now(BEIJING_TZ) + timedelta(minutes=10)).isoformat()
        c.execute("""
            INSERT INTO verification_codes (phone, code, code_type, received_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """, (phone, code, code_type, datetime.now(BEIJING_TZ).isoformat(), expires_at))
        conn.commit()
        conn.close()
        print("📱 收到验证码: %s - %s" % (phone, code))

    def get_latest_verification_code(self, phone: str) -> Optional[Dict[str, Any]]:
        import sqlite3
        conn = sqlite3.connect(self.db.db_name)
        c = conn.cursor()
        # 优先返回未用且未过期的最新验证码
        c.execute("""
            SELECT code, code_type, received_at
            FROM verification_codes
            WHERE phone=? AND used=0 AND expires_at > ?
            ORDER BY received_at DESC
            LIMIT 1
        """, (phone, datetime.now(BEIJING_TZ).isoformat()))
        row = c.fetchone()
        if row:
            conn.close()
            return {"code": row[0], "code_type": row[1], "received_at": row[2]}
        # 没有未用的，返回最近一条（不管used状态）
        c.execute("""
            SELECT code, code_type, received_at
            FROM verification_codes
            WHERE phone=?
            ORDER BY received_at DESC
            LIMIT 1
        """, (phone,))
        row = c.fetchone()
        conn.close()
        if row:
            return {"code": row[0], "code_type": row[1], "received_at": row[2]}
        return None

    # ---------- 账号信息提取 ----------
    async def extract_account_info_from_session(self, session_file: str) -> dict:
        """从Session文件提取账号信息"""
        try:
            # Telethon expects session path without .session extension
            session_base = session_file.replace('.session', '') if session_file.endswith('.session') else session_file
            client = TelegramClient(session_base, int(config.API_ID), str(config.API_HASH))
            await client.connect()
            
            if not await client.is_user_authorized():
                await client.disconnect()
                return {"error": "Session未授权"}
            
            me = await client.get_me()
            await client.disconnect()
            
            return {
                "phone": me.phone if me.phone else "unknown",
                "user_id": me.id
            }
            
        except Exception as e:
            return {"error": f"提取失败: {str(e)}"}
    async def extract_account_info_from_tdata(self, tdata_path: str) -> dict:
        if not OPENTELE_AVAILABLE:
            return {"error": "opentele库未安装"}
        try:
            tdesk = TDesktop(tdata_path)
            if not tdesk.isLoaded():
                return {"error": "TData未授权或无效"}
            # 临时session文件保存在sessions/temp目录
            os.makedirs(config.SESSIONS_BAK_DIR, exist_ok=True)
            temp_session_name = "temp_api_%d" % int(time.time())
            temp_session = os.path.join(config.SESSIONS_BAK_DIR, temp_session_name)
            client = await tdesk.ToTelethon(session=temp_session, flag=UseCurrentSession)
            await client.connect()
            me = await client.get_me()
            await client.disconnect()
            # 清理临时session
            for suf in (".session", ".session-journal"):
                p = "%s%s" % (temp_session, suf)
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            return {
                "phone": me.phone,
                "user_id": me.id,
                "username": me.username,
                "first_name": me.first_name,
                "last_name": me.last_name,
                "is_premium": getattr(me, 'premium', False)
            }
        except Exception as e:
            return {"error": "提取失败: %s" % str(e)}

    # ---------- 阶段2：转换为 API 并持久化复制 ----------
    async def convert_to_api_format(
        self,
        files: List[Tuple[str, str]],
        file_type: str,
        override_two_fa: Optional[str] = None
    ) -> List[dict]:
        api_accounts = []
        password_detector = PasswordDetector()
        sessions_dir = config.SESSIONS_DIR
        os.makedirs(sessions_dir, exist_ok=True)

        for file_path, file_name in files:
            try:
                if file_type == "session":
                    info = await self.extract_account_info_from_session(file_path)
                else:
                    info = await self.extract_account_info_from_tdata(file_path)

                if "error" in info:
                    print("❌ 提取失败: %s - %s" % (file_name, info["error"]))
                    continue

                phone = info.get("phone")
                if not phone:
                    print("⚠️ 无法获取手机号: %s" % file_name)
                    continue

                two_fa = override_two_fa or (password_detector.detect_password(file_path, file_type) or "")

                persisted_session = ""
                persisted_tdata = ""

                if file_type == "session":
                    dest = os.path.join(sessions_dir, "%s.session" % phone)
                    try:
                        shutil.copy2(file_path, dest)
                    except Exception:
                        try:
                            if os.path.exists(dest):
                                os.remove(dest)
                            shutil.copy2(file_path, dest)
                        except Exception as e2:
                            print("❌ 复制session失败: %s" % e2)
                            continue
                    persisted_session = dest
                    json_src = file_path.replace(".session", ".json")
                    if os.path.exists(json_src):
                        try:
                            shutil.copy2(json_src, os.path.join(sessions_dir, "%s.json" % phone))
                        except Exception:
                            pass
                else:
                    phone_dir = os.path.join(sessions_dir, phone)
                    tdest = os.path.join(phone_dir, "tdata")
                    try:
                        if os.path.exists(tdest):
                            shutil.rmtree(tdest, ignore_errors=True)
                        os.makedirs(phone_dir, exist_ok=True)
                        shutil.copytree(file_path, tdest)
                    except Exception as e:
                        print("❌ 复制TData失败: %s" % e)
                        continue
                    persisted_tdata = tdest

                api_key = self.generate_api_key(phone)
                vurl = self.generate_verification_url(api_key)

                self.save_api_account(
                    phone=phone,
                    api_key=api_key,
                    verification_url=vurl,
                    two_fa_password=two_fa,
                    session_data=persisted_session,
                    tdata_path=persisted_tdata,
                    account_info=info
                )

                api_accounts.append({
                    "phone": phone,
                    "api_key": api_key,
                    "verification_url": vurl,
                    "two_fa_password": two_fa,
                    "account_info": info,
                    "created_at": datetime.now(BEIJING_TZ).isoformat(),
                    "format_version": "1.0"
                })
                print("✅ 转换成功: %s -> %s" % (phone, vurl))
            except Exception as e:
                print("❌ 处理失败: %s - %s" % (file_name, e))
                continue

        return api_accounts

    def create_api_result_files(self, api_accounts: List[dict], task_id: str, user_id: int = None) -> List[str]:
        out_dir = os.path.join(os.getcwd(), "api_results")
        os.makedirs(out_dir, exist_ok=True)
        
        # Get translated filename if user_id is provided
        if user_id is not None:
            filename = t(user_id, 'api_result_filename').format(count=len(api_accounts))
        else:
            # Fallback to Chinese for backward compatibility
            filename = f"TG_API_{len(api_accounts)}个账号.txt"
        
        out_txt = os.path.join(out_dir, filename)
        with open(out_txt, "w", encoding="utf-8") as f:
            for it in (api_accounts or []):
                f.write("%s\t%s\n" % (it["phone"], it["verification_url"]))
        return [out_txt]

    # ---------- 自动监听 777000 ----------
    def start_code_watch(self, api_key: str, timeout: int = 1800, fresh: bool = False, history_window_sec: int = 0):
        try:
            acc = self.get_account_by_api_key(api_key)
            if not acc:
                return False, "无效的API密钥"

            # 记录模式与回扫窗口；fresh 时清未用旧码
            self.fresh_watch[api_key] = bool(fresh)
            self.history_window_sec[api_key] = int(history_window_sec or 0)
            if fresh:
                try:
                    self.mark_all_codes_used(acc.get("phone", ""))
                except Exception:
                    pass

            # 已在监听则不重复启动（但已更新 fresh/window 配置）
            if api_key in self.code_watchers and self.code_watchers[api_key].is_alive():
                return True, "已在监听"

            def runner():
                import asyncio
                asyncio.run(self._watch_code_async(acc, timeout=timeout, api_key=api_key))

            th = threading.Thread(target=runner, daemon=True)
            self.code_watchers[api_key] = th
            th.start()
            return True, "已开始监听"
        except Exception as e:
            return False, "启动失败: %s" % e

    async def _watch_code_async(self, acc: Dict[str, Any], timeout: int = 1800, api_key: str = ""):
        if not TELETHON_AVAILABLE:
            print("❌ Telethon 未安装，自动监听不可用")
            return

        phone = acc.get("phone", "")
        session_path = acc.get("session_data") or ""
        tdata_path = acc.get("tdata_path") or ""

        client = None
        temp_session_name = None
        try:
            is_fresh = bool(self.fresh_watch.get(api_key, False))
            window_sec = int(self.history_window_sec.get(api_key, 0) or 0)  # 刷新后回扫窗口（秒）

            if session_path and os.path.exists(session_path):
                # Telethon expects session path without .session extension
                session_base = session_path.replace('.session', '') if session_path.endswith('.session') else session_path
                client = TelegramClient(session_base, int(config.API_ID), str(config.API_HASH))
            elif tdata_path and os.path.exists(tdata_path) and OPENTELE_AVAILABLE:
                tdesk = TDesktop(tdata_path)
                if not tdesk.isLoaded():
                    print("⚠️ TData 无法加载: %s" % phone)
                    return
                # 临时session文件保存在sessions/temp目录
                os.makedirs(config.SESSIONS_BAK_DIR, exist_ok=True)
                temp_session_name = os.path.join(config.SESSIONS_BAK_DIR, "watch_%s_%d" % (phone, int(time.time())))
                client = await tdesk.ToTelethon(session=temp_session_name, flag=UseCurrentSession, api=API.TelegramDesktop)
            else:
                print("⚠️ 无可用会话（缺少 session 或 tdata），放弃监听: %s" % phone)
                return

            await client.connect()
            if not await client.is_user_authorized():
                print("⚠️ 会话未授权: %s" % phone)
                await client.disconnect()
                return

            import re as _re
            import asyncio as _aio
            from telethon import events

            def extract_code(text: str):
                if not text:
                    return None
                m = _re.search(r"\b(\d{5,6})\b", text)
                if m:
                    return m.group(1)
                digits = _re.findall(r"\d", text)
                if len(digits) >= 6:
                    return "".join(digits[:6])
                if len(digits) >= 5:
                    return "".join(digits[:5])
                return None

            # 历史回扫：fresh 模式仅回扫最近 window_sec；否则回扫10分钟内
            try:
                entity = await client.get_entity(777000)
                if is_fresh and window_sec > 0:
                    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_sec)
                    found_codes = []
                    async for msg in client.iter_messages(entity, limit=20):
                        msg_dt = msg.date
                        if msg_dt.tzinfo is None:
                            msg_dt = msg_dt.replace(tzinfo=timezone.utc)
                        if msg_dt >= cutoff:
                            code = extract_code(getattr(msg, "raw_text", "") or getattr(msg, "message", ""))
                            if code and code not in found_codes:
                                found_codes.append(code)
                                self.save_verification_code(phone, code, "app")
                                print("[WATCH] 历史回扫发现验证码: %s (时间: %s)" % (code, msg_dt))
                    if found_codes:
                        print("[WATCH] 历史回扫共找到 %d 个验证码，继续实时监听" % len(found_codes))
                elif not is_fresh:
                    found_codes = []
                    async for msg in client.iter_messages(entity, limit=10):
                        msg_dt = msg.date
                        if msg_dt.tzinfo is None:
                            msg_dt = msg_dt.replace(tzinfo=timezone.utc)
                        if datetime.now(timezone.utc) - msg_dt <= timedelta(minutes=10):
                            code = extract_code(getattr(msg, "raw_text", "") or getattr(msg, "message", ""))
                            if code and code not in found_codes:
                                found_codes.append(code)
                                self.save_verification_code(phone, code, "app")
                                print("[WATCH] 历史回扫发现验证码: %s (时间: %s)" % (code, msg_dt))
                    if found_codes:
                        print("[WATCH] 历史回扫共找到 %d 个验证码，继续实时监听" % len(found_codes))
            except Exception as e:
                print("⚠️ 历史读取失败: %s" % e)

            @client.on(events.NewMessage(from_users=777000))
            async def on_code(evt):
                code = extract_code(evt.raw_text or "")
                n_preview = (evt.raw_text or "").replace("\n", " ")[:120]
                print("[WATCH] new msg: %s | code=%s" % (n_preview, code))
                if code:
                    self.save_verification_code(phone, code, "app")
                    print("[WATCH] 验证码已保存: %s -> %s" % (phone, code))

            print("[WATCH] 开始持续监听 777000 消息，账号: %s，超时: %ds" % (phone, timeout))
            try:
                await _aio.wait_for(client.run_until_disconnected(), timeout=timeout)
            except _aio.TimeoutError:
                print("⏱️ 监听超时（%ds），正常退出: %s" % (timeout, phone))
            except Exception as e:
                print("⚠️ 监听异常退出: %s - %s" % (phone, e))
        except Exception as e:
            print("❌ 监听异常 %s: %s" % (phone, e))
        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            if temp_session_name:
                for suf in (".session", ".session-journal"):
                    p = "%s%s" % (temp_session_name, suf)
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass

    # ---------- Web ----------
def start_web_server(self):
    # 不依赖外部 FLASK_AVAILABLE 变量，直接按需导入
    try:
        from flask import Flask, jsonify, request, render_template_string
    except Exception as e:
        print("❌ Flask 未安装或导入失败: %s" % e)
        return

    if getattr(self, "flask_app", None):
        # 已经启动过
        return

    self.flask_app = Flask(__name__)

    @self.flask_app.route('/verify/<api_key>')
    def verification_page(api_key):
        try:
            account = self.get_account_by_api_key(api_key)
            if not account:
                return "❌ 无效的API密钥", 404

            # 若类里有自定义模板方法则调用；否则使用最简模板兜底，避免 500
            if hasattr(self, "render_verification_template"):
                return self.render_verification_template(
                    account['phone'],
                    api_key,
                    account.get('two_fa_password') or ""
                )

            minimal = r'''<!doctype html><meta charset="utf-8">
<title>Verify {{phone}}</title>
<div style="font-family:system-ui;padding:24px;background:#0b0f14;color:#e5e7eb">
  <h2 style="margin:0 0 8px">Top9 验证码接收</h2>
  <div>Phone: {{phone}}</div>
  <div id="status" style="margin:12px 0;padding:10px;border:1px solid #243244;border-radius:8px">读取验证码中…</div>
  <div id="code" style="font-size:40px;font-weight:800;letter-spacing:6px"></div>
</div>
<script>
fetch('/api/start_watch/{{api_key}}',{method:'POST'}).catch(()=>{});
function tick(){
  fetch('/api/get_code/{{api_key}}').then(r=>r.json()).then(d=>{
    if(d.success){document.getElementById('code').textContent=d.code;document.getElementById('status').textContent='验证码已接收';}
    else{document.getElementById('status').textContent='读取验证码中…'}
  }).catch(()=>{});
}
tick(); setInterval(tick,3000);
</script>'''
            return render_template_string(minimal, phone=account['phone'], api_key=api_key)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return "Template error: %s" % str(e), 500

    @self.flask_app.route('/api/get_code/<api_key>')
    def api_get_code(api_key):
        from flask import jsonify
        account = self.get_account_by_api_key(api_key)
        if not account:
            return jsonify({"error": "无效的API密钥"}), 404
        latest = self.get_latest_verification_code(account['phone'])
        if latest:
            return jsonify({
                "success": True,
                "code": latest['code'],
                "type": latest['code_type'],
                "received_at": latest['received_at']
            })
        return jsonify({"success": False, "message": "暂无验证码"})

    @self.flask_app.route('/api/submit_code', methods=['POST'])
    def api_submit_code():
        from flask import request, jsonify
        data = request.json or {}
        phone = data.get('phone')
        code = data.get('code')
        ctype = data.get('type', 'sms')
        if not phone or not code:
            return jsonify({"error": "缺少必要参数"}), 400
        self.save_verification_code(str(phone), str(code), str(ctype))
        return jsonify({"success": True})

    @self.flask_app.route('/api/start_watch/<api_key>', methods=['POST', 'GET'])
    def api_start_watch(api_key):
        # 解析 fresh/window_sec/timeout，容错处理
        from flask import request, jsonify
        q = request.args or {}
        fresh = str(q.get('fresh', '0')).lower() in ('1', 'true', 'yes', 'y', 'on')

        def _safe_float(v, default=0.0):
            try:
                if v is None:
                    return float(default)
                s = str(v).strip()
                import re
                m = re.search(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', s)
                if not m:
                    return float(default)
                return float(m.group(0))
            except Exception:
                return float(default)

        def _safe_int(v, default=0):
            try:
                return int(_safe_float(v, default))
            except Exception:
                return int(default)

        timeout = _safe_int(q.get('timeout', None), 1800)
        window_sec = _safe_int(q.get('window_sec', None), 0)
        ok, msg = self.start_code_watch(api_key, timeout=timeout, fresh=fresh, history_window_sec=window_sec)
        return jsonify({"ok": ok, "message": msg, "timeout": timeout, "window_sec": window_sec})

    @self.flask_app.route('/healthz')
    def healthz():
        from flask import jsonify
        return jsonify({"ok": True, "base_url": self.base_url}), 200

    @self.flask_app.route('/debug/account/<api_key>')
    def debug_account(api_key):
        from flask import jsonify
        acc = self.get_account_by_api_key(api_key)
        return jsonify(acc or {}), (200 if acc else 404)

    # 独立线程启动，避免嵌套函数缩进问题
    t = threading.Thread(target=self._run_server, daemon=True)
    t.start()

def _run_server(self):
    host = os.getenv("API_SERVER_HOST", "0.0.0.0")
    preferred_port = int(os.getenv("API_SERVER_PORT", str(config.WEB_SERVER_PORT)))
    
    # 查找可用端口
    port = preferred_port
    if config.ALLOW_PORT_SHIFT:
        available_port = _find_available_port(preferred_port)
        if available_port and available_port != preferred_port:
            print(f"⚠️ [API-SERVER] 端口 {preferred_port} 被占用，切换到端口 {available_port}")
            port = available_port
            # 更新 base_url
            if hasattr(self, 'base_url'):
                self.base_url = self.base_url.replace(f':{preferred_port}', f':{port}')
        elif not available_port:
            print(f"❌ [API-SERVER] 无法找到可用端口（尝试范围：{preferred_port}-{preferred_port + 20}）")
            print(f"💡 [API-SERVER] 验证码服务器将不会启动，请手动释放端口或关闭 ALLOW_PORT_SHIFT")
            return
    
    print(f"🌐 [API-SERVER] 验证码接收服务器启动: http://{host}:{port} (BASE_URL={self.base_url if hasattr(self, 'base_url') else 'N/A'})")
    try:
        # 这里直接用 self.flask_app.run；Flask 已在 start_web_server 中导入并实例化
        self.flask_app.run(host=host, port=port, debug=False)
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"❌ [API-SERVER] 端口 {port} 仍被占用: {e}")
            print(f"💡 [API-SERVER] 请检查是否有其他进程占用该端口")
        else:
            print(f"❌ [API-SERVER] Flask 服务器启动失败: {e}")
    except Exception as e:
        print(f"❌ [API-SERVER] Flask 服务器运行错误: {e}")
# ========== APIFormatConverter 缩进安全补丁 v2（放在类定义之后、实例化之前）==========
import os, json, threading

# 确保类已定义
try:
    APIFormatConverter
except NameError:
    raise RuntimeError("请把本补丁放在 class APIFormatConverter 定义之后")

# 环境变量助手：去首尾空格/引号
def _afc_env(self, key: str, default: str = "") -> str:
    val = os.getenv(key)
    if val is None:
        return default
    return str(val).strip().strip('"').strip("'")

# 渲染模板：深色主题、内容居中放大、2FA/验证码/手机号复制（HTTPS+回退）、支持 .env 文案、标题模板
def _afc_render_verification_template(self, phone: str, api_key: str, two_fa_password: str = "", status: str = "active") -> str:
    from flask import render_template_string
    import json

    # 解析手机号
    country_code = ""
    national_number = phone or ""
    try:
        import phonenumbers
        p = phone if phone.startswith('+') else '+' + phone
        parsed = phonenumbers.parse(p, None)
        country_code = "+%d" % parsed.country_code
        national_number = str(parsed.national_number)
    except Exception:
        if phone and phone.startswith('+'):
            for i in [3, 2, 1]:
                if len(phone) > i + 4:
                    country_code = phone[:i+1]
                    national_number = phone[i+1:]
                    break

    template = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Telegram Login · {{ phone }}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;
  background:#e8ecf1;
  min-height:100vh;
  display:flex;
  align-items:center;
  justify-content:center;
  padding:16px;
}
.card{
  background:#fff;
  border-radius:16px;
  box-shadow:0 4px 24px rgba(0,0,0,.08);
  width:100%;
  max-width:420px;
  padding:28px 24px;
}
.notice{
  background:#fff8e6;
  border:1px solid #ffe0a0;
  border-radius:10px;
  padding:14px 16px;
  margin-bottom:24px;
  font-size:13px;
  color:#b8860b;
  line-height:1.7;
  font-weight:600;
}
.group{margin-bottom:20px}
.label{font-size:13px;color:#888;margin-bottom:8px;font-weight:700}
.row{
  display:flex;
  align-items:center;
  justify-content:space-between;
  background:#f7f8fa;
  border-radius:10px;
  padding:14px 16px;
  min-height:52px;
  gap:10px;
}
.phone-info{display:flex;align-items:center;gap:6px;flex-wrap:wrap;flex:1;min-width:0}
.pcountry{font-size:18px;font-weight:700;color:#333}
.pnum{font-size:18px;font-weight:700;color:#1565c0}
.tag{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:4px;margin-left:4px;white-space:nowrap}
.tag.ok{color:#4caf50;background:#e8f5e9}
.tag.checking{color:#fff;background:#90a4ae;font-size:11px}
.tag.banned{color:#fff;background:#f44336;font-size:11px}
.tag.unauth{color:#fff;background:#ff9800;font-size:10px}
.tag.off{color:#f44336;background:#fce4ec}
.tag.frozen{color:#fff;background:#ff6f00;font-size:11px}
.tag.multi{color:#fff;background:#7b1fa2;font-size:11px}
.val{font-size:20px;font-weight:700;color:#1565c0;letter-spacing:4px;flex:1}
.val.wait{color:#999;font-size:14px;font-weight:400;letter-spacing:0}
.cbtn{
  background:#f0f0f0;
  border:1px solid #ddd;
  border-radius:6px;
  padding:8px 16px;
  font-size:13px;
  color:#333;
  cursor:pointer;
  white-space:nowrap;
  flex-shrink:0;
  -webkit-tap-highlight-color:transparent;
  transition:background .15s;
}
.cbtn:active{background:#d8d8d8}
.cbtn.ok{background:#e8f5e9;color:#4caf50;border-color:#a5d6a7}
.hint{font-size:12px;color:#f57c00;text-align:right;margin-top:6px}
.status{
  text-align:center;
  padding:14px;
  border-radius:10px;
  font-size:14px;
  font-weight:600;
  margin-bottom:20px;
}
.status.waiting{background:#fff8e6;color:#f5a623}
.status.done{background:#e8f5e9;color:#4caf50}
.toast{
  position:fixed;left:50%;bottom:24px;
  transform:translateX(-50%) translateY(20px);
  background:rgba(0,0,0,.78);color:#fff;
  padding:10px 20px;border-radius:8px;
  font-size:13px;font-weight:600;
  opacity:0;pointer-events:none;z-index:9999;
  transition:opacity .2s,transform .2s;
}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
@media(max-width:480px){
  body{padding:10px}
  .card{padding:22px 16px;border-radius:12px}
  .pcountry,.pnum{font-size:16px}
  .val{font-size:18px}
  .cbtn{padding:8px 12px;font-size:12px}
}
</style>
</head>
<body>
<div class="card">
  <div class="notice">
    记得开启通行密钥 不怕掉线&nbsp;&nbsp;新设备频繁切IP是大忌&nbsp;&nbsp;满24小时在修改资料和密码
  </div>

  <div class="group">
    <div class="label">手机号</div>
    <div class="row">
      <div class="phone-info">
        <span class="pcountry">{{ country_code }}</span>
        <span class="pnum">{{ national_number }}</span>
<span class="tag checking" id="status-tag">检测中...</span>
      </div>
      <button class="cbtn" onclick="cp('{{ phone }}',this)">复制</button>
    </div>
  </div>

  <div class="group">
    <div class="label">登录验证码</div>
    <div id="code-row" class="row">
      <span id="code-val" class="val wait">等待验证码...</span>
      <button class="cbtn" id="copy-code" style="display:none" onclick="cpCode(this)">复制</button>
    </div>
    <div id="code-hint" class="hint" style="display:none"></div>
  </div>

  {% if two_fa_password %}
  <div class="group">
    <div class="label">两步验证 (2FA) 密码</div>
    <div class="row">
      <span class="val">{{ two_fa_password }}</span>
      <button class="cbtn" onclick="cp('{{ two_fa_password }}',this)">复制</button>
    </div>
  </div>
  {% endif %}

  <div id="status" class="status waiting">等待验证码...</div>
</div>

<div id="toast" class="toast"></div>

<script>
var apiKey='{{ api_key }}';
var PREFIX=window.location.pathname.split('/verify/')[0]||'';
var codeValue='';

// 建立 SSE 连接，打开即监听，关闭即停止
var evtSource = new EventSource(PREFIX+'/api/stream/'+apiKey);

evtSource.onmessage = function(e) {
  try {
    var d = JSON.parse(e.data);
    if (d.code && d.code !== codeValue) {
      codeValue = d.code;
      var el = document.getElementById('code-val');
      var hint = document.getElementById('code-hint');
      var st = document.getElementById('status');
      var copyBtn = document.getElementById('copy-code');
      el.textContent = d.code;
      el.className = 'val';
      if (copyBtn) copyBtn.style.display = '';
      if (hint) { hint.style.display = 'block'; hint.textContent = '收到于: ' + (d.time || ''); }
      if (st) { st.className = 'status done'; st.textContent = '验证码已接收 ✓'; }
    }
  } catch(err) {}
};

evtSource.onerror = function() {
  console.warn('SSE 连接断开，浏览器将自动重连');
};

// 关闭页面时断开 SSE
window.addEventListener('beforeunload', function() { evtSource.close(); });

// 复制功能
function cp(t, b) {
  if (!t) return;
  var fallback = function() {
    var a = document.createElement('textarea');
    a.value = t; a.style.position = 'fixed'; a.style.opacity = '0';
    document.body.appendChild(a); a.select();
    try { document.execCommand('copy'); done(b); } catch(e) { toast('复制失败'); }
    document.body.removeChild(a);
  };
  if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(t).then(function() { done(b); }).catch(fallback);
  } else { fallback(); }
}
function done(b) {
  if (!b) return;
  var o = b.textContent;
  b.textContent = '已复制 ✓'; b.classList.add('ok');
  setTimeout(function() { b.textContent = o; b.classList.remove('ok'); }, 1500);
  toast('已复制');
}
function cpCode(b) { if (codeValue) { cp(codeValue, b); } else { toast('暂无验证码'); } }

var toastT = null;
function toast(m, d) {
  var t = document.getElementById('toast');
  if (!t) return;
  t.textContent = m || '';
  t.classList.add('show');
  if (toastT) clearTimeout(toastT);
  toastT = setTimeout(function() { t.classList.remove('show'); }, d || 1500);
}

// 账号状态检测
fetch(PREFIX+'/api/account_status/'+apiKey)
  .then(function(r) { return r.json(); })
  .then(function(d) {
    var tag = document.getElementById('status-tag');
    if (!tag) return;
    if (d.status === 'banned') { tag.textContent = '已封禁'; tag.className = 'tag banned'; }
    else if (d.status === 'unauthorized') { tag.textContent = '授权失效'; tag.className = 'tag unauth'; }
    else if (d.status === 'frozen') { tag.textContent = '已冻结'; tag.className = 'tag frozen'; }
    else if (d.status === 'multi') { tag.textContent = '多设备冲突'; tag.className = 'tag multi'; }
    else { tag.textContent = '正常'; tag.className = 'tag ok'; }
  }).catch(function() {
    var tag = document.getElementById('status-tag');
    if (tag) { tag.textContent = '正常'; tag.className = 'tag ok'; }
  });
</script>
</body>
</html>"""
    return render_template_string(
        template,
        phone=phone,
        api_key=api_key,
        two_fa_password=two_fa_password,
        country_code=country_code,
        national_number=national_number,
        status=status
    )

# Web 服务器（按需导入 Flask）
def _afc_start_web_server(self):
    try:
        from flask import Flask, jsonify, request, render_template_string
    except Exception as e:
        print("❌ Flask 导入失败: %s" % e)
        return

    if getattr(self, "flask_app", None):
        return

    self.flask_app = Flask(__name__)

    @self.flask_app.route('/verify/<api_key>')
    def _verify(api_key):
        try:
            account = self.get_account_by_api_key(api_key)
            if not account:
                return "❌ 无效的API密钥", 404
            return self.render_verification_template(
                account['phone'], api_key, account.get('two_fa_password') or "", account.get('status', 'active')
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            return "Template error: %s" % str(e), 500

    @self.flask_app.route('/api/get_code/<api_key>')
    def _get_code(api_key):
        account = self.get_account_by_api_key(api_key)
        if not account:
            return jsonify({"error":"无效的API密钥"}), 404
        latest = self.get_latest_verification_code(account['phone'])
        if latest:
            return jsonify({"success":True,"code":latest["code"],"type":latest["code_type"],"received_at":latest["received_at"]})
        return jsonify({"success":False,"message":"暂无验证码"})

    @self.flask_app.route('/api/submit_code', methods=['POST'])
    def _submit():
        data = request.json or {}
        phone = data.get('phone'); code = data.get('code'); ctype = data.get('type','sms')
        if not phone or not code:
            return jsonify({"error":"缺少必要参数"}), 400
        self.save_verification_code(str(phone), str(code), str(ctype))
        return jsonify({"success":True})

    @self.flask_app.route('/api/start_watch/<api_key>', methods=['POST','GET'])
    def _start_watch(api_key):
        q = request.args or {}
        def _safe_float(v, default=0.0):
            try:
                if v is None: return float(default)
                import re; m = re.search(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', str(v).strip())
                return float(m.group(0)) if m else float(default)
            except Exception:
                return float(default)
        def _safe_int(v, default=0):
            try: return int(_safe_float(v, default))
            except Exception: return int(default)

        fresh = str(q.get('fresh','0')).lower() in ('1','true','yes','y','on')
        timeout = _safe_int(q.get('timeout', None), 1800)
        window_sec = _safe_int(q.get('window_sec', None), 0)
        ok, msg = self.start_code_watch(api_key, timeout=timeout, fresh=fresh, history_window_sec=window_sec)
        return jsonify({"ok":ok,"message":msg,"timeout":timeout,"window_sec":window_sec})

    @self.flask_app.route('/api/stop_watch/<api_key>', methods=['POST','GET'])
    def _stop_watch(api_key):
        # 停止监听：标记线程应该结束
        if api_key in self.code_watchers:
            # 设置停止标志
            self._stop_watch_flags = getattr(self, '_stop_watch_flags', {})
            self._stop_watch_flags[api_key] = True
            return jsonify({"ok": True, "message": "已停止监听"})
        return jsonify({"ok": True, "message": "未在监听"})

    @self.flask_app.route('/api/account_status/<api_key>')
    def _account_status(api_key):
        """实时检测账号状态"""
        account = self.get_account_by_api_key(api_key)
        if not account:
            return jsonify({"error": "无效的API密钥"}), 404
        
        phone = account['phone']
        session_path = account.get('session_data', '')
        tdata_path = account.get('tdata_path', '')
        old_status = account.get('status', 'active')
        
        # 尝试用 Telethon session 检测
        new_status = old_status
        detail = ""
        
        if session_path and os.path.exists(session_path):
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                
                async def check():
                    from telethon import TelegramClient
                    from telethon.errors import (
                        UserDeactivatedBanError, UserDeactivatedError,
                        AuthKeyUnregisteredError, PhoneNumberBannedError,
                        AuthKeyDuplicatedError,
                    )
                    sess = session_path.replace('.session', '') if session_path.endswith('.session') else session_path
                    client = TelegramClient(sess, int(config.API_ID), str(config.API_HASH))
                    try:
                        await asyncio.wait_for(client.connect(), timeout=15)
                        auth = await asyncio.wait_for(client.is_user_authorized(), timeout=15)
                        if not auth:
                            return 'unauthorized', '账号已掉授权'
                        # 授权成功后检查冻结状态：读取 777000 最近消息
                        try:
                            frozen_keywords = ['your account is frozen', 'account has been frozen', 'account is frozen', 'frozen']
                            msgs = await asyncio.wait_for(client.get_messages(777000, limit=5), timeout=10)
                            for msg in msgs:
                                text = (getattr(msg, 'raw_text', '') or getattr(msg, 'message', '') or '').lower()
                                if any(kw in text for kw in frozen_keywords):
                                    return 'frozen', '账号已冻结'
                        except Exception:
                            pass
                        return 'active', '账号正常'
                    except AuthKeyDuplicatedError:
                        return 'multi', '多设备冲突，授权Key重复'
                    except (UserDeactivatedBanError, PhoneNumberBannedError):
                        return 'banned', '账号已被封禁'
                    except UserDeactivatedError:
                        return 'banned', '账号已被封禁'
                    except AuthKeyUnregisteredError:
                        return 'unauthorized', '授权已失效'
                    except Exception as e:
                        err = str(e).lower()
                        if 'banned' in err or 'deactivated' in err:
                            return 'banned', '账号已被封禁'
                        if 'auth' in err and 'unregistered' in err:
                            return 'unauthorized', '授权已失效'
                        if 'frozen' in err:
                            return 'frozen', '账号已冻结'
                        if 'duplicated' in err or 'duplicate' in err:
                            return 'multi', '多设备冲突'
                        return old_status, str(e)
                    finally:
                        try:
                            await client.disconnect()
                        except:
                            pass
                
                new_status, detail = loop.run_until_complete(check())
                loop.close()
            except Exception as e:
                detail = str(e)
        elif tdata_path and os.path.exists(tdata_path):
            # tdata 类型暂时保持数据库状态，无法直接用 Telethon 检测
            detail = "tdata类型，使用缓存状态"
        else:
            detail = "无session文件"
        
        # 如果状态变了，更新数据库
        if new_status != old_status:
            try:
                import sqlite3
                conn = sqlite3.connect(self.db.db_name)
                conn.execute("UPDATE api_accounts SET status=? WHERE api_key=?", (new_status, api_key))
                conn.commit()
                conn.close()
            except:
                pass
        
        return jsonify({
            "status": new_status,
            "detail": detail,
            "phone": phone
        })

    @self.flask_app.route('/api/stream/<api_key>')
    def _sse_stream(api_key):
        """SSE 实时推送验证码"""
        import json as _json
        import time as _time
        account = self.get_account_by_api_key(api_key)
        if not account:
            return jsonify({"error": "无效的API密钥"}), 404

        phone = account['phone']

        # 确保监听已启动（回扫最近120秒历史消息）
        try:
            self.start_code_watch(api_key, timeout=1800, fresh=False, history_window_sec=120)
        except Exception:
            pass

        def generate():
            # 先推送当前已有的最新验证码（历史）
            latest = self.get_latest_verification_code(phone)
            last_sent_code = None
            def _fmt_time(ts):
                if not ts:
                    return ''
                try:
                    return datetime.fromisoformat(ts).strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    return ts

            if latest and latest.get('code'):
                last_sent_code = latest['code']
                data = _json.dumps({
                    'code': latest['code'],
                    'time': _fmt_time(latest.get('received_at', ''))
                })
                yield 'data: %s\n\n' % data

            # 持续轮询数据库，检测新验证码（每秒检查一次）
            heartbeat_counter = 0
            while True:
                _time.sleep(1)
                heartbeat_counter += 1

                try:
                    current = self.get_latest_verification_code(phone)
                    if current and current.get('code') and current['code'] != last_sent_code:
                        last_sent_code = current['code']
                        data = _json.dumps({
                            'code': current['code'],
                            'time': _fmt_time(current.get('received_at', ''))
                        })
                        yield 'data: %s\n\n' % data
                except Exception:
                    pass

                # 每25秒发送心跳，防止连接超时
                if heartbeat_counter >= 25:
                    heartbeat_counter = 0
                    yield ': heartbeat\n\n'

        from flask import Response, stream_with_context
        return Response(
            stream_with_context(generate()),
            content_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Access-Control-Allow-Origin': '*'
            }
        )

    @self.flask_app.route('/healthz')
    def _healthz():
        return jsonify({"ok":True,"base_url":self.base_url}), 200

    t = threading.Thread(target=self._run_server, daemon=True)
    t.start()

def _afc_run_server(self):
    host = os.getenv("API_SERVER_HOST", "0.0.0.0")
    preferred_port = int(os.getenv("API_SERVER_PORT", str(config.WEB_SERVER_PORT)))
    
    # 查找可用端口
    port = preferred_port
    if config.ALLOW_PORT_SHIFT:
        available_port = _find_available_port(preferred_port)
        if available_port and available_port != preferred_port:
            print(f"⚠️ [CODE_SERVER] 端口 {preferred_port} 被占用，切换到端口 {available_port}")
            port = available_port
            # 更新 base_url
            if hasattr(self, 'base_url'):
                self.base_url = self.base_url.replace(f':{preferred_port}', f':{port}')
        elif not available_port:
            print(f"❌ [CODE_SERVER] 无法找到可用端口（尝试范围：{preferred_port}-{preferred_port + 20}）")
            print(f"💡 [CODE_SERVER] 验证码服务器将不会启动，请手动释放端口或关闭 ALLOW_PORT_SHIFT")
            return
    
    print(f"🌐 [CODE_SERVER] 验证码接收服务器启动: http://{host}:{port} (BASE_URL={self.base_url if hasattr(self, 'base_url') else 'N/A'})")
    try:
        self.flask_app.run(host=host, port=port, debug=False)
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"❌ [CODE_SERVER] 端口 {port} 仍被占用: {e}")
            print(f"💡 [CODE_SERVER] 请检查是否有其他进程占用该端口")
        else:
            print(f"❌ [CODE_SERVER] Flask 服务器启动失败: {e}")
    except Exception as e:
        print(f"❌ [CODE_SERVER] Flask 服务器运行错误: {e}")

# 把方法安全挂到类上（先定义，后挂载；用 hasattr 避免引用未定义名字）
if not hasattr(APIFormatConverter, "_env"):
    APIFormatConverter._env = _afc_env
if not hasattr(APIFormatConverter, "render_verification_template"):
    APIFormatConverter.render_verification_template = _afc_render_verification_template
if not hasattr(APIFormatConverter, "start_web_server"):
    APIFormatConverter.start_web_server = _afc_start_web_server
if not hasattr(APIFormatConverter, "_run_server"):
    APIFormatConverter._run_server = _afc_run_server
# ========== 补丁结束 ==========


# ================================
# 恢复保护工具函数
# ================================

def normalize_phone(phone: Any, default_country_prefix: str = None) -> str:
    """
    规范化电话号码格式，确保返回字符串类型
    
    Args:
        phone: 电话号码（可以是 int、str 或其他类型）
        default_country_prefix: 默认国家前缀（如 '+62'），如果号码缺少国际前缀则添加
    
    Returns:
        规范化后的电话号码字符串
    """
    # 获取默认前缀
    if default_country_prefix is None:
        default_country_prefix = getattr(config, 'FORGET2FA_DEFAULT_COUNTRY_PREFIX', '+62')
    
    # 处理 None 和空值
    if phone is None or phone == "":
        return "unknown"
    
    # 转换为字符串
    phone_str = str(phone).strip()
    
    # 移除空白字符
    phone_str = phone_str.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    
    # 如果为空或是 "unknown"，直接返回
    if not phone_str or phone_str.lower() == "unknown":
        return "unknown"
    
    # 如果已经有 + 前缀，直接返回
    if phone_str.startswith("+"):
        return phone_str
    
    # 如果是纯数字且长度合理（通常手机号10-15位）
    if phone_str.isdigit() and len(phone_str) >= 10:
        # 如果数字很长（可能已包含国家代码），直接添加+
        # 否则使用配置的国家前缀
        if len(phone_str) >= 11:  # 国际号码通常11-15位
            return f"+{phone_str}"
        else:
            # 短号码可能缺少国家代码，使用配置的前缀
            # 去除前缀中的+，然后添加
            prefix = default_country_prefix.lstrip('+')
            return f"+{prefix}{phone_str}"
    
    # 其他情况尝试提取数字
    digits_only = ''.join(c for c in phone_str if c.isdigit())
    if digits_only and len(digits_only) >= 10:
        if len(digits_only) >= 11:
            return f"+{digits_only}"
        else:
            prefix = default_country_prefix.lstrip('+')
            return f"+{prefix}{digits_only}"
    
    # 无法规范化，返回原始字符串
    return phone_str

def _find_available_port(preferred: int = 8080, max_tries: int = 20) -> Optional[int]:
    """
    查找可用端口
    
    Args:
        preferred: 首选端口
        max_tries: 最多尝试次数
    
    Returns:
        可用端口号，如果找不到则返回 None
    """
    import socket
    
    for port in range(preferred, preferred + max_tries):
        sock = None
        try:
            # 尝试绑定端口
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            # 尝试绑定到端口（而不是连接）
            sock.bind(('127.0.0.1', port))
            # 绑定成功，说明端口可用
            return port
        except OSError:
            # 绑定失败（端口被占用），尝试下一个
            continue
        except Exception:
            continue
        finally:
            # 确保socket总是被关闭
            if sock:
                try:
                    sock.close()
                except:
                    pass
    
    return None

# ================================
# 忘记2FA管理器
# ================================

