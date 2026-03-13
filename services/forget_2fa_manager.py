"""
services.forget_2fa_manager - Forget 2FA password reset manager
"""
import asyncio
import logging
import os
import random
import re
import shutil
import time
import traceback
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.constants import BEIJING_TZ, COOLDOWN_THRESHOLD_SECONDS

logger = logging.getLogger(__name__)

try:
    from telethon import TelegramClient
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    TelegramClient = None

try:
    import socks
    PROXY_SUPPORT = True
except ImportError:
    PROXY_SUPPORT = False

try:
    from opentele.api import API, UseCurrentSession
    from opentele.td import TDesktop
    OPENTELE_AVAILABLE = True
except ImportError:
    OPENTELE_AVAILABLE = False
    API = None
    UseCurrentSession = None
    TDesktop = None

from core.config import Config
from core.database import Database
from managers.proxy_manager import ProxyManager
from testers.proxy_tester import ProxyRotator
from utils.helpers import utc_to_beijing
try:
    from i18n import get_text as t
except ImportError:
    def t(user_id, key, **kwargs): return key

config = Config()

class Forget2FAManager:
    """忘记2FA管理器 - 官方密码重置流程（高速+防封混合模式）"""
    
    # 配置常量 - 平衡速度与防封
    DEFAULT_CONCURRENT_LIMIT = 50      # 并发限制50（批量高速处理）
    DEFAULT_MAX_PROXY_RETRIES = 3      # 代理重试次数为3
    DEFAULT_PROXY_TIMEOUT = 10         # 代理超时时间10秒
    DEFAULT_MIN_DELAY = 3.0            # 批次间最小延迟3秒
    DEFAULT_MAX_DELAY = 6.0            # 批次间最大延迟6秒
    DEFAULT_NOTIFY_WAIT = 0.5          # 等待通知到达的时间
    
    def __init__(self, proxy_manager: ProxyManager, db: Database,
                 concurrent_limit: int = None,
                 max_proxy_retries: int = None,
                 proxy_timeout: int = None,
                 min_delay: float = None,
                 max_delay: float = None,
                 notify_wait: float = None):
        self.proxy_manager = proxy_manager
        self.db = db
        
        # 使用环境变量配置或传入参数或默认值
        self.concurrent_limit = concurrent_limit if concurrent_limit is not None else (getattr(config, 'FORGET2FA_CONCURRENT', None) or self.DEFAULT_CONCURRENT_LIMIT)
        self.max_proxy_retries = max_proxy_retries if max_proxy_retries is not None else (getattr(config, 'FORGET2FA_MAX_PROXY_RETRIES', None) or self.DEFAULT_MAX_PROXY_RETRIES)
        self.proxy_timeout = proxy_timeout if proxy_timeout is not None else (getattr(config, 'FORGET2FA_PROXY_TIMEOUT', None) or self.DEFAULT_PROXY_TIMEOUT)
        self.min_delay = min_delay if min_delay is not None else (getattr(config, 'FORGET2FA_MIN_DELAY', None) or self.DEFAULT_MIN_DELAY)
        self.max_delay = max_delay if max_delay is not None else (getattr(config, 'FORGET2FA_MAX_DELAY', None) or self.DEFAULT_MAX_DELAY)
        self.notify_wait = notify_wait if notify_wait is not None else (getattr(config, 'FORGET2FA_NOTIFY_WAIT', None) or self.DEFAULT_NOTIFY_WAIT)
        
        # 创建代理轮换器（每个账号使用不同代理）
        self.proxy_rotator = ProxyRotator(self.proxy_manager.proxies if self.proxy_manager.proxies else [])
        
        # 创建信号量控制并发
        self.semaphore = asyncio.Semaphore(self.concurrent_limit)
        
        # 打印配置
        print(f"⚡ 忘记2FA管理器初始化（高速+防封模式）:")
        print(f"   - 并发处理: {self.concurrent_limit}个账号/批次")
        print(f"   - 批次间隔: {self.min_delay}-{self.max_delay}秒")
        print(f"   - 代理策略: 每账号轮换，IP不够循环复用")
        print(f"   - 超时重试: 最多{self.max_proxy_retries}次")
        print(f"   - 可用代理: {len(self.proxy_rotator.proxies)}个")
    
    def create_proxy_dict(self, proxy_info: Dict) -> Optional[Dict]:
        """创建代理字典"""
        if not proxy_info:
            return None
        
        try:
            if PROXY_SUPPORT:
                if proxy_info['type'] == 'socks5':
                    proxy_type = socks.SOCKS5
                elif proxy_info['type'] == 'socks4':
                    proxy_type = socks.SOCKS4
                else:
                    proxy_type = socks.HTTP
                
                proxy_dict = {
                    'proxy_type': proxy_type,
                    'addr': proxy_info['host'],
                    'port': proxy_info['port']
                }
                
                if proxy_info.get('username') and proxy_info.get('password'):
                    proxy_dict['username'] = proxy_info['username']
                    proxy_dict['password'] = proxy_info['password']
            else:
                proxy_dict = (proxy_info['host'], proxy_info['port'])
            
            return proxy_dict
            
        except Exception as e:
            print(f"❌ 创建代理配置失败: {e}")
            return None
    
    def format_proxy_string(self, proxy_info: Optional[Dict]) -> str:
        """格式化代理字符串用于显示 - 隐藏详细信息，保护用户隐私"""
        if not proxy_info:
            return "本地连接"
        # 不再暴露具体的代理地址和端口，只显示使用了代理
        return "使用代理"
    
    def format_proxy_string_internal(self, proxy_info: Optional[Dict]) -> str:
        """格式化代理字符串用于内部日志（仅服务器日志，不暴露给用户）"""
        if not proxy_info:
            return "本地连接"
        proxy_type = proxy_info.get('type', 'http')
        host = proxy_info.get('host', '')
        port = proxy_info.get('port', '')
        return f"{proxy_type} {host}:{port}"
    
    @staticmethod
    def mask_proxy_for_display(proxy_used: str, user_id: int = None) -> str:
        """
        隐藏代理详细信息，仅显示是否使用代理
        用于报告文件和进度显示，保护用户代理隐私
        """
        # 如果没有提供user_id，返回默认中文（向后兼容）
        if user_id is None:
            if not proxy_used:
                return "本地连接"
            if "本地连接" in proxy_used or proxy_used == "本地连接":
                return "本地连接"
            return "✅ 使用代理"
        
        # 使用翻译
        if not proxy_used:
            return t(user_id, 'forget_2fa_proxy_local')
        if "本地连接" in proxy_used or proxy_used == "本地连接":
            return t(user_id, 'forget_2fa_proxy_local')
        # 只显示使用了代理，不暴露具体IP/端口
        return t(user_id, 'forget_2fa_proxy_using')
    
    @staticmethod
    def mask_proxy_in_string(text: str) -> str:
        """
        从任意字符串中移除代理详细信息，保护用户代理隐私
        用于报告和日志输出
        """
        import re
        if not text:
            return text
        
        # 匹配各种代理格式的正则表达式
        patterns = [
            # 代理 host:port 格式
            r'代理\s+[a-zA-Z0-9\-_.]+\.[a-zA-Z0-9\-_.]+:\d+',
            # //host:port 格式
            r'//[a-zA-Z0-9\-_.]+\.[a-zA-Z0-9\-_.]+:\d+',
            # http://host:port 格式
            r'https?://[a-zA-Z0-9\-_.]+\.[a-zA-Z0-9\-_.]+:\d+',
            # socks5://host:port 格式
            r'socks[45]?://[a-zA-Z0-9\-_.]+\.[a-zA-Z0-9\-_.]+:\d+',
            # 住宅代理 host:port 格式
            r'住宅代理\s+[a-zA-Z0-9\-_.]+\.[a-zA-Z0-9\-_.]+:\d+',
            # HTTP host:port 格式
            r'HTTP\s+[a-zA-Z0-9\-_.]+\.[a-zA-Z0-9\-_.]+:\d+',
            # SOCKS host:port 格式
            r'SOCKS[45]?\s+[a-zA-Z0-9\-_.]+\.[a-zA-Z0-9\-_.]+:\d+',
            # 一般的 host:port 格式（IP或域名后面跟端口）
            r'\b[a-zA-Z0-9\-_.]+\.(vip|com|net|org|io|xyz|cn):\d+\b',
        ]
        
        result = text
        for pattern in patterns:
            result = re.sub(pattern, '使用代理', result, flags=re.IGNORECASE)
        
        return result
    
    async def check_2fa_status(self, client) -> Tuple[bool, str, Optional[Dict]]:
        """
        检测账号是否设置2FA
        
        Returns:
            (是否有2FA, 状态描述, 密码信息字典)
        """
        try:
            from telethon.tl.functions.account import GetPasswordRequest
            
            pwd_info = await asyncio.wait_for(
                client(GetPasswordRequest()),
                timeout=10
            )
            
            if pwd_info.has_password:
                return True, "账号已设置2FA密码", {
                    'has_password': True,
                    'has_recovery': pwd_info.has_recovery,
                    'hint': pwd_info.hint or ""
                }
            else:
                return False, "账号未设置2FA密码", {'has_password': False}
                
        except Exception as e:
            return False, f"检测2FA状态失败: {str(e)[:50]}", None
    
    async def request_password_reset(self, client) -> Tuple[bool, str, Optional[datetime]]:
        """
        请求重置密码
        
        Returns:
            (是否成功, 状态描述, 冷却期结束时间)
        """
        try:
            from telethon.tl.functions.account import ResetPasswordRequest
            from datetime import timezone
            
            result = await asyncio.wait_for(
                client(ResetPasswordRequest()),
                timeout=15
            )
            
            # 检查结果类型 - 使用类名字符串比较避免导入问题
            result_type = type(result).__name__
            
            if hasattr(result, 'until_date'):
                # ResetPasswordRequestedWait - 正在等待冷却期
                until_date = result.until_date
                
                # 判断是新请求还是已在冷却期
                # 如果until_date距离现在小于6天23小时，说明是已存在的冷却期（不是刚刚请求的）
                # Note: Telegram API returns UTC times, so we use UTC for comparison if timezone-aware
                # Otherwise use naive Beijing time for comparison with naive datetime
                now = datetime.now(timezone.utc) if until_date.tzinfo else datetime.now(BEIJING_TZ).replace(tzinfo=None)
                time_remaining = until_date - now
                
                # 7天 = 604800秒，如果剩余时间少于6天23小时，说明是已在冷却期
                # 但是如果时间已经过期（负数），则冷却期已结束
                remaining_seconds = time_remaining.total_seconds()
                
                if remaining_seconds <= 0:
                    # 冷却期已过，需要再次请求完成重置
                    # 根据 Telegram 官方规则，7天后需要手动再点一次忘记密码才会真正重置
                    logger.info("冷却期已过，自动发起第二次重置请求...")
                    try:
                        second_result = await asyncio.wait_for(
                            client(ResetPasswordRequest()),
                            timeout=15
                        )
                        second_result_type = type(second_result).__name__
                        
                        if second_result_type == 'ResetPasswordOk':
                            return True, "密码已成功重置（冷却期结束后完成）", None
                        elif hasattr(second_result, 'until_date'):
                            # 仍然有冷却期（不太可能，但需要处理）
                            return False, "第二次请求仍在冷却期中", second_result.until_date
                        else:
                            return True, "密码重置请求已提交（冷却期结束后）", None
                    except Exception as e2:
                        logger.warning(f"第二次重置请求失败: {e2}")
                        # 即使第二次请求失败，也返回成功，因为冷却期确实已过
                        return True, f"冷却期已结束，第二次请求遇到问题: {str(e2)[:30]}", None
                elif remaining_seconds < COOLDOWN_THRESHOLD_SECONDS:
                    days_remaining = time_remaining.days
                    hours_remaining = time_remaining.seconds // 3600
                    return False, f"已在冷却期中 (剩余约{days_remaining}天{hours_remaining}小时)", until_date
                else:
                    # 新请求，剩余时间接近7天
                    return True, "已请求密码重置，正在等待冷却期", until_date
            elif result_type == 'ResetPasswordOk':
                # ResetPasswordOk - 密码已被重置（极少见，通常需要等待）
                return True, "密码已成功重置", None
            elif result_type == 'ResetPasswordFailedWait':
                # ResetPasswordFailedWait - 重置请求失败，需要等待
                retry_date = getattr(result, 'retry_date', None)
                return False, f"重置请求失败，需等待后重试", retry_date
            else:
                # 其他情况 - 通常是成功
                return True, "密码重置请求已提交", None
                
        except Exception as e:
            error_msg = str(e).lower()
            if "flood" in error_msg:
                return False, "操作过于频繁，请稍后重试", None
            elif "fresh_reset" in error_msg or "recently" in error_msg:
                return False, "已在冷却期中", None
            else:
                return False, f"请求重置失败: {str(e)[:50]}", None
    
    async def delete_reset_notification(self, client, account_name: str = "") -> bool:
        """
        删除来自777000（Telegram官方）的密码重置通知消息
        
        Args:
            client: TelegramClient实例
            account_name: 账号名称（用于日志）
            
        Returns:
            是否成功删除
        """
        try:
            # 获取777000实体（Telegram官方通知账号）
            entity = await asyncio.wait_for(
                client.get_entity(777000),
                timeout=10
            )
            
            # 获取最近的消息（通常重置通知是最新的几条之一）
            messages = await asyncio.wait_for(
                client.get_messages(entity, limit=10),  # 增加到10条确保覆盖
                timeout=10
            )
            
            deleted_count = 0
            for msg in messages:
                if msg.text:
                    # 检查是否是密码重置通知（多语言匹配，包含更多关键词）
                    text_lower = msg.text.lower()
                    if any(keyword in text_lower for keyword in [
                        # 英文关键词
                        'reset password',
                        'reset your telegram password',
                        'request to reset password',
                        'request to reset',
                        '2-step verification',
                        'two-step verification',
                        'cancel the password reset',
                        'cancel reset request',
                        'password reset request',
                        # 中文关键词
                        '重置密码',
                        '密码重置',
                        '二次验证',
                        '两步验证',
                        '二步验证',
                        '取消密码重置',
                        '取消重置',
                        # 俄语关键词
                        'сброс пароля',
                        'двухфакторн',
                        # 印尼语关键词
                        'reset kata sandi',
                        'verifikasi dua langkah',
                        # 其他语言
                        'réinitialiser',  # 法语
                        'zurücksetzen',    # 德语
                        'restablecer',     # 西班牙语
                    ]):
                        try:
                            await client.delete_messages(entity, msg.id)
                            deleted_count += 1
                            print(f"🗑️ [{account_name}] 已删除重置通知消息 (ID: {msg.id})")
                        except Exception as del_err:
                            print(f"⚠️ [{account_name}] 删除消息失败: {str(del_err)[:30]}")
            
            if deleted_count > 0:
                print(f"✅ [{account_name}] 成功删除 {deleted_count} 条重置通知")
                return True
            else:
                print(f"ℹ️ [{account_name}] 未找到需要删除的重置通知")
                return True  # 没有找到也算成功
                
        except Exception as e:
            print(f"⚠️ [{account_name}] 获取/删除通知失败: {str(e)[:50]}")
            return False
    
    async def connect_with_proxy_fallback(self, file_path: str, account_name: str, file_type: str = 'session') -> Tuple[Optional[TelegramClient], str, bool]:
        """
        使用代理轮换器连接，IP超时自动切换下一个重试（最多3次）
        支持 session 和 tdata 两种格式
        
        Returns:
            (client或None, 代理描述字符串, 是否成功连接)
        """
        # 检查代理是否可用
        proxy_enabled = self.db.get_proxy_enabled() if self.db else True
        use_proxy = config.USE_PROXY and proxy_enabled and len(self.proxy_rotator.proxies) > 0
        
        tried_proxies = []
        
        # 处理 tdata 格式
        if file_type == 'tdata':
            return await self._connect_tdata_with_proxy_fallback(file_path, account_name, use_proxy, tried_proxies)
        
        # 处理 session 格式
        session_base = file_path.replace('.session', '') if file_path.endswith('.session') else file_path
        
        # 优先尝试代理连接 - 使用代理轮换器
        if use_proxy:
            for attempt in range(self.max_proxy_retries):
                # 使用代理轮换器获取下一个代理
                proxy_info = self.proxy_rotator.get_next_proxy()
                if not proxy_info:
                    break
                
                # 使用内部格式用于去重，但不暴露给用户
                proxy_str_internal = self.format_proxy_string_internal(proxy_info)
                if proxy_str_internal in tried_proxies:
                    # 如果已尝试过这个代理，获取下一个
                    continue
                tried_proxies.append(proxy_str_internal)
                
                # 用于显示的代理字符串（隐藏详细信息）
                proxy_str = "使用代理"
                
                proxy_dict = self.create_proxy_dict(proxy_info)
                if not proxy_dict:
                    continue
                
                print(f"🌐 [{account_name}] 尝试代理连接 #{attempt + 1} (轮换)")
                
                client = None
                try:
                    # 住宅代理使用更长超时
                    timeout = config.RESIDENTIAL_PROXY_TIMEOUT if proxy_info.get('is_residential', False) else self.proxy_timeout
                    
                    client = TelegramClient(
                        session_base,
                        int(config.API_ID),
                        str(config.API_HASH),
                        timeout=timeout,
                        connection_retries=1,
                        retry_delay=1,
                        proxy=proxy_dict
                    )
                    
                    await asyncio.wait_for(client.connect(), timeout=timeout)
                    
                    # 检查授权
                    is_authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=5)
                    if not is_authorized:
                        await client.disconnect()
                        return None, proxy_str, False
                    
                    print(f"✅ [{account_name}] 代理连接成功")
                    return client, proxy_str, True
                    
                except asyncio.TimeoutError:
                    print(f"⏱️ [{account_name}] 代理超时，切换下一个...")
                    if client:
                        try:
                            await client.disconnect()
                        except:
                            pass
                except Exception as e:
                    print(f"❌ [{account_name}] 代理连接失败 - {str(e)[:50]}")
                    if client:
                        try:
                            await client.disconnect()
                        except:
                            pass
        
        # 所有代理都失败，回退到本地连接
        print(f"🔄 [{account_name}] 所有代理失败，回退到本地连接...")
        try:
            client = TelegramClient(
                session_base,
                int(config.API_ID),
                str(config.API_HASH),
                timeout=15,
                connection_retries=2,
                retry_delay=1,
                proxy=None
            )
            
            await asyncio.wait_for(client.connect(), timeout=15)
            
            is_authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=5)
            if not is_authorized:
                await client.disconnect()
                return None, "本地连接", False
            
            print(f"✅ [{account_name}] 本地连接成功")
            return client, "本地连接 (代理失败后回退)", True
            
        except Exception as e:
            print(f"❌ [{account_name}] 本地连接也失败: {str(e)[:50]}")
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            return None, "本地连接", False
    
    async def _connect_tdata_with_proxy_fallback(self, tdata_path: str, account_name: str, 
                                                  use_proxy: bool, tried_proxies: list) -> Tuple[Optional[TelegramClient], str, bool]:
        """
        处理TData格式的连接（使用opentele转换）- 使用代理轮换器
        
        Returns:
            (client或None, 代理描述字符串, 是否成功连接)
        """
        if not OPENTELE_AVAILABLE:
            print(f"❌ [{account_name}] opentele库未安装，无法处理TData格式")
            return None, "本地连接", False
        
        # 优先尝试代理连接 - 使用代理轮换器
        if use_proxy:
            for attempt in range(self.max_proxy_retries):
                # 使用代理轮换器获取下一个代理
                proxy_info = self.proxy_rotator.get_next_proxy()
                if not proxy_info:
                    break
                
                # 使用内部格式用于去重，但不暴露给用户
                proxy_str_internal = self.format_proxy_string_internal(proxy_info)
                if proxy_str_internal in tried_proxies:
                    continue
                tried_proxies.append(proxy_str_internal)
                
                # 用于显示的代理字符串（隐藏详细信息）
                proxy_str = "使用代理"
                
                proxy_dict = self.create_proxy_dict(proxy_info)
                if not proxy_dict:
                    continue
                
                print(f"🌐 [{account_name}] TData代理连接 #{attempt + 1} (轮换)")
                
                client = None
                try:
                    # 使用opentele加载TData
                    tdesk = TDesktop(tdata_path)
                    
                    if not tdesk.isLoaded():
                        print(f"❌ [{account_name}] TData未授权或无效")
                        return None, proxy_str, False
                    
                    # 创建临时session名称（保存在sessions/temp目录）
                    os.makedirs(config.SESSIONS_BAK_DIR, exist_ok=True)
                    session_name = os.path.join(config.SESSIONS_BAK_DIR, f"temp_forget2fa_{int(time.time()*1000)}")
                    
                    # 住宅代理使用更长超时
                    timeout = config.RESIDENTIAL_PROXY_TIMEOUT if proxy_info.get('is_residential', False) else self.proxy_timeout
                    
                    # 转换为Telethon客户端（带代理）
                    client = await tdesk.ToTelethon(
                        session=session_name, 
                        flag=UseCurrentSession, 
                        api=API.TelegramDesktop,
                        proxy=proxy_dict
                    )
                    
                    await asyncio.wait_for(client.connect(), timeout=timeout)
                    
                    # 检查授权
                    is_authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=5)
                    if not is_authorized:
                        await client.disconnect()
                        # 清理临时session文件
                        self._cleanup_temp_session(session_name)
                        return None, proxy_str, False
                    
                    print(f"✅ [{account_name}] TData代理连接成功")
                    return client, proxy_str, True
                    
                except asyncio.TimeoutError:
                    print(f"⏱️ [{account_name}] TData代理超时，切换下一个...")
                    if client:
                        try:
                            await client.disconnect()
                        except:
                            pass
                except Exception as e:
                    print(f"❌ [{account_name}] TData代理连接失败 - {str(e)[:50]}")
                    if client:
                        try:
                            await client.disconnect()
                        except:
                            pass
        
        # 所有代理都失败，回退到本地连接
        print(f"🔄 [{account_name}] TData所有代理失败，回退到本地连接...")
        try:
            tdesk = TDesktop(tdata_path)
            
            if not tdesk.isLoaded():
                print(f"❌ [{account_name}] TData未授权或无效")
                return None, "本地连接", False
            
            session_name = f"temp_forget2fa_{int(time.time()*1000)}"
            
            # 转换为Telethon客户端（无代理）
            client = await tdesk.ToTelethon(
                session=session_name, 
                flag=UseCurrentSession, 
                api=API.TelegramDesktop
            )
            
            await asyncio.wait_for(client.connect(), timeout=15)
            
            is_authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=5)
            if not is_authorized:
                await client.disconnect()
                self._cleanup_temp_session(session_name)
                return None, "本地连接", False
            
            print(f"✅ [{account_name}] TData本地连接成功")
            return client, "本地连接 (代理失败后回退)", True
            
        except Exception as e:
            print(f"❌ [{account_name}] TData本地连接也失败: {str(e)[:50]}")
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            return None, "本地连接", False
    
    def _cleanup_temp_session(self, session_name: str):
        """清理临时session文件"""
        try:
            session_file = f"{session_name}.session"
            if os.path.exists(session_file):
                os.remove(session_file)
        except:
            pass
    
    async def process_single_account(self, file_path: str, file_name: str, 
                                     file_type: str, batch_id: str) -> Dict:
        """
        处理单个账号（强制使用代理，失败后回退本地）
        
        Returns:
            结果字典
        """
        start_time = time.time()
        result = {
            'account_name': file_name,
            'phone': '',
            'file_type': file_type,
            'proxy_used': '',
            'status': 'failed',
            'error': '',
            'cooling_until': '',
            'elapsed': 0.0
        }
        
        async with self.semaphore:
            client = None
            try:
                # 1. 连接（优先代理，回退本地）- 支持 session 和 tdata 格式
                client, proxy_used, connected = await self.connect_with_proxy_fallback(
                    file_path, file_name, file_type
                )
                result['proxy_used'] = proxy_used
                
                if not connected or not client:
                    result['status'] = 'failed'
                    result['error'] = '连接失败 (所有代理和本地都失败)'
                    result['elapsed'] = time.time() - start_time
                    self.db.insert_forget_2fa_log(
                        batch_id, file_name, '', file_type, proxy_used,
                        'failed', result['error'], '', result['elapsed']
                    )
                    return result
                
                # 2. 获取用户信息
                try:
                    me = await asyncio.wait_for(client.get_me(), timeout=5)
                    result['phone'] = me.phone or ''
                    user_info = f"ID:{me.id}"
                    if me.username:
                        user_info += f" @{me.username}"
                except Exception as e:
                    user_info = "账号"
                
                # 3. 检测2FA状态
                has_2fa, status_msg, pwd_info = await self.check_2fa_status(client)
                
                if not has_2fa:
                    # 账号没有设置2FA
                    result['status'] = 'no_2fa'
                    result['error'] = status_msg
                    result['elapsed'] = time.time() - start_time
                    self.db.insert_forget_2fa_log(
                        batch_id, file_name, result['phone'], file_type, proxy_used,
                        'no_2fa', status_msg, '', result['elapsed']
                    )
                    print(f"⚠️ [{file_name}] {status_msg}")
                    return result
                
                # 4. 请求密码重置
                success, reset_msg, cooling_until = await self.request_password_reset(client)
                
                if success:
                    result['status'] = 'requested'
                    if cooling_until:
                        # 转换为北京时间显示
                        result['cooling_until'] = utc_to_beijing(cooling_until)
                        result['error'] = f"{reset_msg}，冷却期至: {result['cooling_until']} (北京时间)"
                    else:
                        result['error'] = reset_msg
                    print(f"✅ [{file_name}] {reset_msg}")
                    
                    # 5. 删除来自777000的重置通知消息
                    # 使用可配置的等待时间（默认0.5秒，从原来的2秒减少以提升速度）
                    await asyncio.sleep(self.notify_wait)
                    await self.delete_reset_notification(client, file_name)
                else:
                    # 检查是否已在冷却期
                    if "冷却期" in reset_msg or "recently" in reset_msg.lower():
                        result['status'] = 'cooling'
                        if cooling_until:
                            # 转换为北京时间显示
                            result['cooling_until'] = utc_to_beijing(cooling_until)
                            result['error'] = f"{reset_msg}，冷却期至: {result['cooling_until']} (北京时间)"
                        else:
                            result['error'] = reset_msg
                        print(f"⏳ [{file_name}] {reset_msg}")  # 冷却期使用⏳图标
                    else:
                        result['status'] = 'failed'
                        result['error'] = reset_msg
                        print(f"❌ [{file_name}] {reset_msg}")
                
                result['elapsed'] = time.time() - start_time
                self.db.insert_forget_2fa_log(
                    batch_id, file_name, result['phone'], file_type, proxy_used,
                    result['status'], result['error'], result['cooling_until'], result['elapsed']
                )
                
            except Exception as e:
                result['status'] = 'failed'
                result['error'] = f"处理异常: {str(e)[:50]}"
                result['elapsed'] = time.time() - start_time
                self.db.insert_forget_2fa_log(
                    batch_id, file_name, result['phone'], file_type, result['proxy_used'],
                    'failed', result['error'], '', result['elapsed']
                )
                print(f"❌ [{file_name}] {result['error']}")
            finally:
                if client:
                    try:
                        await client.disconnect()
                    except:
                        pass
            
            return result
    
    async def batch_process_with_progress(self, files: List[Tuple[str, str]], 
                                         file_type: str, 
                                         batch_id: str,
                                         progress_callback=None) -> Dict:
        """
        批量处理（高速+防封混合模式 - 批量并发，每批次间隔3-6秒）
        
        Args:
            files: [(文件路径, 文件名), ...]
            file_type: 'session' 或 'tdata'
            batch_id: 批次ID
            progress_callback: 进度回调函数
            
        Returns:
            结果字典
        """
        results = {
            'requested': [],    # 已请求重置
            'no_2fa': [],       # 无需重置
            'cooling': [],      # 冷却期中
            'failed': []        # 失败
        }
        
        total = len(files)
        processed = [0]  # 使用列表以便在闭包中修改
        start_time = time.time()
        results_lock = asyncio.Lock()  # 用于线程安全地更新results
        
        async def process_single_with_callback(file_path: str, file_name: str):
            """处理单个账号并更新结果"""
            # 处理单个账号
            result = await self.process_single_account(
                file_path, file_name, file_type, batch_id
            )
            
            # 线程安全地更新结果
            async with results_lock:
                processed[0] += 1
                
                # 分类结果
                status = result.get('status', 'failed')
                if status == 'requested':
                    results['requested'].append(result)
                elif status == 'no_2fa':
                    results['no_2fa'].append(result)
                elif status == 'cooling':
                    results['cooling'].append(result)
                else:
                    results['failed'].append(result)
                
                # 调用进度回调
                if progress_callback:
                    elapsed = time.time() - start_time
                    speed = processed[0] / elapsed if elapsed > 0 else 0
                    await progress_callback(processed[0], total, results, speed, elapsed, result)
            
            return result
        
        # 批量并发处理（每批50个，批次间延迟3-6秒）
        batch_size = self.concurrent_limit
        for i in range(0, len(files), batch_size):
            batch = files[i:i + batch_size]
            
            print(f"📦 处理批次 {i//batch_size + 1}/{(len(files)-1)//batch_size + 1}，包含 {len(batch)} 个账号")
            
            # 创建任务列表
            tasks = [
                process_single_with_callback(file_path, file_name)
                for file_path, file_name in batch
            ]
            
            # 并发执行当前批次
            await asyncio.gather(*tasks, return_exceptions=True)
            
            # 批次间延迟（防风控）- 最后一批不延迟
            if i + batch_size < len(files):
                delay = random.uniform(self.min_delay, self.max_delay)
                print(f"⏳ 批次间延迟 {delay:.1f} 秒...")
                await asyncio.sleep(delay)
        
        return results
    
    def create_result_files(self, results: Dict, task_id: str, files: List[Tuple[str, str]], file_type: str, user_id: int = None) -> List[Tuple[str, str, str, int]]:
        """
        生成结果压缩包（按状态分类）
        
        Returns:
            [(zip路径, txt路径, 状态名称, 数量), ...]
        """
        result_files = []
        
        # 如果没有提供user_id，使用默认语言
        if user_id is None:
            user_id = 0  # 使用默认语言
        
        # 状态映射
        status_map = {
            'requested': (t(user_id, 'forget_2fa_status_requested'), '✅'),
            'no_2fa': (t(user_id, 'forget_2fa_status_no_2fa'), '⚠️'),
            'cooling': (t(user_id, 'forget_2fa_status_cooling'), '⏳'),
            'failed': (t(user_id, 'forget_2fa_status_failed'), '❌')
        }
        
        # 创建文件路径映射
        file_path_map = {name: path for path, name in files}
        
        for status_key, items in results.items():
            if not items:
                continue
            
            status_name, emoji = status_map.get(status_key, (status_key, '📄'))
            
            print(f"📦 正在创建 {status_name} 结果文件，包含 {len(items)} 个账号")
            
            # 创建临时目录
            timestamp_short = str(int(time.time()))[-6:]
            status_temp_dir = os.path.join(config.RESULTS_DIR, f"forget2fa_{status_key}_{timestamp_short}")
            os.makedirs(status_temp_dir, exist_ok=True)
            
            try:
                for item in items:
                    account_name = item.get('account_name', '')
                    file_path = file_path_map.get(account_name, '')
                    
                    if not file_path or not os.path.exists(file_path):
                        continue
                    
                    if file_type == 'session':
                        # 复制session文件
                        dest_path = os.path.join(status_temp_dir, account_name)
                        shutil.copy2(file_path, dest_path)
                        
                        # 复制对应的json文件（如果存在）
                        json_name = account_name.replace('.session', '.json')
                        json_path = os.path.join(os.path.dirname(file_path), json_name)
                        if os.path.exists(json_path):
                            shutil.copy2(json_path, os.path.join(status_temp_dir, json_name))
                    
                    elif file_type == 'tdata':
                        # TData格式正确结构: 号码/tdata/D877F783D5D3EF8C
                        # file_path 指向的是 tdata 目录本身
                        # account_name 是号码（如 123456789）
                        
                        # 创建 号码/tdata 目录结构
                        account_dir = os.path.join(status_temp_dir, account_name)
                        tdata_dest_dir = os.path.join(account_dir, "tdata")
                        os.makedirs(tdata_dest_dir, exist_ok=True)
                        
                        # 复制tdata目录内容到 号码/tdata/
                        if os.path.isdir(file_path):
                            for item_name in os.listdir(file_path):
                                src_item = os.path.join(file_path, item_name)
                                dst_item = os.path.join(tdata_dest_dir, item_name)
                                if os.path.isdir(src_item):
                                    shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
                                else:
                                    shutil.copy2(src_item, dst_item)
                        
                        # 同时复制tdata同级目录下的密码文件（如2fa.txt等）
                        parent_dir = os.path.dirname(file_path)
                        for password_file in ['2fa.txt', 'twofa.txt', 'password.txt']:
                            password_path = os.path.join(parent_dir, password_file)
                            if os.path.exists(password_path):
                                shutil.copy2(password_path, os.path.join(account_dir, password_file))
                
                # 创建ZIP文件 - 使用翻译
                zip_key_map = {
                    'requested': 'zip_forget_2fa_reset',
                    'no_2fa': 'zip_forget_2fa_no_reset',
                    'cooling': 'zip_forget_2fa_cooling',
                    'failed': 'zip_forget_2fa_failed'
                }
                zip_key = zip_key_map.get(status_key, 'zip_forget_2fa_reset')
                zip_filename = t(user_id, zip_key).format(count=len(items)) + ".zip"
                zip_path = os.path.join(config.RESULTS_DIR, zip_filename)
                
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files_list in os.walk(status_temp_dir):
                        for file in files_list:
                            file_path_full = os.path.join(root, file)
                            arcname = os.path.relpath(file_path_full, status_temp_dir)
                            zipf.write(file_path_full, arcname)
                
                # 创建TXT报告 - 使用翻译
                report_key_map = {
                    'requested': 'report_forget_2fa_reset',
                    'no_2fa': 'report_forget_2fa_no_reset',
                    'cooling': 'report_forget_2fa_cooling',
                    'failed': 'report_forget_2fa_failed'
                }
                report_key = report_key_map.get(status_key, 'report_forget_2fa_reset')
                txt_filename = t(user_id, report_key).format(count=len(items))
                txt_path = os.path.join(config.RESULTS_DIR, txt_filename)
                
                # 获取报告标题翻译键
                title_key_map = {
                    'requested': 'report_forget_2fa_title_reset',
                    'no_2fa': 'report_forget_2fa_title_no_reset',
                    'cooling': 'report_forget_2fa_title_cooling',
                    'failed': 'report_forget_2fa_title_failed'
                }
                title_key = title_key_map.get(status_key, 'report_forget_2fa_title_reset')
                
                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write(f"{t(user_id, title_key)}\n")
                    f.write("=" * 50 + "\n\n")
                    f.write(f"{t(user_id, 'report_forget_2fa_total').format(count=len(items))}\n")
                    f.write(f"{t(user_id, 'report_forget_2fa_generated').format(time=datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S CST'))}\n\n")
                    
                    f.write(f"{t(user_id, 'report_forget_2fa_detail_list')}\n")
                    f.write("-" * 50 + "\n\n")
                    
                    for idx, item in enumerate(items, 1):
                        f.write(f"{idx}. {emoji} {item.get('account_name', '')}\n")
                        phone = item.get('phone', t(user_id, 'forget_2fa_status_unknown'))
                        f.write(f"   {t(user_id, 'report_forget_2fa_phone').format(phone=phone)}\n")
                        
                        # 状态描述 - 使用正确的翻译键
                        error_msg = item.get('error', status_name)
                        
                        # 根据状态键选择正确的状态翻译
                        if status_key == 'requested':
                            cooling_date = item.get('cooling_until', '')
                            if cooling_date:
                                status_text = t(user_id, 'report_forget_2fa_status_reset_waiting').format(date=cooling_date)
                            else:
                                status_text = t(user_id, 'report_forget_2fa_status_reset_waiting').format(date='N/A')
                        elif status_key == 'no_2fa':
                            if 'detect' in error_msg.lower() or '检测' in error_msg:
                                status_text = t(user_id, 'report_forget_2fa_status_detect_failed').format(error=error_msg)
                            else:
                                status_text = t(user_id, 'report_forget_2fa_status_no_2fa')
                        elif status_key == 'cooling':
                            cooling_date = item.get('cooling_until', '')
                            status_text = t(user_id, 'report_forget_2fa_status_in_cooling').format(date=cooling_date)
                        else:  # failed
                            status_text = t(user_id, 'report_forget_2fa_status_connection_failed')
                        
                        f.write(f"   {status_text}\n")
                        
                        # 隐藏代理详细信息，保护用户隐私
                        masked_proxy = self.mask_proxy_for_display(item.get('proxy_used', t(user_id, 'forget_2fa_status_local')), user_id)
                        f.write(f"   {masked_proxy}\n")
                        
                        if item.get('cooling_until') and status_key != 'requested':
                            f.write(f"   {t(user_id, 'report_forget_2fa_cooling_until').format(date=item.get('cooling_until'))}\n")
                        elapsed_time = f"{item.get('elapsed', 0):.1f}"
                        f.write(f"   {t(user_id, 'report_forget_2fa_duration').format(time=elapsed_time)}\n\n")
                
                print(f"✅ 创建文件: {zip_filename}")
                result_files.append((zip_path, txt_path, status_name, len(items)))
                
            except Exception as e:
                print(f"❌ 创建{status_name}结果文件失败: {e}")
                import traceback
                traceback.print_exc()
            finally:
                # 清理临时目录
                if os.path.exists(status_temp_dir):
                    shutil.rmtree(status_temp_dir, ignore_errors=True)
        
        return result_files

