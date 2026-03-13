"""
services.two_factor_manager - Two-factor authentication management
"""
import asyncio
import json
import logging
import os
import shutil
import traceback
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.constants import BEIJING_TZ

logger = logging.getLogger(__name__)

try:
    from telethon import TelegramClient
    from telethon.errors import FloodWaitError
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False

try:
    import socks
    PROXY_SUPPORT = True
except ImportError:
    PROXY_SUPPORT = False

try:
    pass  # no-op
except ImportError:
    pass

from core.config import Config
from core.database import Database
from core.constants import (
    PROGRESS_UPDATE_INTERVAL, PROGRESS_UPDATE_MIN_PERCENT,
    PROGRESS_UPDATE_MIN_PERCENT_LARGE, PROGRESS_LARGE_BATCH_THRESHOLD,
)
from detectors.password_detector import PasswordDetector
from services.format_converter import FormatConverter
try:
    from i18n import get_text as t
except ImportError:
    def t(user_id, key, **kwargs): return key

config = Config()

class TwoFactorManager:
    """二级密码管理器 - 批量修改2FA密码"""
    
    # 配置常量 - 并发处理数量
    DEFAULT_CONCURRENT_LIMIT = 50  # 默认并发数限制，提升批量处理速度
    
    def __init__(self, proxy_manager: ProxyManager, db: Database):
        self.proxy_manager = proxy_manager
        self.db = db
        self.password_detector = PasswordDetector()
        self.semaphore = asyncio.Semaphore(self.DEFAULT_CONCURRENT_LIMIT)  # 使用配置的并发数
        # 用于存储待处理的2FA任务
        self.pending_2fa_tasks = {}  # {user_id: {'files': [...], 'file_type': '...', 'extract_dir': '...', 'task_id': '...'}}
    
    async def change_2fa_password(self, session_path: str, old_password: str, new_password: str, 
                                  account_name: str, user_id: int = None) -> Tuple[bool, str]:
        """
        修改单个账号的2FA密码
        
        Args:
            session_path: Session文件路径
            old_password: 旧密码
            new_password: 新密码
            account_name: 账号名称（用于日志）
            user_id: 用户ID（用于翻译）
            
        Returns:
            (是否成功, 详细信息)
        """
        if not TELETHON_AVAILABLE:
            return False, "Telethon未安装"
        
        async with self.semaphore:
            client = None
            proxy_dict = None
            proxy_used = "本地连接"
            
            try:
                # 尝试使用代理
                proxy_enabled = self.db.get_proxy_enabled() if self.db else True
                if config.USE_PROXY and proxy_enabled and self.proxy_manager.proxies:
                    proxy_info = self.proxy_manager.get_next_proxy()
                    if proxy_info:
                        proxy_dict = self.create_proxy_dict(proxy_info)
                        if proxy_dict:
                            # 隐藏代理详细信息，保护用户隐私
                            proxy_used = t(user_id, 'report_2fa_using_proxy')
                
                # 创建客户端
                # Telethon expects session path without .session extension
                session_base = session_path.replace('.session', '') if session_path.endswith('.session') else session_path
                client = TelegramClient(
                    session_base,
                    int(config.API_ID),
                    str(config.API_HASH),
                    timeout=config.CONNECTION_TIMEOUT,
                    connection_retries=3,
                    retry_delay=1,
                    proxy=proxy_dict
                )
                
                # 连接
                await asyncio.wait_for(client.connect(), timeout=15)
                
                # 检查授权
                is_authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=5)
                if not is_authorized:
                    return False, f"{proxy_used} | 账号未授权"
                
                # 获取用户信息
                try:
                    me = await asyncio.wait_for(client.get_me(), timeout=5)
                    user_info = f"ID:{me.id}"
                    if me.username:
                        user_info += f" @{me.username}"
                except Exception as e:
                    user_info = "账号"
                
                # 修改2FA密码 - 使用 Telethon 内置方法
                try:
                    # 使用 Telethon 的内置密码修改方法
                    result = await client.edit_2fa(
                        current_password=old_password if old_password else None,
                        new_password=new_password,
                        hint=f"Modified {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d')}"
                    )
                    
                    # 修改成功后，更新文件中的密码
                    json_path = session_path.replace('.session', '.json')
                    has_json = os.path.exists(json_path)
                    
                    update_success = await self._update_password_files(
                        session_path, 
                        new_password, 
                        'session'
                    )
                    
                    if update_success:
                        if has_json:
                            return True, f"{user_info} | {proxy_used} | {t(user_id, 'report_2fa_success_updated')}"
                        else:
                            return True, f"{user_info} | {proxy_used} | {t(user_id, 'report_2fa_success_updated')} {t(user_id, 'status_no_json_found')}"
                    else:
                        return True, f"{user_info} | {proxy_used} | {t(user_id, 'report_2fa_success_updated')} {t(user_id, 'status_file_update_failed')}"
                    
                except AttributeError:
                    # 如果 edit_2fa 不存在，使用手动方法
                    return await self._change_2fa_manual(
                        client, session_path, old_password, new_password, 
                        user_info, proxy_used
                    )
                except Exception as e:
                    error_msg = str(e).lower()
                    if "password" in error_msg and "invalid" in error_msg:
                        return False, f"{user_info} | {proxy_used} | 旧密码错误"
                    elif "password" in error_msg and "incorrect" in error_msg:
                        return False, f"{user_info} | {proxy_used} | 旧密码不正确"
                    elif "flood" in error_msg:
                        return False, f"{user_info} | {proxy_used} | 操作过于频繁，请稍后重试"
                    else:
                        return False, f"{user_info} | {proxy_used} | 修改失败: {str(e)[:50]}"
                
            except Exception as e:
                error_msg = str(e).lower()
                if any(word in error_msg for word in ["timeout", "network", "connection"]):
                    return False, f"{proxy_used} | 网络连接失败"
                else:
                    return False, f"{proxy_used} | 错误: {str(e)[:50]}"
            finally:
                if client:
                    try:
                        await client.disconnect()
                    except:
                        pass
    
    async def _change_2fa_manual(self, client, session_path: str, old_password: str, 
                                 new_password: str, user_info: str, proxy_used: str) -> Tuple[bool, str]:
        """
        手动修改2FA密码（备用方法）
        """
        try:
            from telethon.tl.functions.account import GetPasswordRequest, UpdatePasswordSettingsRequest
            from telethon.tl.types import PasswordInputSettings
            
            # 获取密码配置
            pwd_info = await client(GetPasswordRequest())
            
            # 使用 Telethon 客户端的内置密码处理
            if old_password:
                password_bytes = old_password.encode('utf-8')
            else:
                password_bytes = b''
            
            # 生成新密码
            new_password_bytes = new_password.encode('utf-8')
            
            # 创建密码设置
            new_settings = PasswordInputSettings(
                new_password_hash=new_password_bytes,
                hint=f"Modified {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d')}"
            )
            
            # 尝试更新
            await client(UpdatePasswordSettingsRequest(
                password=password_bytes,
                new_settings=new_settings
            ))
            
            # 更新文件
            json_path = session_path.replace('.session', '.json')
            has_json = os.path.exists(json_path)
            
            update_success = await self._update_password_files(session_path, new_password, 'session')
            
            if update_success:
                if has_json:
                    return True, f"{user_info} | {proxy_used} | {t(None, 'report_2fa_success_updated')}"
                else:
                    return True, f"{user_info} | {proxy_used} | {t(None, 'report_2fa_success_updated')} {t(None, 'status_no_json_found')}"
            else:
                return True, f"{user_info} | {proxy_used} | {t(None, 'report_2fa_success_updated')} {t(None, 'status_file_update_failed')}"
            
        except Exception as e:
            return False, f"{user_info} | {proxy_used} | 手动修改失败: {str(e)[:50]}"
    
    async def remove_2fa_password(self, session_path: str, old_password: str, 
                                  account_name: str = "", file_type: str = 'session',
                                  proxy_dict: Optional[Dict] = None, user_id: int = None) -> Tuple[bool, str]:
        """
        删除2FA密码
        
        Args:
            session_path: Session文件路径
            old_password: 当前的2FA密码
            account_name: 账号名称（用于日志）
            file_type: 文件类型（'session' 或 'tdata'）
            proxy_dict: 代理配置（可选）
            user_id: 用户ID（用于翻译）
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息说明)
        """
        if not TELETHON_AVAILABLE:
            return False, "Telethon未安装"
        
        async with self.semaphore:
            client = None
            # Use translation for proxy_used, with fallback for None user_id
            if user_id:
                proxy_used = t(user_id, 'report_delete_2fa_local_connection')
            else:
                proxy_used = "本地连接"
            
            try:
                # 尝试使用代理
                if not proxy_dict:
                    proxy_enabled = self.db.get_proxy_enabled() if self.db else True
                    if config.USE_PROXY and proxy_enabled and self.proxy_manager.proxies:
                        proxy_info = self.proxy_manager.get_next_proxy()
                        if proxy_info:
                            proxy_dict = self.create_proxy_dict(proxy_info)
                            if proxy_dict:
                                if user_id:
                                    proxy_used = t(user_id, 'report_delete_2fa_using_proxy')
                                else:
                                    proxy_used = "使用代理"
                
                # 创建客户端
                session_base = session_path.replace('.session', '') if session_path.endswith('.session') else session_path
                client = TelegramClient(
                    session_base,
                    int(config.API_ID),
                    str(config.API_HASH),
                    timeout=config.CONNECTION_TIMEOUT,
                    connection_retries=3,
                    retry_delay=1,
                    proxy=proxy_dict
                )
                
                # 连接
                await asyncio.wait_for(client.connect(), timeout=15)
                
                # 检查授权
                is_authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=5)
                if not is_authorized:
                    if user_id:
                        return False, f"{proxy_used} | {t(user_id, 'report_delete_2fa_error_unauthorized')}"
                    else:
                        return False, f"{proxy_used} | 账号未授权"
                
                # 获取用户信息
                try:
                    me = await asyncio.wait_for(client.get_me(), timeout=5)
                    user_info = f"ID:{me.id}"
                    if me.username:
                        user_info += f" @{me.username}"
                except Exception as e:
                    user_info = "账号"
                
                # 删除2FA密码 - 使用 Telethon 的 edit_2fa 方法
                try:
                    # 使用 edit_2fa 删除密码（new_password=None表示删除）
                    result = await client.edit_2fa(
                        current_password=old_password if old_password else None,
                        new_password=None,  # None表示删除密码
                        hint=''
                    )
                    
                    # 删除成功后，更新文件中的密码为空
                    json_path = session_path.replace('.session', '.json')
                    has_json = os.path.exists(json_path)
                    
                    update_success = await self._update_password_files(
                        session_path, 
                        '', 
                        'session'
                    )
                    
                    if update_success:
                        if has_json:
                            if user_id:
                                return True, f"{user_info} | {proxy_used} | {t(user_id, 'report_delete_2fa_success_with_json')}"
                            else:
                                return True, f"{user_info} | {proxy_used} | 2FA密码已删除，文件已更新"
                        else:
                            if user_id:
                                return True, f"{user_info} | {proxy_used} | {t(user_id, 'report_delete_2fa_success_no_json')}"
                            else:
                                return True, f"{user_info} | {proxy_used} | 2FA密码已删除"
                    else:
                        if user_id:
                            return True, f"{user_info} | {proxy_used} | {t(user_id, 'report_delete_2fa_success_update_failed')}"
                        else:
                            return True, f"{user_info} | {proxy_used} | 2FA密码已删除，但文件更新失败"
                    
                except AttributeError:
                    # 如果 edit_2fa 不存在，使用手动方法
                    return await self._remove_2fa_manual(
                        client, session_path, old_password, 
                        user_info, proxy_used, user_id
                    )
                except Exception as e:
                    error_msg = str(e).lower()
                    if "password" in error_msg and ("invalid" in error_msg or "incorrect" in error_msg):
                        if user_id:
                            return False, f"{user_info} | {proxy_used} | {t(user_id, 'report_delete_2fa_error_wrong_password')}"
                        else:
                            return False, f"{user_info} | {proxy_used} | 密码错误"
                    elif "no password" in error_msg or "not set" in error_msg:
                        if user_id:
                            return False, f"{user_info} | {proxy_used} | {t(user_id, 'report_delete_2fa_error_no_2fa')}"
                        else:
                            return False, f"{user_info} | {proxy_used} | 未设置2FA"
                    elif "flood" in error_msg:
                        if user_id:
                            return False, f"{user_info} | {proxy_used} | {t(user_id, 'report_delete_2fa_error_flood')}"
                        else:
                            return False, f"{user_info} | {proxy_used} | 操作过于频繁，请稍后重试"
                    elif any(word in error_msg for word in ["frozen", "deactivated", "banned"]):
                        if user_id:
                            return False, f"{user_info} | {proxy_used} | {t(user_id, 'report_delete_2fa_error_frozen')}"
                        else:
                            return False, f"{user_info} | {proxy_used} | 账号已冻结/封禁"
                    else:
                        if user_id:
                            return False, f"{user_info} | {proxy_used} | {t(user_id, 'report_delete_2fa_error_deletion_failed')}: {str(e)[:50]}"
                        else:
                            return False, f"{user_info} | {proxy_used} | 删除失败: {str(e)[:50]}"
                
            except Exception as e:
                error_msg = str(e).lower()
                if any(word in error_msg for word in ["timeout", "network", "connection"]):
                    if user_id:
                        return False, f"{proxy_used} | {t(user_id, 'report_delete_2fa_error_network')}"
                    else:
                        return False, f"{proxy_used} | 网络连接失败"
                else:
                    if user_id:
                        return False, f"{proxy_used} | {t(user_id, 'report_delete_2fa_error_general')}: {str(e)[:50]}"
                    else:
                        return False, f"{proxy_used} | 错误: {str(e)[:50]}"
            finally:
                if client:
                    try:
                        await client.disconnect()
                    except:
                        pass
    
    async def _remove_2fa_manual(self, client, session_path: str, old_password: str, 
                                 user_info: str, proxy_used: str, user_id: int = None) -> Tuple[bool, str]:
        """
        手动删除2FA密码（备用方法）
        """
        try:
            from telethon.tl.functions.account import GetPasswordRequest, UpdatePasswordSettingsRequest
            from telethon.tl.types import PasswordInputSettings
            
            # 获取密码配置
            pwd_info = await client(GetPasswordRequest())
            
            # 使用旧密码验证
            if old_password:
                password_bytes = old_password.encode('utf-8')
            else:
                password_bytes = b''
            
            # 创建密码设置（删除密码）
            new_settings = PasswordInputSettings(
                new_algo=None,  # 删除密码
                new_password_hash=b'',
                hint=''
            )
            
            # 尝试更新
            await client(UpdatePasswordSettingsRequest(
                password=password_bytes,
                new_settings=new_settings
            ))
            
            # 更新文件
            json_path = session_path.replace('.session', '.json')
            has_json = os.path.exists(json_path)
            
            update_success = await self._update_password_files(session_path, '', 'session')
            
            if update_success:
                if has_json:
                    if user_id:
                        return True, f"{user_info} | {proxy_used} | {t(user_id, 'report_delete_2fa_success_with_json')}"
                    else:
                        return True, f"{user_info} | {proxy_used} | 2FA密码已删除，文件已更新"
                else:
                    if user_id:
                        return True, f"{user_info} | {proxy_used} | {t(user_id, 'report_delete_2fa_success_no_json')}"
                    else:
                        return True, f"{user_info} | {proxy_used} | 2FA密码已删除"
            else:
                if user_id:
                    return True, f"{user_info} | {proxy_used} | {t(user_id, 'report_delete_2fa_success_update_failed')}"
                else:
                    return True, f"{user_info} | {proxy_used} | 2FA密码已删除，但文件更新失败"
            
        except Exception as e:
            if user_id:
                return False, f"{user_info} | {proxy_used} | {t(user_id, 'report_delete_2fa_manual_failed')}: {str(e)[:50]}"
            else:
                return False, f"{user_info} | {proxy_used} | 手动删除失败: {str(e)[:50]}"

    def create_proxy_dict(self, proxy_info: Dict) -> Optional[Dict]:
        """创建代理字典（复用SpamBotChecker的实现）"""
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
    
    async def _update_password_files(self, file_path: str, new_password: str, file_type: str) -> bool:
        """
        更新文件中的密码
        
        Args:
            file_path: 文件路径（session或tdata路径）
            new_password: 新密码
            file_type: 文件类型（'session' 或 'tdata'）
            
        Returns:
            是否更新成功。对于纯Session文件（无JSON），返回True表示成功（非阻塞）
        """
        try:
            if file_type == 'session':
                # 更新Session对应的JSON文件（可选，如果存在）
                json_path = file_path.replace('.session', '.json')
                if os.path.exists(json_path):
                    try:
                        with open(json_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        
                        # 更新密码字段 - 统一使用 twofa 字段，删除其他密码字段
                        # 1. 删除所有旧的密码字段（除了 twofa）
                        old_fields_to_remove = ['twoFA', '2fa', 'password', 'two_fa']
                        removed_fields = []
                        for field in old_fields_to_remove:
                            if field in data:
                                del data[field]
                                removed_fields.append(field)
                        
                        # 2. 设置标准的 twofa 字段
                        data['twofa'] = new_password
                        
                        # 3. 保存更新后的文件
                        with open(json_path, 'w', encoding='utf-8') as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        
                        if removed_fields:
                            print(f"✅ 文件已更新: {os.path.basename(json_path)} - 已删除字段 {removed_fields}，统一使用 twofa 字段")
                        else:
                            print(f"✅ 文件已更新: {os.path.basename(json_path)} - twofa 字段已设置")
                        
                        return True
                            
                    except Exception as e:
                        print(f"❌ 更新JSON文件失败 {os.path.basename(json_path)}: {e}")
                        return False
                else:
                    print(f"ℹ️ JSON文件不存在，跳过JSON更新: {os.path.basename(file_path)}")
                    # 对于纯Session文件，不存在JSON是正常情况，返回True表示不影响密码修改成功
                    return True
                    
            elif file_type == 'tdata':
                # 更新TData目录中的密码文件
                d877_path = os.path.join(file_path, "D877F783D5D3EF8C")
                if not os.path.exists(d877_path):
                    print(f"⚠️ TData目录结构无效: {file_path}")
                    return False
                
                updated = False
                found_files = []
                
                # 方法1: 在整个 tdata 目录搜索现有密码文件
                for password_file_name in ['2fa.txt', 'twofa.txt', 'password.txt']:
                    for root, dirs, files in os.walk(file_path):
                        for file in files:
                            if file.lower() == password_file_name.lower():
                                password_file = os.path.join(root, file)
                                try:
                                    with open(password_file, 'w', encoding='utf-8') as f:
                                        f.write(new_password)
                                    print(f"✅ TData密码文件已更新: {file}")
                                    found_files.append(file)
                                    updated = True
                                except Exception as e:
                                    print(f"❌ 更新密码文件失败 {file}: {e}")
                
                # 方法2: 如果没有找到任何密码文件，创建新的 2fa.txt（与 tdata 同级）
                if not found_files:
                    try:
                        # 获取 tdata 的父目录（与 tdata 同级）
                        parent_dir = os.path.dirname(file_path)
                        new_password_file = os.path.join(parent_dir, "2fa.txt")
                        
                        with open(new_password_file, 'w', encoding='utf-8') as f:
                            f.write(new_password)
                        print(f"✅ TData密码文件已创建: 2fa.txt (位置: 与 tdata 目录同级)")
                        updated = True
                    except Exception as e:
                        print(f"❌ 创建密码文件失败: {e}")
                
                return updated
            
            return False
            
        except Exception as e:
            print(f"❌ 更新文件密码失败: {e}")
            return False
    
    async def batch_change_passwords(self, files: List[Tuple[str, str]], file_type: str, 
                                    old_password: Optional[str], new_password: str,
                                    progress_callback=None, user_id: int = None) -> Dict[str, List[Tuple[str, str, str]]]:
        """
        批量修改密码
        
        Args:
            files: 文件列表 [(路径, 名称), ...]
            file_type: 文件类型（'tdata' 或 'session'）
            old_password: 手动输入的旧密码（备选）
            new_password: 新密码
            progress_callback: 进度回调函数
            user_id: 用户ID（用于翻译）
            
        Returns:
            结果字典 {'成功': [...], '失败': [...]}
        """
        results = {
            "成功": [],
            "失败": []
        }
        
        total = len(files)
        processed = 0
        start_time = time.time()
        
        async def process_single_file(file_path, file_name):
            nonlocal processed
            try:
                # 1. 如果是 TData 格式，需要先转换为 Session
                if file_type == 'tdata':
                    print(f"🔄 TData格式需要先转换为Session: {file_name}")
                    
                    # 使用 FormatConverter 转换
                    converter = FormatConverter(self.db)
                    status, info, name = await converter.convert_tdata_to_session(
                        file_path, 
                        file_name,
                        int(config.API_ID),
                        str(config.API_HASH)
                    )
                    
                    if status != "转换成功":
                        results["失败"].append((file_path, file_name, t(user_id, 'report_2fa_conversion_failed').format(error=info)))
                        processed += 1
                        return
                    
                    # 转换成功，使用生成的 session 文件
                    sessions_dir = config.SESSIONS_DIR
                    phone = file_name  # TData 的名称通常是手机号
                    session_path = os.path.join(sessions_dir, f"{phone}.session")
                    
                    if not os.path.exists(session_path):
                        if user_id:
                            results["失败"].append((file_path, file_name, t(user_id, 'report_delete_2fa_error_session_not_found')))
                        else:
                            results["失败"].append((file_path, file_name, "转换后的Session文件未找到"))
                        processed += 1
                        return
                    
                    print(f"✅ TData已转换为Session: {phone}.session")
                    actual_file_path = session_path
                    actual_file_type = 'session'
                else:
                    actual_file_path = file_path
                    actual_file_type = file_type
                
                # 2. 尝试自动检测密码
                detected_password = self.password_detector.detect_password(file_path, file_type)
                
                # 3. 如果检测失败，使用手动输入的备选密码
                current_old_password = detected_password if detected_password else old_password
                
                if not current_old_password:
                    results["失败"].append((file_path, file_name, t(user_id, 'report_2fa_old_password_not_found')))
                    processed += 1
                    return
                
                # 4. 修改密码（使用 Session 格式）
                success, info = await self.change_2fa_password(
                    actual_file_path, current_old_password, new_password, file_name, user_id
                )
                
                if success:
                    # 如果原始是 TData，需要更新原始 TData 文件
                    if file_type == 'tdata':
                        tdata_update = await self._update_password_files(
                            file_path, new_password, 'tdata'
                        )
                        if tdata_update:
                            info += f" | {t(user_id, 'status_tdata_updated')}"
                    
                    results["成功"].append((file_path, file_name, info))
                    print(f"✅ 修改成功 {processed + 1}/{total}: {file_name}")
                else:
                    results["失败"].append((file_path, file_name, info))
                    print(f"❌ 修改失败 {processed + 1}/{total}: {file_name} - {info}")
                
                processed += 1
                
                # 调用进度回调
                if progress_callback:
                    elapsed = time.time() - start_time
                    speed = processed / elapsed if elapsed > 0 else 0
                    await progress_callback(processed, total, results, speed, elapsed)
                
            except Exception as e:
                if user_id:
                    results["失败"].append((file_path, file_name, f"{t(user_id, 'report_delete_2fa_error_exception')}: {str(e)[:50]}"))
                else:
                    results["失败"].append((file_path, file_name, f"异常: {str(e)[:50]}"))
                processed += 1
                print(f"❌ 处理失败 {processed}/{total}: {file_name} - {str(e)}")
        
        # 批量并发处理（使用配置的并发数）
        semaphore = asyncio.Semaphore(self.DEFAULT_CONCURRENT_LIMIT)
        
        async def process_with_semaphore(file_path, file_name):
            async with semaphore:
                await process_single_file(file_path, file_name)
        
        tasks = [process_with_semaphore(file_path, file_name) for file_path, file_name in files]
        
        # 等待所有任务完成 - 添加超时保护
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=3600  # 1小时超时
            )
        except asyncio.TimeoutError:
            logger.error("批量修改2FA密码超时")
            print("❌ 批量修改2FA密码超时（1小时）")
        
        # 确保最后一次进度回调被调用
        if progress_callback:
            try:
                elapsed = time.time() - start_time
                speed = processed / elapsed if elapsed > 0 else 0
                await progress_callback(processed, total, results, speed, elapsed)
                logger.info(f"修改2FA密码完成: {processed}/{total}")
            except Exception as e:
                logger.error(f"最终进度回调错误: {e}")
        
        return results
    
    async def batch_remove_passwords(self, files: List[Tuple[str, str]], file_type: str, 
                                    old_password: Optional[str],
                                    progress_callback=None, user_id: int = None) -> Dict[str, List[Tuple[str, str, str]]]:
        """
        批量删除2FA密码
        
        Args:
            files: 文件列表 [(路径, 名称), ...]
            file_type: 文件类型（'tdata' 或 'session'）
            old_password: 手动输入的旧密码（备选）
            progress_callback: 进度回调函数
            user_id: 用户ID（用于翻译）
            
        Returns:
            结果字典 {'成功': [...], '失败': [...]}
        """
        results = {
            "成功": [],
            "失败": []
        }
        
        total = len(files)
        processed = 0
        start_time = time.time()
        
        # 智能进度更新控制变量
        last_update_time = 0
        last_update_percent = 0
        
        async def process_single_file(file_path, file_name):
            nonlocal processed
            try:
                # 1. 如果是 TData 格式，需要先转换为 Session
                if file_type == 'tdata':
                    print(f"🔄 TData格式需要先转换为Session: {file_name}")
                    
                    # 使用 FormatConverter 转换
                    converter = FormatConverter(self.db)
                    status, info, name = await converter.convert_tdata_to_session(
                        file_path, 
                        file_name,
                        int(config.API_ID),
                        str(config.API_HASH)
                    )
                    
                    if status != "转换成功":
                        results["失败"].append((file_path, file_name, t(user_id, 'report_2fa_conversion_failed').format(error=info)))
                        processed += 1
                        return
                    
                    # 转换成功，使用生成的 session 文件
                    sessions_dir = config.SESSIONS_DIR
                    phone = file_name  # TData 的名称通常是手机号
                    session_path = os.path.join(sessions_dir, f"{phone}.session")
                    
                    if not os.path.exists(session_path):
                        results["失败"].append((file_path, file_name, "转换后的Session文件未找到"))
                        processed += 1
                        return
                    
                    print(f"✅ TData已转换为Session: {phone}.session")
                    actual_file_path = session_path
                    actual_file_type = 'session'
                else:
                    actual_file_path = file_path
                    actual_file_type = file_type
                
                # 2. 尝试自动检测密码
                detected_password = self.password_detector.detect_password(file_path, file_type)
                
                # 3. 如果检测失败，使用手动输入的备选密码
                current_old_password = detected_password if detected_password else old_password
                
                if not current_old_password:
                    if user_id:
                        results["失败"].append((file_path, file_name, t(user_id, 'report_2fa_old_password_not_found')))
                    else:
                        results["失败"].append((file_path, file_name, "未找到旧密码"))
                    processed += 1
                    return
                
                # 4. 删除密码（使用 Session 格式）
                success, info = await self.remove_2fa_password(
                    actual_file_path, current_old_password, file_name, 
                    file_type=actual_file_type, user_id=user_id
                )
                
                if success:
                    # 如果原始是 TData，需要更新原始 TData 文件
                    if file_type == 'tdata':
                        tdata_update = await self._update_password_files(
                            file_path, '', 'tdata'
                        )
                        if tdata_update:
                            info += " | TData文件已更新"
                    
                    results["成功"].append((file_path, file_name, info))
                    print(f"✅ 删除成功 {processed + 1}/{total}: {file_name}")
                else:
                    results["失败"].append((file_path, file_name, info))
                    print(f"❌ 删除失败 {processed + 1}/{total}: {file_name} - {info}")
                
                processed += 1
                
                # 智能进度回调 - 避免触发 Telegram 限流
                if progress_callback:
                    nonlocal last_update_time, last_update_percent
                    
                    current_time = time.time()
                    current_percent = int(processed / total * 100) if total > 0 else 0
                    
                    # 确定更新策略（大批量降低更新频率）
                    update_interval = PROGRESS_UPDATE_INTERVAL
                    if total >= PROGRESS_LARGE_BATCH_THRESHOLD:
                        percent_step = PROGRESS_UPDATE_MIN_PERCENT_LARGE
                    elif total >= 100:
                        percent_step = PROGRESS_UPDATE_MIN_PERCENT
                    else:
                        percent_step = 1  # 小批量每1%更新
                    
                    # 判断是否应该更新进度
                    time_ok = (current_time - last_update_time) >= update_interval
                    percent_ok = (current_percent - last_update_percent) >= percent_step
                    is_final = (processed == total)
                    
                    should_update = is_final or (time_ok and percent_ok)
                    
                    if should_update:
                        try:
                            elapsed = time.time() - start_time
                            speed = processed / elapsed if elapsed > 0 else 0
                            await progress_callback(processed, total, results, speed, elapsed)
                            last_update_time = current_time
                            last_update_percent = current_percent
                        except FloodWaitError as e:
                            # 被限流时不阻塞，直接跳过本次更新
                            logger.warning(f"进度更新被限流（跳过）: {e.seconds}秒")
                        except Exception as e:
                            # 其他错误也不阻塞处理流程
                            logger.warning(f"进度更新失败（跳过）: {e}")
                
            except Exception as e:
                if user_id:
                    results["失败"].append((file_path, file_name, f"{t(user_id, 'report_delete_2fa_error_exception')}: {str(e)[:50]}"))
                else:
                    results["失败"].append((file_path, file_name, f"异常: {str(e)[:50]}"))
                processed += 1
                print(f"❌ 处理失败 {processed}/{total}: {file_name} - {str(e)}")
        
        # 批量并发处理（使用配置的并发数）
        semaphore = asyncio.Semaphore(self.DEFAULT_CONCURRENT_LIMIT)
        
        async def process_with_semaphore(file_path, file_name):
            async with semaphore:
                await process_single_file(file_path, file_name)
        
        tasks = [process_with_semaphore(file_path, file_name) for file_path, file_name in files]
        
        # 等待所有任务完成 - 添加超时保护
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=3600  # 1小时超时
            )
        except asyncio.TimeoutError:
            logger.error("批量删除2FA超时")
            print("❌ 批量删除2FA超时（1小时）")
        
        # 确保最后一次进度回调被调用
        if progress_callback:
            try:
                elapsed = time.time() - start_time
                speed = processed / elapsed if elapsed > 0 else 0
                await progress_callback(processed, total, results, speed, elapsed)
                logger.info(f"删除2FA完成: {processed}/{total}")
            except FloodWaitError as e:
                logger.warning(f"最终进度回调被限流（跳过）: {e.seconds}秒")
            except Exception as e:
                logger.error(f"最终进度回调错误: {e}")
        
        return results
    
    def create_result_files(self, results: Dict, task_id: str, file_type: str = 'session', user_id: int = None, operation: str = 'change') -> List[Tuple[str, str, str, int]]:
        """
        创建结果文件（修复版 - 分离 ZIP 和 TXT）
        
        Args:
            operation: 操作类型，'change' 表示修改2FA，'remove' 表示删除2FA
        
        Returns:
            [(zip文件路径, txt文件路径, 状态名称, 数量), ...]
        """
        logger.info(f"开始创建结果文件: task_id={task_id}, file_type={file_type}, operation={operation}")
        result_files = []
        
        for status, items in results.items():
            if not items:
                continue
            
            logger.info(f"📦 正在创建 {status} 结果文件，包含 {len(items)} 个账号")
            print(f"📦 正在创建 {status} 结果文件，包含 {len(items)} 个账号")
            
            # 为每个状态创建唯一的临时目录
            timestamp_short = str(int(time.time()))[-6:]
            status_temp_dir = os.path.join(config.RESULTS_DIR, f"{status}_{timestamp_short}")
            os.makedirs(status_temp_dir, exist_ok=True)
            
            # 确保每个账号有唯一目录名
            used_names = set()
            
            try:
                logger.info(f"开始复制文件到临时目录: {status_temp_dir}")
                for index, (file_path, file_name, info) in enumerate(items):
                    if file_type == "session":
                        # 复制 session 文件
                        dest_path = os.path.join(status_temp_dir, file_name)
                        if os.path.exists(file_path):
                            shutil.copy2(file_path, dest_path)
                            print(f"📄 复制Session文件: {file_name}")
                        
                        # 查找对应的 json 文件（如果存在）
                        json_name = file_name.replace('.session', '.json')
                        json_path = os.path.join(os.path.dirname(file_path), json_name)
                        if os.path.exists(json_path):
                            json_dest = os.path.join(status_temp_dir, json_name)
                            shutil.copy2(json_path, json_dest)
                            print(f"📄 复制JSON文件: {json_name}")
                        else:
                            print(f"ℹ️ 无JSON文件: {file_name} (纯Session文件)")
                    
                    elif file_type == "tdata":
                        # 使用原始文件夹名称（通常是手机号）
                        original_name = file_name
                        
                        # 确保名称唯一性
                        unique_name = original_name
                        counter = 1
                        while unique_name in used_names:
                            unique_name = f"{original_name}_{counter}"
                            counter += 1
                        
                        used_names.add(unique_name)
                        
                        # 创建 手机号/ 目录（与转换模块一致）
                        phone_dir = os.path.join(status_temp_dir, unique_name)
                        os.makedirs(phone_dir, exist_ok=True)
                        
                        # 1. 复制 tdata 目录
                        target_dir = os.path.join(phone_dir, "tdata")
                        
                        # 复制 TData 文件（使用正确的递归复制）
                        if os.path.exists(file_path) and os.path.isdir(file_path):
                            # 遍历 TData 目录
                            for item in os.listdir(file_path):
                                item_path = os.path.join(file_path, item)
                                dest_item_path = os.path.join(target_dir, item)
                                
                                if os.path.isdir(item_path):
                                    # 递归复制目录
                                    shutil.copytree(item_path, dest_item_path, dirs_exist_ok=True)
                                else:
                                    # 复制文件
                                    os.makedirs(target_dir, exist_ok=True)
                                    shutil.copy2(item_path, dest_item_path)
                            
                            print(f"📂 复制TData: {unique_name}/tdata/")
                        
                        # 2. 复制密码文件（从 tdata 的父目录，即与 tdata 同级）
                        parent_dir = os.path.dirname(file_path)
                        for password_file_name in ['2fa.txt', 'twofa.txt', 'password.txt']:
                            password_file_path = os.path.join(parent_dir, password_file_name)
                            if os.path.exists(password_file_path):
                                # 复制到 手机号/ 目录下（与 tdata 同级）
                                dest_password_path = os.path.join(phone_dir, password_file_name)
                                shutil.copy2(password_file_path, dest_password_path)
                                print(f"📄 复制密码文件: {unique_name}/{password_file_name}")
                
                # 创建 ZIP 文件 - 新格式
                logger.info(f"开始打包ZIP文件: {status}, {len(items)} 个文件")
                # Use translation for ZIP filename based on operation
                if operation == 'remove':
                    # Delete 2FA operation
                    if status == "成功":
                        zip_filename = t(user_id, 'zip_delete_2fa_success').format(count=len(items)) + '.zip'
                    else:  # 失败
                        zip_filename = t(user_id, 'zip_delete_2fa_failed').format(count=len(items)) + '.zip'
                else:
                    # Change 2FA operation (default)
                    if status == "成功":
                        zip_filename = t(user_id, 'zip_change_2fa_success').format(count=len(items)) + '.zip'
                    else:  # 失败
                        zip_filename = t(user_id, 'zip_change_2fa_failed').format(count=len(items)) + '.zip'
                zip_path = os.path.join(config.RESULTS_DIR, zip_filename)
                
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files_list in os.walk(status_temp_dir):
                        for file in files_list:
                            file_path_full = os.path.join(root, file)
                            # 使用相对路径，避免重复
                            arcname = os.path.relpath(file_path_full, status_temp_dir)
                            zipf.write(file_path_full, arcname)
                
                logger.info(f"✅ ZIP文件创建成功: {zip_filename}")
                print(f"✅ 创建ZIP文件: {zip_filename}")
                
                # 创建 TXT 报告 - 新格式
                logger.info(f"开始创建TXT报告: {status}")
                # Use translation for TXT filename based on operation
                if operation == 'remove':
                    # Delete 2FA operation
                    if status == "成功":
                        txt_filename = t(user_id, 'report_delete_2fa_success').format(count=len(items))
                    else:  # 失败
                        txt_filename = t(user_id, 'report_delete_2fa_failed').format(count=len(items))
                else:
                    # Change 2FA operation (default)
                    if status == "成功":
                        txt_filename = t(user_id, 'report_change_2fa_success').format(count=len(items))
                    else:  # 失败
                        txt_filename = t(user_id, 'report_change_2fa_failed').format(count=len(items))
                txt_path = os.path.join(config.RESULTS_DIR, txt_filename)
                
                with open(txt_path, 'w', encoding='utf-8') as f:
                    # Use translation for report title based on operation
                    if operation == 'remove':
                        # Delete 2FA operation
                        if status == "成功":
                            f.write(t(user_id, 'report_delete_2fa_title_success') + "\n")
                        else:  # 失败
                            f.write(t(user_id, 'report_delete_2fa_title_failed') + "\n")
                        f.write("=" * 50 + "\n\n")
                        f.write(t(user_id, 'report_delete_2fa_total').format(count=len(items)) + "\n\n")
                        f.write(t(user_id, 'report_delete_2fa_generated').format(time=datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S CST')) + "\n")
                        
                        f.write(t(user_id, 'report_delete_2fa_detail_list') + "\n")
                        f.write("-" * 50 + "\n\n")
                        
                        for idx, (file_path, file_name, info) in enumerate(items, 1):
                            # 隐藏代理详细信息，保护用户隐私
                            masked_info = Forget2FAManager.mask_proxy_in_string(info)
                            f.write(f"{idx}. {t(user_id, 'report_delete_2fa_account').format(account=file_name)}\n")
                            f.write(f"   {t(user_id, 'report_delete_2fa_details').format(info=masked_info)}\n")
                            f.write(f"   {t(user_id, 'report_delete_2fa_process_time').format(time=datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S CST'))}\n\n")
                        
                        # 如果是失败列表，添加解决方案
                        if status == "失败":
                            f.write("\n" + "=" * 50 + "\n")
                            f.write(t(user_id, 'report_delete_2fa_failure_analysis') + "\n")
                            f.write("-" * 50 + "\n\n")
                            f.write(f"1. {t(user_id, 'report_delete_2fa_reason_unauthorized')}\n")
                            f.write(f"   - {t(user_id, 'report_delete_2fa_reason_unauthorized_desc1')}\n")
                            f.write(f"   - {t(user_id, 'report_delete_2fa_reason_unauthorized_desc2')}\n\n")
                            f.write(f"2. {t(user_id, 'report_delete_2fa_reason_wrong_password')}\n")
                            f.write(f"   - {t(user_id, 'report_delete_2fa_reason_wrong_password_desc1')}\n")
                            f.write(f"   - {t(user_id, 'report_delete_2fa_reason_wrong_password_desc2')}\n\n")
                            f.write(f"3. {t(user_id, 'report_delete_2fa_reason_network')}\n")
                            f.write(f"   - {t(user_id, 'report_delete_2fa_reason_network_desc1')}\n")
                            f.write(f"   - {t(user_id, 'report_delete_2fa_reason_network_desc2')}\n\n")
                    else:
                        # Change 2FA operation (default)
                        if status == "成功":
                            f.write(t(user_id, 'report_2fa_title_success') + "\n")
                        else:  # 失败
                            f.write(t(user_id, 'report_2fa_title_failed') + "\n")
                        f.write("=" * 50 + "\n\n")
                        f.write(t(user_id, 'report_2fa_total').format(count=len(items)) + "\n\n")
                        f.write(t(user_id, 'report_2fa_generated').format(time=datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S CST')) + "\n")
                        
                        f.write(t(user_id, 'report_2fa_detail_list') + "\n")
                        f.write("-" * 50 + "\n\n")
                        
                        for idx, (file_path, file_name, info) in enumerate(items, 1):
                            # 隐藏代理详细信息，保护用户隐私
                            masked_info = Forget2FAManager.mask_proxy_in_string(info)
                            f.write(f"{idx}. {t(user_id, 'report_2fa_account').format(account=file_name)}\n")
                            f.write(f"   {t(user_id, 'report_2fa_details').format(info=masked_info)}\n")
                            f.write(f"   {t(user_id, 'report_2fa_process_time').format(time=datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S CST'))}\n\n")
                        
                        # 如果是失败列表，添加解决方案
                        if status == "失败":
                            f.write("\n" + "=" * 50 + "\n")
                            f.write(t(user_id, 'report_2fa_failure_analysis') + "\n")
                            f.write("-" * 50 + "\n\n")
                            f.write(f"1. {t(user_id, 'report_2fa_reason_unauthorized')}\n")
                            f.write(f"   - {t(user_id, 'report_2fa_reason_unauthorized_desc1')}\n")
                            f.write(f"   - {t(user_id, 'report_2fa_reason_unauthorized_desc2')}\n\n")
                            f.write(f"2. {t(user_id, 'report_2fa_reason_wrong_password')}\n")
                            f.write(f"   - {t(user_id, 'report_2fa_reason_wrong_password_desc1')}\n")
                            f.write(f"   - {t(user_id, 'report_2fa_reason_wrong_password_desc2')}\n\n")
                            f.write(f"3. {t(user_id, 'report_2fa_reason_network')}\n")
                            f.write(f"   - {t(user_id, 'report_2fa_reason_network_desc1')}\n")
                            f.write(f"   - {t(user_id, 'report_2fa_reason_network_desc2')}\n\n")
                
                logger.info(f"✅ TXT报告创建成功: {txt_filename}")
                print(f"✅ 创建TXT报告: {txt_filename}")
                
                result_files.append((zip_path, txt_path, status, len(items)))
                
            except Exception as e:
                logger.error(f"❌ 创建{status}结果文件失败: {e}")
                print(f"❌ 创建{status}结果文件失败: {e}")
                import traceback
                traceback.print_exc()
            finally:
                # 清理临时目录
                if os.path.exists(status_temp_dir):
                    shutil.rmtree(status_temp_dir, ignore_errors=True)
                    logger.info(f"临时目录已清理: {status_temp_dir}")
        
        logger.info(f"结果文件创建完成: 共 {len(result_files)} 组文件")
        return result_files
    
    def cleanup_expired_tasks(self, timeout_seconds: int = 300):
        """
        清理过期的待处理任务（默认5分钟超时）
        
        Args:
            timeout_seconds: 超时时间（秒）
        """
        current_time = time.time()
        expired_users = []
        
        for user_id, task_info in self.pending_2fa_tasks.items():
            task_start_time = task_info.get('start_time', 0)
            if current_time - task_start_time > timeout_seconds:
                expired_users.append(user_id)
        
        # 清理过期任务
        for user_id in expired_users:
            task_info = self.pending_2fa_tasks[user_id]
            
            # 清理临时文件
            extract_dir = task_info.get('extract_dir')
            temp_zip = task_info.get('temp_zip')
            
            if extract_dir and os.path.exists(extract_dir):
                try:
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    print(f"🗑️ 清理过期任务的解压目录: {extract_dir}")
                except:
                    pass
            
            if temp_zip and os.path.exists(temp_zip):
                try:
                    shutil.rmtree(os.path.dirname(temp_zip), ignore_errors=True)
                    print(f"🗑️ 清理过期任务的临时文件: {temp_zip}")
                except:
                    pass
            
            # 删除任务信息
            del self.pending_2fa_tasks[user_id]
            print(f"⏰ 清理过期任务: user_id={user_id}")

