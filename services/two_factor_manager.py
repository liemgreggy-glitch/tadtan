
"""
services.two_factor_manager - Two-factor authentication management
"""
import asyncio
import logging
import os
import random
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from telethon import TelegramClient
    from telethon.errors import (
        SessionPasswordNeededError,
        PasswordHashInvalidError,
        FloodWaitError,
        PhoneNumberBannedError,
        AuthKeyUnregisteredError,
        UserDeactivatedBanError
    )
    from telethon.tl.functions.account import (
        UpdatePasswordSettingsRequest,
        GetPasswordRequest
    )
    from telethon.tl.types import (
        InputCheckPasswordSRP,
        PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow
    )
    import hashlib
    import os as os_module
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    logger.warning("Telethon not available for TwoFactorManager")

try:
    from opentele.api import API, UseCurrentSession
    from opentele.td import TDesktop
    OPENTELE_AVAILABLE = True
except ImportError:
    OPENTELE_AVAILABLE = False
    logger.warning("opentele not available")

from core.config import Config
from core.database import Database
from managers.proxy_manager import ProxyManager
from testers.proxy_tester import ProxyRotator
from utils.helpers import utc_to_beijing


class TwoFactorManager:
    """二步验证管理器"""
    
    def __init__(self, proxy_manager: ProxyManager, db: Database):
        self.proxy_manager = proxy_manager
        self.db = db
        self.config = Config()
        self.proxy_rotator = None
        
        # 初始化代理轮换器
        if self.proxy_manager and self.proxy_manager.proxies:
            self.proxy_rotator = ProxyRotator(self.proxy_manager.proxies)
    
    async def set_2fa(
        self,
        session_path: str,
        password: str,
        hint: Optional[str] = None,
        email: Optional[str] = None
    ) -> Tuple[bool, str]:
        """设置二步验证"""
        client = None
        try:
            # 获取代理
            proxy = None
            if self.proxy_rotator:
                proxy_string = self.proxy_rotator.get_random_proxy()
                if proxy_string:
                    proxy = self._parse_proxy(proxy_string)
            
            # 创建客户端
            client = TelegramClient(
                session_path,
                self.config.API_ID,
                self.config.API_HASH,
                proxy=proxy
            )
            
            await client.connect()
            
            if not await client.is_user_authorized():
                return False, "账号未授权"
            
            # 获取当前密码状态
            password_state = await client(GetPasswordRequest())
            
            if password_state.has_password:
                return False, "账号已设置二步验证"
            
            # 设置新密码
            new_settings = {
                'new_password': password.encode('utf-8'),
                'new_hint': hint or '',
                'email': email or ''
            }
            
            await client(UpdatePasswordSettingsRequest(
                password=InputCheckPasswordSRP(
                    srp_id=0,
                    A=b'',
                    M1=b''
                ),
                new_settings=new_settings
            ))
            
            return True, "二步验证设置成功"
            
        except FloodWaitError as e:
            wait_time = e.seconds
            return False, f"触发限制，需等待 {wait_time} 秒"
            
        except Exception as e:
            logger.error(f"设置二步验证失败: {e}")
            return False, f"设置失败: {str(e)}"
            
        finally:
            if client:
                await client.disconnect()
    
    async def remove_2fa(
        self,
        session_path: str,
        current_password: str
    ) -> Tuple[bool, str]:
        """移除二步验证"""
        client = None
        try:
            # 获取代理
            proxy = None
            if self.proxy_rotator:
                proxy_string = self.proxy_rotator.get_random_proxy()
                if proxy_string:
                    proxy = self._parse_proxy(proxy_string)
            
            # 创建客户端
            client = TelegramClient(
                session_path,
                self.config.API_ID,
                self.config.API_HASH,
                proxy=proxy
            )
            
            await client.connect()
            
            if not await client.is_user_authorized():
                return False, "账号未授权"
            
            # 获取当前密码状态
            password_state = await client(GetPasswordRequest())
            
            if not password_state.has_password:
                return False, "账号未设置二步验证"
            
            # 验证当前密码
            password_hash = self._compute_hash(
                password_state.current_algo,
                current_password
            )
            
            # 移除密码
            await client(UpdatePasswordSettingsRequest(
                password=InputCheckPasswordSRP(
                    srp_id=password_state.srp_id,
                    A=password_hash,
                    M1=b''
                ),
                new_settings={
                    'new_password': b'',
                    'new_hint': '',
                    'email': ''
                }
            ))
            
            return True, "二步验证已移除"
            
        except PasswordHashInvalidError:
            return False, "密码错误"
            
        except FloodWaitError as e:
            wait_time = e.seconds
            return False, f"触发限制，需等待 {wait_time} 秒"
            
        except Exception as e:
            logger.error(f"移除二步验证失败: {e}")
            return False, f"移除失败: {str(e)}"
            
        finally:
            if client:
                await client.disconnect()
    
    async def verify_2fa(
        self,
        session_path: str,
        password: str
    ) -> Tuple[bool, str]:
        """验证二步验证密码"""
        client = None
        try:
            # 获取代理
            proxy = None
            if self.proxy_rotator:
                proxy_string = self.proxy_rotator.get_random_proxy()
                if proxy_string:
                    proxy = self._parse_proxy(proxy_string)
            
            # 创建客户端
            client = TelegramClient(
                session_path,
                self.config.API_ID,
                self.config.API_HASH,
                proxy=proxy
            )
            
            await client.connect()
            
            if not await client.is_user_authorized():
                return False, "账号未授权"
            
            # 获取密码状态
            password_state = await client(GetPasswordRequest())
            
            if not password_state.has_password:
                return False, "账号未设置二步验证"
            
            # 验证密码
            password_hash = self._compute_hash(
                password_state.current_algo,
                password
            )
            
            await client.sign_in(password=password)
            
            return True, "密码正确"
            
        except PasswordHashInvalidError:
            return False, "密码错误"
            
        except Exception as e:
            logger.error(f"验证二步验证失败: {e}")
            return False, f"验证失败: {str(e)}"
            
        finally:
            if client:
                await client.disconnect()
    
    async def batch_set_2fa(
        self,
        sessions: List[str],
        password: str,
        hint: Optional[str] = None,
        email: Optional[str] = None,
        concurrent: int = 5
    ) -> Dict[str, Tuple[bool, str]]:
        """批量设置二步验证"""
        results = {}
        semaphore = asyncio.Semaphore(concurrent)
        
        async def set_with_limit(session_path: str):
            async with semaphore:
                success, message = await self.set_2fa(
                    session_path,
                    password,
                    hint,
                    email
                )
                results[session_path] = (success, message)
                
                # 避免频率限制
                await asyncio.sleep(random.uniform(1, 3))
        
        tasks = [set_with_limit(session) for session in sessions]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        return results
    
    async def batch_remove_2fa(
        self,
        sessions: List[str],
        password: str,
        concurrent: int = 5
    ) -> Dict[str, Tuple[bool, str]]:
        """批量移除二步验证"""
        results = {}
        semaphore = asyncio.Semaphore(concurrent)
        
        async def remove_with_limit(session_path: str):
            async with semaphore:
                success, message = await self.remove_2fa(
                    session_path,
                    password
                )
                results[session_path] = (success, message)
                
                # 避免频率限制
                await asyncio.sleep(random.uniform(1, 3))
        
        tasks = [remove_with_limit(session) for session in sessions]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        return results
    
    def _compute_hash(self, algo, password: str) -> bytes:
        """计算密码哈希"""
        if isinstance(algo, PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow):
            hash1 = hashlib.sha256(
                algo.salt1 + password.encode('utf-8') + algo.salt1
            ).digest()
            
            hash2 = hashlib.sha256(
                algo.salt2 + hash1 + algo.salt2
            ).digest()
            
            return hashlib.pbkdf2_hmac(
                'sha512',
                hash2,
                algo.salt1,
                100000
            )
        
        return b''
    
    def _parse_proxy(self, proxy_string: str) -> Optional[Dict]:
        """解析代理字符串"""
        try:
            parts = proxy_string.strip().split(':')
            if len(parts) < 3:
                return None
            
            proxy_type = parts[0].lower()
            addr = parts[1]
            port = int(parts[2])
            
            proxy_dict = {
                'proxy_type': proxy_type,
                'addr': addr,
                'port': port
            }
            
            if len(parts) >= 4:
                proxy_dict['username'] = parts[3]
            if len(parts) >= 5:
                proxy_dict['password'] = parts[4]
            
            return proxy_dict
            
        except Exception as e:
            logger.error(f"解析代理失败: {e}")
            return None
