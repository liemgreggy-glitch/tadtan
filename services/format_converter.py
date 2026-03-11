"""
services.format_converter - Session/TData format conversion service
"""
import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.constants import BEIJING_TZ

logger = logging.getLogger(__name__)

try:
    from telethon import TelegramClient
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False

try:
    from opentele.api import API, UseCurrentSession
    from opentele.td import TDesktop
    from opentele.tl import TelegramClient as OpenTeleClient
    OPENTELE_AVAILABLE = True
except ImportError:
    OPENTELE_AVAILABLE = False
    API = None
    UseCurrentSession = None
    TDesktop = None
    OpenTeleClient = None

from core.config import Config
from services.forget_2fa_manager import Forget2FAManager
try:
    from i18n import get_text as t
except ImportError:
    def t(user_id, key, **kwargs): return key

config = Config()


class FormatConverter:
    """Tdata与Session格式互转器"""
    
    def __init__(self, db):
        self.db = db
    
    def generate_failure_files(self, tdata_path: str, tdata_name: str, error_message: str):
        """
        生成失败转换的session和JSON文件
        用于所有转换失败的情况
        """
        # 使用config中定义的sessions目录
        sessions_dir = config.SESSIONS_DIR
        os.makedirs(sessions_dir, exist_ok=True)
        
        phone = tdata_name
        
        # 生成失败的session文件
        failed_session_path = os.path.join(sessions_dir, f"{phone}.session")
        self.create_failed_session_file(failed_session_path, error_message)
        
        # 生成失败的JSON文件
        failed_json_data = self.generate_failed_json(phone, phone, error_message, tdata_name)
        failed_json_path = os.path.join(sessions_dir, f"{phone}.json")
        with open(failed_json_path, 'w', encoding='utf-8') as f:
            json.dump(failed_json_data, f, ensure_ascii=False, indent=2)
        
        print(f"❌ 转换失败，已生成失败标记文件: {tdata_name}")
        print(f"   📄 Session文件: sessions/{phone}.session")
        print(f"   📄 JSON文件: sessions/{phone}.json")
    
    def create_empty_session_file(self, session_path: str):
        """
        创建空的session文件占位符
        用于当临时session文件不存在时
        """
        try:
            # 创建一个空的SQLite数据库文件作为session文件
            # Telethon session文件是SQLite数据库格式
            import sqlite3
            conn = sqlite3.connect(session_path)
            cursor = conn.cursor()
            # 创建基本的Telethon session表结构
            cursor.execute('''
                CREATE TABLE sessions (
                    dc_id INTEGER PRIMARY KEY,
                    server_address TEXT,
                    port INTEGER,
                    auth_key BLOB
                )
            ''')
            cursor.execute('''
                CREATE TABLE entities (
                    id INTEGER PRIMARY KEY,
                    hash INTEGER NOT NULL,
                    username TEXT,
                    phone INTEGER,
                    name TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE sent_files (
                    md5_digest BLOB,
                    file_size INTEGER,
                    type INTEGER,
                    id INTEGER,
                    hash INTEGER,
                    PRIMARY KEY(md5_digest, file_size, type)
                )
            ''')
            cursor.execute('''
                CREATE TABLE update_state (
                    id INTEGER PRIMARY KEY,
                    pts INTEGER,
                    qts INTEGER,
                    date INTEGER,
                    seq INTEGER
                )
            ''')
            cursor.execute('''
                CREATE TABLE version (
                    version INTEGER PRIMARY KEY
                )
            ''')
            cursor.execute('INSERT INTO version VALUES (6)')
            conn.commit()
            conn.close()
            print(f"📄 创建空session文件: {os.path.basename(session_path)}")
        except Exception as e:
            print(f"⚠️ 创建空session文件失败: {e}")
    
    def create_failed_session_file(self, session_path: str, error_message: str):
        """
        创建失败标记的session文件
        用于转换失败的情况
        """
        self.create_empty_session_file(session_path)
        # 在同目录创建一个标记文件说明这是失败的session
        error_marker = session_path + ".error"
        try:
            with open(error_marker, 'w', encoding='utf-8') as f:
                f.write(f"转换失败: {error_message}\n")
                f.write(f"时间: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S CST')}\n")
        except:
            pass
    
    def generate_failed_json(self, phone: str, session_name: str, error_message: str, tdata_name: str) -> dict:
        """
        生成包含错误信息的JSON文件
        用于转换失败的情况
        """
        current_time = datetime.now(BEIJING_TZ)
        
        json_data = {
            "app_id": 2040,
            "app_hash": "b18441a1ff607e10a989891a5462e627",
            "sdk": "Windows 11",
            "device": "Desktop",
            "app_version": "6.1.4 x64",
            "lang_pack": "en",
            "system_lang_pack": "en-US",
            "twoFA": "",
            "role": None,
            "id": 0,
            "phone": phone,
            "username": None,
            "date_of_birth": None,
            "date_of_birth_integrity": None,
            "is_premium": False,
            "premium_expiry": None,
            "first_name": "",
            "last_name": None,
            "has_profile_pic": False,
            "spamblock": "unknown",
            "spamblock_end_date": None,
            "session_file": session_name,
            "stats_spam_count": 0,
            "stats_invites_count": 0,
            "last_connect_date": current_time.strftime('%Y-%m-%dT%H:%M:%S+0000'),
            "session_created_date": current_time.strftime('%Y-%m-%dT%H:%M:%S+0000'),
            "app_config_hash": None,
            "extra_params": "",
            "device_model": "Desktop",
            "user_id": 0,
            "ipv6": False,
            "register_time": None,
            "sex": None,
            "last_check_time": int(current_time.timestamp()),
            "device_token": "",
            "tz_offset": 0,
            "perf_cat": 2,
            "avatar": "img/default.png",
            "proxy": None,
            "block": False,
            "package_id": "",
            "installer": "",
            "email": "",
            "email_id": "",
            "secret": "",
            "category": "",
            "scam": False,
            "is_blocked": False,
            "voip_token": "",
            "last_reg_time": -62135596800,
            "has_password": False,
            "block_since_time": 0,
            "block_until_time": 0,
            "conversion_time": current_time.strftime('%Y-%m-%d %H:%M:%S'),
            "conversion_source": "TData",
            "conversion_status": "failed",
            "error_message": error_message,
            "original_tdata_name": tdata_name
        }
        
        return json_data
    
    async def generate_session_json(self, me, phone: str, session_name: str, output_dir: str) -> dict:
        """
        生成完整的Session JSON数据
        基于提供的JSON模板格式
        """
        current_time = datetime.now(BEIJING_TZ)
        
        # 从用户对象提取信息
        user_id = me.id if hasattr(me, 'id') else 0
        first_name = me.first_name if hasattr(me, 'first_name') and me.first_name else ""
        last_name = me.last_name if hasattr(me, 'last_name') and me.last_name else None
        username = me.username if hasattr(me, 'username') and me.username else None
        is_premium = me.premium if hasattr(me, 'premium') else False
        has_profile_pic = hasattr(me, 'photo') and me.photo is not None
        
        # 生成JSON数据(基于提供的模板)
        json_data = {
            "app_id": 2040,
            "app_hash": "b18441a1ff607e10a989891a5462e627",
            "sdk": "Windows 11",
            "device": "Desktop",
            "app_version": "6.1.4 x64",
            "lang_pack": "en",
            "system_lang_pack": "en-US",
            "twoFA": "",
            "role": None,
            "id": user_id,
            "phone": phone,
            "username": username,
            "date_of_birth": None,
            "date_of_birth_integrity": None,
            "is_premium": is_premium,
            "premium_expiry": None,
            "first_name": first_name,
            "last_name": last_name,
            "has_profile_pic": has_profile_pic,
            "spamblock": "free",
            "spamblock_end_date": None,
            "session_file": session_name,
            "stats_spam_count": 0,
            "stats_invites_count": 0,
            "last_connect_date": current_time.strftime('%Y-%m-%dT%H:%M:%S+0000'),
            "session_created_date": current_time.strftime('%Y-%m-%dT%H:%M:%S+0000'),
            "app_config_hash": None,
            "extra_params": "",
            "device_model": "Desktop",
            "user_id": user_id,
            "ipv6": False,
            "register_time": None,
            "sex": None,
            "last_check_time": int(current_time.timestamp()),
            "device_token": "",
            "tz_offset": 0,
            "perf_cat": 2,
            "avatar": "img/default.png",
            "proxy": None,
            "block": False,
            "package_id": "",
            "installer": "",
            "email": "",
            "email_id": "",
            "secret": "",
            "category": "",
            "scam": False,
            "is_blocked": False,
            "voip_token": "",
            "last_reg_time": -62135596800,
            "has_password": False,
            "block_since_time": 0,
            "block_until_time": 0,
            "conversion_time": current_time.strftime('%Y-%m-%d %H:%M:%S'),
            "conversion_source": "TData"
        }
        
        return json_data
    
    async def convert_tdata_to_session(self, tdata_path: str, tdata_name: str, api_id: int, api_hash: str) -> Tuple[str, str, str]:
        """
        将Tdata转换为Session
        返回: (状态, 信息, 账号名)
        """
        client = None
        session_file = None
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                if not OPENTELE_AVAILABLE:
                    error_msg = "opentele库未安装"
                    self.generate_failure_files(tdata_path, tdata_name, error_msg)
                    return "转换错误", error_msg, tdata_name
                
                print(f"🔄 尝试转换 {tdata_name} (尝试 {attempt + 1}/{max_retries})")
                
                # 加载TData
                tdesk = TDesktop(tdata_path)
                
                # 检查是否已授权
                if not tdesk.isLoaded():
                    print(f"❌ TData加载失败: {tdata_name}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    error_msg = "TData未授权或无效"
                    self.generate_failure_files(tdata_path, tdata_name, error_msg)
                    return "转换错误", error_msg, tdata_name
                
                # 生成唯一的session名称以避免冲突
                # 临时session文件保存在sessions/temp目录
                unique_session_name = f"{tdata_name}_{int(time.time()*1000)}"
                temp_session_path = os.path.join(config.SESSIONS_BAK_DIR, unique_session_name)
                session_file = f"{unique_session_name}.session"
                
                # 确保sessions/temp目录存在
                os.makedirs(config.SESSIONS_BAK_DIR, exist_ok=True)
                
                # 转换为Telethon Session (带超时)
                try:
                    client = await asyncio.wait_for(
                        tdesk.ToTelethon(
                            session=temp_session_path,
                            flag=UseCurrentSession,
                            api=API.TelegramDesktop
                        ),
                        timeout=30.0
                    )
                except asyncio.TimeoutError:
                    print(f"⏱️ TData转换超时: {tdata_name}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    error_msg = "TData转换超时"
                    self.generate_failure_files(tdata_path, tdata_name, error_msg)
                    return "转换错误", error_msg, tdata_name
                
                # 连接并获取账号信息 (带超时)
                try:
                    await asyncio.wait_for(client.connect(), timeout=15.0)
                except asyncio.TimeoutError:
                    print(f"⏱️ 连接超时: {tdata_name}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    error_msg = "连接超时"
                    self.generate_failure_files(tdata_path, tdata_name, error_msg)
                    return "转换错误", error_msg, tdata_name
                
                if not await client.is_user_authorized():
                    print(f"❌ 账号未授权: {tdata_name}")
                    error_msg = "<<ERROR:error_unauthorized>>"
                    self.generate_failure_files(tdata_path, tdata_name, error_msg)
                    return "转换错误", error_msg, tdata_name
                
                # 获取完整用户信息
                me = await client.get_me()
                phone = me.phone if me.phone else "未知"
                username = me.username if me.username else "<<NO_USERNAME>>"
                
                # 重命名session文件为手机号
                final_session_name = phone if phone != "未知" else tdata_name
                final_session_file = f"{final_session_name}.session"
                
                # 确保连接关闭
                await client.disconnect()
                
                # 使用config中定义的sessions目录
                sessions_dir = config.SESSIONS_DIR
                os.makedirs(sessions_dir, exist_ok=True)
                
                # 重命名session文件
                # ToTelethon creates session file at the path specified (temp_session_path)
                # 临时session文件保存在sessions_bak目录
                temp_session_path = os.path.join(config.SESSIONS_BAK_DIR, session_file)
                final_session_path = os.path.join(sessions_dir, final_session_file)
                
                # 确保session文件总是被创建
                session_created = False
                if os.path.exists(temp_session_path):
                    # 如果目标文件已存在，先删除
                    if os.path.exists(final_session_path):
                        os.remove(final_session_path)
                    os.rename(temp_session_path, final_session_path)
                    session_created = True
                    
                    # 同时处理journal文件
                    temp_journal = temp_session_path + "-journal"
                    final_journal = final_session_path + "-journal"
                    if os.path.exists(temp_journal):
                        if os.path.exists(final_journal):
                            os.remove(final_journal)
                        os.rename(temp_journal, final_journal)
                else:
                    # 如果临时session文件不存在，创建一个空的session文件
                    print(f"⚠️ 临时session文件不存在，创建空session文件")
                    self.create_empty_session_file(final_session_path)
                    session_created = True
                
                # 生成完整的JSON文件
                json_data = await self.generate_session_json(me, phone, final_session_name, sessions_dir)
                json_path = os.path.join(sessions_dir, f"{final_session_name}.json")
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(json_data, f, ensure_ascii=False, indent=2)
                
                print(f"✅ 转换成功: {tdata_name} -> {phone}")
                print(f"   📄 Session文件: sessions/{final_session_file}")
                print(f"   📄 JSON文件: sessions/{final_session_name}.json")
                return "转换成功", f"手机号: {phone} | 用户名: @{username}", tdata_name
                
            except Exception as e:
                error_msg = str(e)
                print(f"❌ 转换错误 {tdata_name}: {error_msg}")
                
                # 清理临时文件（sessions_bak目录）
                if session_file:
                    try:
                        temp_session_path = os.path.join(config.SESSIONS_BAK_DIR, session_file)
                        if os.path.exists(temp_session_path):
                            os.remove(temp_session_path)
                        temp_journal = temp_session_path + "-journal"
                        if os.path.exists(temp_journal):
                            os.remove(temp_journal)
                    except:
                        pass
                
                if attempt < max_retries - 1:
                    print(f"🔄 等待 {retry_delay} 秒后重试...")
                    await asyncio.sleep(retry_delay)
                    continue
                
                # 最后一次尝试失败，生成失败标记的文件
                # 确定错误类型和错误消息
                if "database is locked" in error_msg.lower():
                    final_error_msg = "<<ERROR:error_file_locked>>"
                elif "auth key" in error_msg.lower() or "authorization" in error_msg.lower():
                    final_error_msg = "<<ERROR:error_auth_key_invalid>>"
                elif "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                    final_error_msg = "<<ERROR:error_connection_timeout>>"
                elif "network" in error_msg.lower() or "connection" in error_msg.lower():
                    final_error_msg = "<<ERROR:error_network_failed>>"
                else:
                    final_error_msg = f"转换失败: {error_msg[:50]}"
                
                self.generate_failure_files(tdata_path, tdata_name, final_error_msg)
                return "转换错误", final_error_msg, tdata_name
            finally:
                # 确保客户端连接关闭
                if client:
                    try:
                        await client.disconnect()
                    except:
                        pass
        
        # 如果到达这里说明所有重试都失败了
        error_msg = "多次重试后失败"
        self.generate_failure_files(tdata_path, tdata_name, error_msg)
        return "转换错误", error_msg, tdata_name
    
    async def convert_session_to_tdata(self, session_path: str, session_name: str, api_id: int, api_hash: str) -> Tuple[str, str, str]:
        """
        将Session转换为Tdata
        返回: (状态, 信息, 账号名)
        """
        try:
            if not OPENTELE_AVAILABLE:
                return "转换错误", "opentele库未安装", session_name
            
            # 创建Telethon客户端
            client = OpenTeleClient(session_path, api_id, api_hash)
            
            # 连接
            await client.connect()
            
            if not await client.is_user_authorized():
                await client.disconnect()
                return "转换错误", "Session未授权", session_name
            
            # 获取账号信息
            me = await client.get_me()
            phone = me.phone if me.phone else "未知"
            username = me.username if me.username else "<<NO_USERNAME>>"
            
            # 转换为TData
            tdesk = await client.ToTDesktop(flag=UseCurrentSession)
            
            # 使用config中定义的sessions目录
            sessions_dir = config.SESSIONS_DIR
            os.makedirs(sessions_dir, exist_ok=True)
            
            # 保存TData - 修改为: sessions/手机号/tdata/ 结构
            phone_dir = os.path.join(sessions_dir, phone)
            tdata_dir = os.path.join(phone_dir, "tdata")
            
            # 确保目录存在
            os.makedirs(phone_dir, exist_ok=True)
            
            tdesk.SaveTData(tdata_dir)
            
            await client.disconnect()
            
            return "转换成功", f"手机号: {phone} | 用户名: @{username}", session_name
            
        except Exception as e:
            error_msg = str(e)
            if "database is locked" in error_msg.lower():
                return "转换错误", "<<ERROR:error_session_locked>>", session_name
            elif "auth key" in error_msg.lower():
                return "转换错误", "<<ERROR:error_auth_key_invalid>>", session_name
            else:
                return "转换错误", f"<<ERROR:error_conversion_failed>>: {error_msg[:50]}", session_name
    
    async def batch_convert_with_progress(self, files: List[Tuple[str, str]], conversion_type: str, 
                                         api_id: int, api_hash: str, update_callback) -> Dict[str, List[Tuple[str, str, str]]]:
        """
        批量转换并提供实时进度更新
        conversion_type: "tdata_to_session" 或 "session_to_tdata"
        """
        results = {
            "转换成功": [],
            "转换错误": []
        }
        
        total = len(files)
        processed = 0
        start_time = time.time()
        last_update_time = 0
        
        # 线程安全的锁
        lock = asyncio.Lock()
        
        async def process_single_file(file_path, file_name):
            nonlocal processed, last_update_time
            
            # 为每个转换设置超时
            conversion_timeout = 60.0  # 每个文件最多60秒
            
            try:
                if conversion_type == "tdata_to_session":
                    status, info, name = await asyncio.wait_for(
                        self.convert_tdata_to_session(file_path, file_name, api_id, api_hash),
                        timeout=conversion_timeout
                    )
                else:  # session_to_tdata
                    status, info, name = await asyncio.wait_for(
                        self.convert_session_to_tdata(file_path, file_name, api_id, api_hash),
                        timeout=conversion_timeout
                    )
                
                async with lock:
                    results[status].append((file_path, file_name, info))
                    processed += 1
                    
                    print(f"✅ 转换完成 {processed}/{total}: {file_name} -> {status} | {info}")
                    
                    # 控制更新频率
                    current_time = time.time()
                    if current_time - last_update_time >= 2 or processed % 5 == 0 or processed == total:
                        elapsed = current_time - start_time
                        speed = processed / elapsed if elapsed > 0 else 0
                        
                        try:
                            await update_callback(processed, total, results, speed, elapsed)
                            last_update_time = current_time
                        except Exception as e:
                            print(f"⚠️ 更新回调失败: {e}")
                        
            except asyncio.TimeoutError:
                print(f"⏱️ 转换超时 {file_name}")
                async with lock:
                    results["转换错误"].append((file_path, file_name, "转换超时(60秒)"))
                    processed += 1
            except Exception as e:
                print(f"❌ 处理失败 {file_name}: {e}")
                async with lock:
                    results["转换错误"].append((file_path, file_name, f"异常: {str(e)[:50]}"))
                    processed += 1
        
        # 增加并发数以加快转换速度，从10提升到20
        batch_size = 20
        print(f"🚀 开始批量转换，并发数: {batch_size}")
        
        for i in range(0, len(files), batch_size):
            batch = files[i:i + batch_size]
            tasks = [process_single_file(file_path, file_name) for file_path, file_name in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
        
        return results
    
    def create_conversion_result_zips(self, results: Dict[str, List[Tuple[str, str, str]]], 
                                     task_id: str, conversion_type: str, user_id: int) -> List[Tuple[str, str, int]]:
        """创建转换结果ZIP文件（修正版）"""
        result_files = []
        
        # 根据转换类型确定文件名前缀 - 使用翻译
        if conversion_type == "tdata_to_session":
            # success_prefix = "tdata转换session 成功"
            # failure_prefix = "tdata转换session 失败"
            success_zip_key = 'zip_tdata_to_session_success'
            report_success_key = 'report_filename_success'  # Will need to be tdata->session specific
        else:  # session_to_tdata
            # success_prefix = "session转换tdata 成功"
            # failure_prefix = "session转换tdata 失败"
            success_zip_key = 'zip_session_to_tdata_success'
            report_success_key = 'report_filename_success'
        
        failure_zip_key = 'zip_conversion_failed'
        report_failed_key = 'report_filename_failed'
        
        for status, files in results.items():
            if not files:
                continue
            
            # 优化路径长度：使用更短的时间戳和简化的目录结构
            timestamp_short = str(int(time.time()))[-6:]  # 只取后6位
            status_temp_dir = os.path.join(config.RESULTS_DIR, f"conv_{timestamp_short}_{status}")
            os.makedirs(status_temp_dir, exist_ok=True)
            
            try:
                for file_path, file_name, info in files:
                    if status == "转换成功":
                        if conversion_type == "tdata_to_session":
                            # Tdata转Session: 复制生成的session文件和JSON文件
                            sessions_dir = config.SESSIONS_DIR
                            
                            # 从info中提取手机号
                            phone = "未知"
                            if "手机号:" in info:
                                phone_part = info.split("手机号:")[1].split("|")[0].strip()
                                phone = phone_part if phone_part else "未知"
                            
                            # Session文件应该保存在sessions目录下
                            session_file = f"{phone}.session"
                            session_path = os.path.join(sessions_dir, session_file)
                            
                            if os.path.exists(session_path):
                                dest_path = os.path.join(status_temp_dir, session_file)
                                shutil.copy2(session_path, dest_path)
                                print(f"📄 复制Session文件: {session_file}")
                            
                            # 复制对应的JSON文件（如果存在）
                            json_file = f"{phone}.json"
                            json_path = os.path.join(sessions_dir, json_file)
                            
                            if os.path.exists(json_path):
                                json_dest = os.path.join(status_temp_dir, json_file)
                                shutil.copy2(json_path, json_dest)
                                print(f"📄 复制JSON文件: {json_file}")
                            else:
                                print(f"ℹ️ 无JSON文件: {phone}.session (纯Session文件)")
                        
                    
                        else:  # session_to_tdata - 修复路径问题
                            # 转换后的文件实际保存在sessions目录下，不是source_dir
                            sessions_dir = config.SESSIONS_DIR
                            
                            # 从info中提取手机号
                            phone = "未知"
                            if "手机号:" in info:
                                phone_part = info.split("手机号:")[1].split("|")[0].strip()
                                phone = phone_part if phone_part else "未知"
                            
                            # 正确的路径：sessions/手机号/
                            phone_dir = os.path.join(sessions_dir, phone)
                            
                            if os.path.exists(phone_dir):
                                # 复制整个手机号目录结构
                                phone_dest = os.path.join(status_temp_dir, phone)
                                shutil.copytree(phone_dir, phone_dest)
                                print(f"📂 复制号码目录: {phone}/tdata/")
                                
                                # 将原始session和json文件复制到手机号目录下（与tdata同级）
                                if os.path.exists(file_path):
                                    session_dest = os.path.join(phone_dest, os.path.basename(file_path))
                                    shutil.copy2(file_path, session_dest)
                                    print(f"📄 复制原始Session: {os.path.basename(file_path)}")
                                
                                # 复制对应的json文件（如果存在）
                                json_name = file_name.replace('.session', '.json')
                                original_json = os.path.join(os.path.dirname(file_path), json_name)
                                if os.path.exists(original_json):
                                    json_dest = os.path.join(phone_dest, json_name)
                                    shutil.copy2(original_json, json_dest)
                                    print(f"📄 复制原始JSON: {json_name}")
                                else:
                                    print(f"ℹ️ 无JSON文件: {file_name} (纯Session文件)")
                            else:
                                print(f"⚠️ 找不到转换后的目录: {phone_dir}")
                    
                    else:  # 转换错误 - 打包失败的文件
                        if conversion_type == "tdata_to_session":
                            if os.path.isdir(file_path):
                                # 检查是否是 tdata 目录，如果是，复制父目录以保留 phone/tdata/D877... 结构
                                if os.path.basename(file_path).lower() == 'tdata':
                                    # file_path 是 tdata 目录，复制其父目录（手机号目录）
                                    phone_dir = os.path.dirname(file_path)
                                    phone_folder_name = os.path.basename(phone_dir)
                                    dest_path = os.path.join(status_temp_dir, phone_folder_name)
                                    shutil.copytree(phone_dir, dest_path)
                                    print(f"📂 复制失败的TData（保留结构）: {phone_folder_name}/tdata/")
                                else:
                                    # 如果不是标准 tdata 结构，按原样复制
                                    dest_path = os.path.join(status_temp_dir, file_name)
                                    shutil.copytree(file_path, dest_path)
                                    print(f"📂 复制失败的TData: {file_name}")
                        else:
                            if os.path.exists(file_path):
                                dest_path = os.path.join(status_temp_dir, file_name)
                                shutil.copy2(file_path, dest_path)
                                print(f"📄 复制失败的Session: {file_name}")
                                
                                # 复制对应的json文件（如果存在）
                                json_name = file_name.replace('.session', '.json')
                                json_path = os.path.join(os.path.dirname(file_path), json_name)
                                if os.path.exists(json_path):
                                    json_dest = os.path.join(status_temp_dir, json_name)
                                    shutil.copy2(json_path, json_dest)
                                    print(f"📄 复制失败的JSON: {json_name}")
                                else:
                                    print(f"ℹ️ 无JSON文件: {file_name} (纯Session文件)")
                        
                        # 创建详细的失败原因说明
                        error_file = os.path.join(status_temp_dir, f"{file_name}_错误原因.txt")
                        with open(error_file, 'w', encoding='utf-8') as f:
                            # 隐藏代理详细信息，保护用户隐私
                            masked_info = Forget2FAManager.mask_proxy_in_string(info)
                            f.write(f"文件: {file_name}\n")
                            f.write(f"转换类型: {conversion_type}\n")
                            f.write(f"失败原因: {masked_info}\n")
                            f.write(f"失败时间: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S CST')}\n")
                            f.write(f"\n建议:\n")
                            if "授权" in info:
                                f.write("- 检查账号是否已登录\n")
                                f.write("- 验证TData文件是否有效\n")
                            elif "超时" in info:
                                f.write("- 检查网络连接\n")
                                f.write("- 尝试使用代理\n")
                            elif "占用" in info:
                                f.write("- 关闭其他使用该文件的程序\n")
                                f.write("- 重启后重试\n")
                
                # 创建 ZIP 文件 - 使用翻译的文件名
                if status == "转换成功":
                    zip_filename = t(user_id, success_zip_key).format(count=len(files)) + ".zip"
                else:
                    zip_filename = t(user_id, failure_zip_key).format(count=len(files)) + ".zip"
                
                zip_path = os.path.join(config.RESULTS_DIR, zip_filename)
                
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files_in_dir in os.walk(status_temp_dir):
                        for file in files_in_dir:
                            file_path_full = os.path.join(root, file)
                            arcname = os.path.relpath(file_path_full, status_temp_dir)
                            zipf.write(file_path_full, arcname)
                
                print(f"✅ 创建ZIP文件: {zip_filename}")
                
                # 创建 TXT 报告 - 使用翻译的文件名和内容
                if status == "转换成功":
                    txt_filename = t(user_id, report_success_key)
                else:
                    txt_filename = t(user_id, report_failed_key)
                txt_path = os.path.join(config.RESULTS_DIR, txt_filename)
                
                # 确定转换类型的显示文本
                if conversion_type == "tdata_to_session":
                    conversion_type_display = "Tdata → Session"
                else:
                    conversion_type_display = "Session → Tdata"
                
                with open(txt_path, 'w', encoding='utf-8') as f:
                    # 报告标题
                    if status == "转换成功":
                        f.write(f"{t(user_id, 'report_title_success')}\n")
                    else:
                        f.write(f"{t(user_id, 'report_title_failed')}\n")
                    f.write("=" * 50 + "\n\n")
                    f.write(f"{t(user_id, 'report_generated_time').format(time=datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S CST'))}\n")
                    f.write(f"{t(user_id, 'report_conversion_type').format(type=conversion_type_display)}\n")
                    f.write(f"{t(user_id, 'report_total_count').format(count=len(files))}\n\n")
                    
                    f.write(f"{t(user_id, 'report_detail_list')}\n")
                    f.write("-" * 50 + "\n\n")
                    
                    for idx, (file_path, file_name, info) in enumerate(files, 1):
                        # 隐藏代理详细信息，保护用户隐私
                        masked_info = Forget2FAManager.mask_proxy_in_string(info)
                        
                        # 解析info中的手机号和用户名
                        phone = "unknown"
                        username = t(user_id, 'report_no_username')
                        if "手机号:" in masked_info:
                            phone_part = masked_info.split("手机号:")[1].split("|")[0].strip()
                            phone = phone_part if phone_part else "unknown"
                        if "用户名:" in masked_info:
                            username_part = masked_info.split("用户名:")[1].strip()
                            # Replace the special placeholder with translated text
                            if "<<NO_USERNAME>>" in username_part:
                                username = t(user_id, 'report_no_username')
                            else:
                                username = username_part if username_part else t(user_id, 'report_no_username')
                        
                        f.write(f"{idx}. {t(user_id, 'report_file').format(filename=file_name)}\n")
                        if status == "转换成功":
                            f.write(f"   {t(user_id, 'report_info').format(phone=phone, username=username)}\n")
                        else:
                            # Translate error messages with special markers
                            translated_error = masked_info
                            if "<<ERROR:" in masked_info:
                                # Extract error key
                                import re
                                error_match = re.search(r'<<ERROR:(\w+)>>', masked_info)
                                if error_match:
                                    error_key = error_match.group(1)
                                    error_text = t(user_id, error_key)
                                    # Replace the marker with translated text
                                    translated_error = re.sub(r'<<ERROR:\w+>>', error_text, masked_info)
                            f.write(f"   {t(user_id, 'report_error').format(error=translated_error)}\n")
                        f.write(f"   {t(user_id, 'report_time').format(time=datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S CST'))}\n\n")
                
                print(f"✅ 创建TXT报告: {txt_filename}")
                
                # ⚠️ 关键修复：返回 4 个值而不是 3 个
                result_files.append((zip_path, txt_path, status, len(files)))
                
            except Exception as e:
                print(f"❌ 创建{status}结果文件失败: {e}")
                import traceback
                traceback.print_exc()
            finally:
                if os.path.exists(status_temp_dir):
                    shutil.rmtree(status_temp_dir, ignore_errors=True)
        
        return result_files
