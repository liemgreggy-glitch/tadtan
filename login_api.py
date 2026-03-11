#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web Login API Service for Telegram Account Bot
Provides web interface and API endpoints for viewing login codes
"""

import os
import asyncio
import json
import secrets
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from threading import Thread

# 定义北京时区常量
BEIJING_TZ = timezone(timedelta(hours=8))

try:
    from aiohttp import web
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    # Create dummy web class to avoid import errors
    class web:
        Application = None
        Request = None
        Response = None
        json_response = None
        AppRunner = None
        TCPSite = None
    print("⚠️ aiohttp未安装，Web Login API功能不可用")
    print("💡 请安装: pip install aiohttp")

try:
    from telethon import TelegramClient, events
    from telethon.tl.functions.account import GetPasswordRequest
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False


@dataclass
class AccountContext:
    """账号上下文信息"""
    token: str
    phone: str
    session_path: str
    api_id: int
    api_hash: str
    client: Optional[Any] = None
    has_2fa: Optional[bool] = None
    last_code: Optional[str] = None
    last_code_at: Optional[datetime] = None
    new_code_event: asyncio.Event = field(default_factory=asyncio.Event)
    is_connected: bool = False
    _connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LoginApiService:
    """Web Login API 服务"""
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8080, public_base_url: str = ""):
        if not AIOHTTP_AVAILABLE:
            raise ImportError("aiohttp is required for LoginApiService")
        
        self.host = host
        self.port = port
        self.public_base_url = public_base_url.rstrip('/')
        self.accounts: Dict[str, AccountContext] = {}
        self.app = None
        self.runner = None
        self.site = None
        self._loop = None
        
        print(f"🌐 Web Login API 服务初始化")
        print(f"   主机: {host}")
        print(f"   端口: {port}")
        if public_base_url:
            print(f"   公开URL: {public_base_url}")
    
    def _create_app(self) -> web.Application:
        """创建 aiohttp 应用"""
        app = web.Application()
        app.router.add_get('/login/{token}', self.handle_login_page)
        app.router.add_get('/api/v1/info/{token}', self.handle_api_info)
        app.router.add_get('/api/v1/code/{token}', self.handle_api_code)
        app.router.add_get('/api/v1/stream/{token}', self.handle_sse_stream)
        app.router.add_get('/healthz', self.handle_healthz)
        return app
    
    async def _start_server(self):
        """启动服务器"""
        try:
            self.app = self._create_app()
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, self.host, self.port)
            await self.site.start()
            print(f"✅ Web Login API 服务已启动在 {self.host}:{self.port}")
        except Exception as e:
            print(f"❌ Web Login API 服务启动失败: {e}")
            raise
    
    def start_background(self):
        """在后台线程中启动服务器"""
        def run_server():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._start_server())
            self._loop.run_forever()
        
        thread = Thread(target=run_server, daemon=True)
        thread.start()
        print("🚀 Web Login API 服务后台线程已启动")
    
    def register_session(self, session_path: str, phone: Optional[str], api_id: int, api_hash: str) -> str:
        """注册一个 session 并返回访问 URL"""
        # 生成唯一 token
        token = secrets.token_urlsafe(16)
        
        # 从 session 路径提取手机号（如果未提供）
        if not phone:
            phone = self._extract_phone_from_path(session_path)
        
        # 创建账号上下文，确保类型正确
        # Note: int(api_id) and str(api_hash) are defensive conversions to prevent TypeError in Telethon
        account = AccountContext(
            token=token,
            phone=phone,
            session_path=session_path,
            api_id=int(api_id) if api_id is not None else 0,
            api_hash=str(api_hash) if api_hash is not None else ""
        )
        
        self.accounts[token] = account
        
        url = self.build_login_url(token)
        print(f"📝 注册 session: {phone} -> {url}")
        
        return url
    
    def build_login_url(self, token: str) -> str:
        """构建登录页面 URL"""
        base = self.public_base_url if self.public_base_url else f"http://{self.host}:{self.port}"
        return f"{base}/login/{token}"
    
    def _extract_phone_from_path(self, session_path: str) -> str:
        """从 session 路径提取手机号"""
        basename = os.path.basename(session_path)
        # 移除 .session 扩展名
        name = basename.replace('.session', '')
        # 如果是数字，假设是手机号
        if name.replace('+', '').replace('_', '').isdigit():
            return name
        return name
    
    async def _ensure_connected(self, account: AccountContext):
        """确保账号已连接到 Telegram"""
        async with account._connect_lock:
            if account.is_connected and account.client:
                return
            
            if not TELETHON_AVAILABLE:
                return
            
            try:
                # 创建客户端
                account.client = TelegramClient(
                    account.session_path,
                    int(account.api_id),
                    str(account.api_hash)
                )
                
                await account.client.connect()
                
                # 检查是否已授权
                if not await account.client.is_user_authorized():
                    account.is_connected = False
                    return
                
                account.is_connected = True
                
                # 检查 2FA 状态
                try:
                    password = await account.client(GetPasswordRequest())
                    account.has_2fa = password.has_password if hasattr(password, 'has_password') else False
                except Exception as e:
                    print(f"⚠️ 检查 2FA 状态失败 {account.phone}: {e}")
                    account.has_2fa = None
                
                # 订阅 777000 消息
                @account.client.on(events.NewMessage(chats=[777000]))
                async def code_handler(event):
                    code = self._extract_code(event.message.message)
                    if code:
                        account.last_code = code
                        account.last_code_at = datetime.now(timezone.utc)
                        account.new_code_event.set()
                        await asyncio.sleep(0)
                        account.new_code_event.clear()
                        print(f"📥 收到验证码 {account.phone}: {code}")
                
                # 获取最近的验证码
                try:
                    messages = await account.client.get_messages(777000, limit=5)
                    for msg in messages:
                        code = self._extract_code(msg.message or "")
                        if code:
                            account.last_code = code
                            msg_time = msg.date
                            if msg_time and msg_time.tzinfo is None:
                                # Telethon returns UTC timestamps as naive datetimes; make them UTC-aware
                                msg_time = msg_time.replace(tzinfo=timezone.utc)
                            account.last_code_at = msg_time
                            break
                except Exception as e:
                    print(f"⚠️ 获取历史消息失败 {account.phone}: {e}")
                
            except Exception as e:
                print(f"❌ 连接失败 {account.phone}: {e}")
                account.is_connected = False
    
    def _extract_code(self, text: str) -> Optional[str]:
        """从消息文本中提取 5-6 位验证码"""
        # 匹配 5-6 位数字
        match = re.search(r'\b(\d{5,6})\b', text)
        return match.group(1) if match else None
    
    async def handle_login_page(self, request: web.Request) -> web.Response:
        """处理登录页面请求"""
        token = request.match_info['token']
        account = self.accounts.get(token)
        
        if not account:
            return web.Response(text="Invalid token", status=404)
        
        # 确保已连接
        await self._ensure_connected(account)
        
        # 生成 HTML
        html = self._generate_login_page_html(account)
        return web.Response(text=html, content_type='text/html')
    
    async def handle_api_info(self, request: web.Request) -> web.Response:
        """处理 API 信息请求"""
        token = request.match_info['token']
        account = self.accounts.get(token)
        
        if not account:
            return web.json_response({'error': 'Invalid token'}, status=404)
        
        # 确保已连接
        await self._ensure_connected(account)
        
        return web.json_response({
            'phone': account.phone,
            'has_2fa': account.has_2fa,
            'last_code': account.last_code,
            'last_code_at': account.last_code_at.isoformat() if account.last_code_at else None
        })
    
    async def handle_api_code(self, request: web.Request) -> web.Response:
        """处理代码轮询请求，支持长轮询"""
        token = request.match_info['token']
        account = self.accounts.get(token)
        
        if not account:
            return web.json_response({'error': 'Invalid token'}, status=404)
        
        # 确保已连接
        await self._ensure_connected(account)
        
        # 获取 wait 参数（长轮询秒数）
        wait = int(request.query.get('wait', '0'))
        wait = max(0, min(wait, 30))  # 限制在 0-30 秒
        
        if wait > 0 and account.is_connected:
            # 长轮询：等待新验证码
            try:
                await asyncio.wait_for(account.new_code_event.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
        
        return web.json_response({
            'last_code': account.last_code,
            'last_code_at': account.last_code_at.isoformat() if account.last_code_at else None
        })
    
    async def handle_sse_stream(self, request: web.Request) -> web.StreamResponse:
        """Server-Sent Events 接口，实时推送验证码"""
        token = request.match_info['token']
        account = self.accounts.get(token)
        if not account:
            return web.Response(text="Invalid token", status=404)

        await self._ensure_connected(account)

        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        response.headers['Access-Control-Allow-Origin'] = '*'
        await response.prepare(request)

        # 先推送当前已有的验证码
        if account.last_code:
            data = json.dumps({
                'code': account.last_code,
                'time': account.last_code_at.astimezone(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S') if account.last_code_at else ''
            })
            await response.write(f"data: {data}\n\n".encode())

        last_sent_code = account.last_code
        try:
            while not request.transport.is_closing():
                try:
                    # Wait up to 25s for a new code; on timeout send a heartbeat to keep the connection alive
                    await asyncio.wait_for(account.new_code_event.wait(), timeout=25)
                except asyncio.TimeoutError:
                    await response.write(b": heartbeat\n\n")
                    continue

                if account.last_code and account.last_code != last_sent_code:
                    last_sent_code = account.last_code
                    data = json.dumps({
                        'code': account.last_code,
                        'time': account.last_code_at.astimezone(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S') if account.last_code_at else ''
                    })
                    await response.write(f"data: {data}\n\n".encode())
        except (ConnectionResetError, asyncio.CancelledError):
            pass

        return response

    async def handle_healthz(self, request: web.Request) -> web.Response:
        """健康检查"""
        return web.Response(text="OK", status=200)
    
    def _generate_login_page_html(self, account: AccountContext) -> str:
        """生成登录页面 HTML - 简洁卡片风格"""
        
        brand_handle = "@PvBot"
        
        # 判断是否有最近的验证码（30分钟内）
        has_recent_code = False
        if account.last_code_at:
            age = datetime.now(timezone.utc) - account.last_code_at
            has_recent_code = (age.total_seconds() / 60) <= 30
        
        # 解析手机号：拆分国家代码和号码
        phone = account.phone or ""
        country_code = ""
        national_number = phone
        try:
            import phonenumbers
            p = phone if phone.startswith('+') else '+' + phone
            parsed = phonenumbers.parse(p, None)
            country_code = f"+{parsed.country_code}"
            national_number = str(parsed.national_number)
        except Exception:
            if phone.startswith('+'):
                for i in [3, 2, 1]:
                    if len(phone) > i + 4:
                        country_code = phone[:i+1]
                        national_number = phone[i+1:]
                        break
        
        # 状态标签
        if account.is_connected:
            status_html = '<span class="tag normal">正常</span>'
        else:
            status_html = '<span class="tag offline">离线</span>'
        
        # 验证码区域
        if has_recent_code and account.last_code:
            code_value = account.last_code
            code_time = account.last_code_at.astimezone(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
            code_section = f'''
                <div class="group">
                    <div class="label">登录验证码</div>
                    <div class="row">
                        <span id="code-val" class="val code">{code_value}</span>
                        <button class="cbtn" id="code-copy-btn" onclick="cp('{code_value}',this)">复制</button>
                    </div>
                    <div class="hint" id="code-time">收到于: {code_time}</div>
                </div>'''
        else:
            code_section = '''
                <div class="group">
                    <div class="label">登录验证码</div>
                    <div class="row">
                        <span id="code-val" class="val wait">等待验证码...</span>
                    </div>
                    <div class="hint" id="code-time">请从 Telegram 客户端触发登录</div>
                </div>'''
        
        # 2FA区域
        twofa_section = ""
        if account.has_2fa:
            twofa_section = '''
                <div class="group">
                    <div class="label">两步验证 (2FA) 密码</div>
                    <div class="row">
                        <span class="val code">••••</span>
                        <button class="cbtn" onclick="cp('',this)">复制</button>
                    </div>
                </div>'''
        
        html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Telegram Login - {account.phone}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;
    background:#e8ecf1;
    min-height:100vh;
    display:flex;
    align-items:center;
    justify-content:center;
    padding:20px;
}}
.lang{{position:fixed;top:16px;right:20px;font-size:13px;color:#666;z-index:100}}
.lang a{{text-decoration:none;color:#666;padding:2px 6px}}
.lang a.on{{color:#333;font-weight:600}}
.lang .s{{color:#ccc}}
.card{{
    background:#fff;
    border-radius:16px;
    box-shadow:0 4px 24px rgba(0,0,0,.08);
    width:100%;
    max-width:420px;
    padding:32px 28px;
}}
.notice{{
    background:#fff8e6;
    border:1px solid #ffe0a0;
    border-radius:10px;
    padding:14px 16px;
    margin-bottom:28px;
    font-size:13px;
    color:#b8860b;
    line-height:1.6;
}}
.group{{margin-bottom:22px}}
.label{{font-size:13px;color:#888;margin-bottom:8px}}
.row{{
    display:flex;
    align-items:center;
    justify-content:space-between;
    background:#f7f8fa;
    border-radius:10px;
    padding:12px 16px;
    min-height:48px;
}}
.val{{font-size:18px;font-weight:700;color:#1a1a2e;letter-spacing:2px}}
.val.code{{color:#1565c0;font-size:22px;letter-spacing:6px}}
.val.wait{{color:#999;font-size:14px;font-weight:400;letter-spacing:0}}
.tag{{
    display:inline-block;
    font-size:12px;
    font-weight:600;
    padding:2px 10px;
    border-radius:4px;
    margin-left:10px;
}}
.tag.normal{{color:#4caf50;background:#e8f5e9}}
.tag.offline{{color:#f44336;background:#fce4ec}}
.pcountry{{font-size:18px;font-weight:700;color:#333}}
.pnum{{font-size:18px;font-weight:700;color:#1565c0}}
.cbtn{{
    background:#f0f0f0;
    border:1px solid #ddd;
    border-radius:6px;
    padding:6px 16px;
    font-size:13px;
    color:#333;
    cursor:pointer;
    transition:all .15s;
    white-space:nowrap;
    flex-shrink:0;
}}
.cbtn:hover{{background:#e4e4e4}}
.cbtn:active{{background:#d8d8d8;transform:scale(.97)}}
.cbtn.ok{{background:#e8f5e9;color:#4caf50;border-color:#a5d6a7}}
.hint{{font-size:12px;color:#aaa;text-align:right;margin-top:6px}}
@media(max-width:480px){{
    body{{padding:12px}}
    .card{{padding:24px 18px;border-radius:12px}}
    .val{{font-size:16px}}
    .val.code{{font-size:20px}}
}}
</style>
</head>
<body>
<div class="lang">
    <a href="#" class="on">中文</a><span class="s">|</span><a href="#">English</a>
</div>
<div class="card">
    <div class="notice">
        记得开启通行密钥 不怕掉线&nbsp;&nbsp;新设备频繁切IP是大忌 满24小时在修改资料和密码
    </div>
    <div class="group">
        <div class="label">手机号</div>
        <div class="row">
            <div>
                <span class="pcountry">{country_code}</span>
                <span class="pnum">&nbsp;{national_number}</span>
                {status_html}
            </div>
            <button class="cbtn" onclick="cp('{phone}',this)">复制</button>
        </div>
    </div>
    {code_section}
    {twofa_section}
</div>
<script>
function cp(t,b){{
    if(!t)return;
    navigator.clipboard.writeText(t).then(()=>{{
        var o=b.textContent;
        b.textContent='已复制 ✓';
        b.classList.add('ok');
        setTimeout(()=>{{b.textContent=o;b.classList.remove('ok')}},1500);
    }}).catch(()=>{{
        var a=document.createElement('textarea');
        a.value=t;document.body.appendChild(a);
        a.select();document.execCommand('copy');
        document.body.removeChild(a);
    }});
}}
var evtSource=new EventSource('/api/v1/stream/{account.token}');
evtSource.onmessage=function(e){{
    var d=JSON.parse(e.data);
    if(d.code){{
        var cv=document.getElementById('code-val');
        var ct=document.getElementById('code-time');
        var cb=document.getElementById('code-copy-btn');
        cv.textContent=d.code;
        cv.className='val code';
        if(ct)ct.textContent='收到于: '+d.time;
        if(cb){{cb.onclick=function(){{cp(d.code,cb)}};}}
    }}
}};
evtSource.onerror=function(){{
    console.error('SSE connection error, browser will auto-reconnect');
}};
window.addEventListener('beforeunload',function(){{evtSource.close();}});
</script>
</body>
</html>"""
        return html