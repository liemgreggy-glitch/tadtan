"""
services.spambot_checker - SpamBot status checking service
"""
import asyncio
import logging
import os
import time
import traceback
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.constants import (
    TDATA_PIPELINE_CONVERT_CONCURRENT,
    TDATA_PIPELINE_CHECK_CONCURRENT,
    TDATA_PIPELINE_CONVERT_TIMEOUT,
    PROGRESS_UPDATE_INTERVAL,
    PROGRESS_UPDATE_MIN_PERCENT,
    PROGRESS_UPDATE_MIN_PERCENT_LARGE,
    PROGRESS_LARGE_BATCH_THRESHOLD,
    BEIJING_TZ,
)

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

from core.config import Config
from managers.proxy_manager import ProxyManager
from models.dataclasses import ProxyUsageRecord

config = Config()


class SpamBotChecker:
    """SpamBot检测器（优化版）"""
    
    def __init__(self, proxy_manager: ProxyManager):
        # 根据快速模式调整并发数，提升到25
        concurrent_limit = config.PROXY_CHECK_CONCURRENT if config.PROXY_FAST_MODE else config.MAX_CONCURRENT_CHECKS
        # 至少使用25个并发
        concurrent_limit = max(concurrent_limit, 25)
        self.semaphore = asyncio.Semaphore(concurrent_limit)
        self.proxy_manager = proxy_manager
        
        # 优化超时设置
        self.fast_timeout = config.PROXY_CHECK_TIMEOUT if config.PROXY_FAST_MODE else config.CHECK_TIMEOUT
        self.connection_timeout = 6  # 连接超时6秒
        self.spambot_timeout = 3     # SpamBot超时3秒
        self.fast_wait = 0.1         # SpamBot等待0.1秒
        
        # 代理使用记录跟踪（使用deque限制大小）
        self.proxy_usage_records: deque = deque(maxlen=config.PROXY_USAGE_LOG_LIMIT)
        
        print(f"⚡ SpamBot检测器初始化: 并发={concurrent_limit}, 快速模式={'开启' if config.PROXY_FAST_MODE else '关闭'}")
        
        # 增强版状态模式 - 支持多语言和更精确的分类
        self.status_patterns = {
            # 地理限制提示 - 判定为无限制（优先级最高）
            # "some phone numbers may trigger a harsh response" 是地理限制，不是双向限制
            "地理限制": [
                "some phone numbers may trigger a harsh response",
                "phone numbers may trigger",
            ],
            "无限制": [
                "good news, no limits are currently applied",
                "you're free as a bird",
                "no limits",
                "free as a bird",
                "no restrictions",
                # 新增英文关键词
                "all good",
                "account is free",
                "working fine",
                "not limited",
                # 中文关键词
                "正常",
                "没有限制",
                "一切正常",
                "无限制"
            ],
            "临时限制": [
                # 临时限制的关键指标（优先级最高）
                "account is now limited until",
                "limited until",
                "account is limited until",
                "moderators have confirmed the report",
                "users found your messages annoying",
                "will be automatically released",
                "limitations will last longer next time",
                "while the account is limited",
                # 新增临时限制关键词
                "temporarily limited",
                "temporarily restricted",
                "temporary ban",
                # 中文关键词
                "暂时限制",
                "临时限制",
                "暂时受限"
            ],
            "垃圾邮件": [
                # 真正的限制 - "actions can trigger" 表示账号行为触发了限制
                "actions can trigger a harsh response from our anti-spam systems",
                "account was limited",
                "you will not be able to send messages",
                "limited by mistake",
                # 注意：移除了 "anti-spam systems" 因为地理限制也包含这个词
                # 注意：移除了 "spam" 因为太宽泛
                # 中文关键词
                "违规",
            ],
            "冻结": [
                # 永久限制的关键指标
                "permanently banned",
                "account has been frozen permanently",
                "permanently restricted",
                "account is permanently",
                "banned permanently",
                "permanent ban",
                # 原有的patterns
                "account was blocked for violations",
                "telegram terms of service",
                "blocked for violations",
                "terms of service",
                "violations of the telegram",
                "banned",
                "suspended",
                # 中文关键词
                "永久限制",
                "永久封禁",
                "永久受限"
            ],
            "等待验证": [
                "wait",
                "pending",
                "verification",
                # 中文关键词
                "等待",
                "审核中",
                "验证"
            ]
        }
        
        # 增强版重试配置
        self.max_retries = 3  # 最大重试次数
        self.retry_delay = 2  # 重试间隔（秒）
    
    def translate_to_english(self, text: str) -> str:
        """翻译到英文（支持俄文和中文）"""
        translations = {
            # 俄文翻译
            'ограничения': 'limitations',
            'заблокирован': 'blocked',
            'спам': 'spam',
            'нарушение': 'violation',
            'жалобы': 'complaints',
            'модераторы': 'moderators',
            'хорошие новости': 'good news',
            'нет ограничений': 'no limits',
            'свободны как птица': 'free as a bird',
            'временно ограничен': 'temporarily limited',
            'постоянно заблокирован': 'permanently banned',
            'ожидание': 'waiting',
            'проверка': 'verification',
            # 中文翻译
            '正常': 'all good',
            '没有限制': 'no limits',
            '一切正常': 'all good',
            '无限制': 'no restrictions',
            '暂时限制': 'temporarily limited',
            '临时限制': 'temporarily limited',
            '暂时受限': 'temporarily restricted',
            '永久限制': 'permanently restricted',
            '永久封禁': 'permanently banned',
            '永久受限': 'permanently restricted',
            '违规': 'violation',
            '受限': 'restricted',
            '限制': 'limited',
            '封禁': 'banned',
            '等待': 'wait',
            '审核中': 'pending',
            '验证': 'verification',
        }
        
        translated = text.lower()
        for src, en in translations.items():
            translated = translated.replace(src.lower(), en)
        
        return translated
    
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
                # 基础代理支持（仅限telethon内置）
                proxy_dict = (proxy_info['host'], proxy_info['port'])
            
            return proxy_dict
            
        except Exception as e:
            print(f"❌ 创建代理配置失败: {e}")
            return None
    
    async def check_account_status(self, session_path: str, account_name: str, db: 'Database') -> Tuple[str, str, str]:
        """增强版账号状态检查
        
        多重验证机制:
        1. 快速连接测试
        2. 账号登录状态检查 (is_user_authorized())
        3. 基本信息获取 (get_me())
        4. SpamBot检查
        """
        if not TELETHON_AVAILABLE:
            return "连接错误", "Telethon未安装", account_name
        
        async with self.semaphore:
            start_time = time.time()
            proxy_attempts = []  # Track all proxy attempts
            proxy_used = "local"
            
            try:
                # 1. 先进行快速连接测试
                can_connect = await self._quick_connection_test(session_path)
                if not can_connect:
                    return "连接错误", "无法连接到Telegram服务器（session文件无效或不存在）", account_name
                
                # 检查是否应使用代理
                proxy_enabled = db.get_proxy_enabled() if db else True
                use_proxy = config.USE_PROXY and proxy_enabled and self.proxy_manager.proxies
                
                # 确定重试次数：使用增强版重试配置
                max_proxy_attempts = self.max_retries if use_proxy else 0
                
                # 尝试不同的代理
                all_timeout = True  # 标记是否所有代理都是超时
                for proxy_attempt in range(max_proxy_attempts + 1):
                    proxy_info = None
                    
                    # 获取代理（如果启用）
                    if use_proxy and proxy_attempt < max_proxy_attempts:
                        proxy_info = self.proxy_manager.get_next_proxy()
                        if config.PROXY_DEBUG_VERBOSE and proxy_info:
                            # 服务器日志中也隐藏代理详细信息
                            print(f"[#{proxy_attempt + 1}] 使用代理 检测账号 {account_name}")
                    
                    # 尝试检测
                    result = await self._single_check_with_proxy(
                        session_path, account_name, db, proxy_info, proxy_attempt
                    )
                    
                    # 记录尝试结果
                    elapsed = time.time() - start_time
                    attempt_result = "success" if result[0] not in ["连接错误", "封禁"] else "failed"
                    
                    # 检查是否为超时错误
                    is_timeout = "timeout" in result[1].lower() or "超时" in result[1]
                    if not is_timeout and result[0] == "连接错误":
                        all_timeout = False  # 有非超时的连接错误
                    
                    if proxy_info:
                        # 内部记录使用隐藏的代理标识
                        proxy_str = "使用代理"
                        proxy_attempts.append({
                            'proxy': proxy_str,
                            'result': attempt_result,
                            'error': result[1] if attempt_result == "failed" else None,
                            'is_residential': proxy_info.get('is_residential', False)
                        })
                    
                    # 如果成功，记录并返回
                    if result[0] != "连接错误":
                        # 创建使用记录
                        usage_record = ProxyUsageRecord(
                            account_name=account_name,
                            proxy_attempted=proxy_str if proxy_info else None,
                            attempt_result=attempt_result,
                            fallback_used=False,
                            error=result[1] if attempt_result == "failed" else None,
                            is_residential=proxy_info.get('is_residential', False) if proxy_info else False,
                            elapsed=elapsed
                        )
                        self.proxy_usage_records.append(usage_record)
                        return result
                    
                    # 如果到达最后一次尝试
                    if proxy_attempt >= max_proxy_attempts:
                        # 创建使用记录
                        usage_record = ProxyUsageRecord(
                            account_name=account_name,
                            proxy_attempted=proxy_str if proxy_info else None,
                            attempt_result=attempt_result,
                            fallback_used=False,
                            error=result[1] if attempt_result == "failed" else None,
                            is_residential=proxy_info.get('is_residential', False) if proxy_info else False,
                            elapsed=elapsed
                        )
                        self.proxy_usage_records.append(usage_record)
                        break
                    
                    # 重试间隔延迟
                    if config.PROXY_DEBUG_VERBOSE:
                        print(f"连接失败 ({result[1][:50]}), 重试下一个代理...")
                    await asyncio.sleep(self.retry_delay)
                
                # 只有所有代理都超时时，才尝试本地连接
                if use_proxy and all_timeout:
                    if config.PROXY_DEBUG_VERBOSE:
                        print(f"所有代理均超时，回退到本地连接: {account_name}")
                    result = await self._single_check_with_proxy(session_path, account_name, db, None, max_proxy_attempts)
                    
                    # 记录本地回退
                    elapsed = time.time() - start_time
                    usage_record = ProxyUsageRecord(
                        account_name=account_name,
                        proxy_attempted=None,
                        attempt_result="success" if result[0] != "连接错误" else "failed",
                        fallback_used=True,
                        error=result[1] if result[0] == "连接错误" else None,
                        is_residential=False,
                        elapsed=elapsed
                    )
                    self.proxy_usage_records.append(usage_record)
                    
                    return result
                
                return "连接错误", f"检查失败 (重试{max_proxy_attempts}次): 多次尝试后仍然失败", account_name
                
            except Exception as e:
                return "连接错误", f"检查失败: {str(e)}", proxy_used
    
    async def _single_check_with_proxy(self, session_path: str, account_name: str, db: 'Database',
                                        proxy_info: Optional[Dict], attempt: int) -> Tuple[str, str, str]:
        """带代理重试的单账号检查（增强版）
        
        增强功能：
        - 最大重试次数（3次）
        - 超时处理
        - 代理失败时的回退机制
        - 重试间隔延迟
        - 精确的冻结账户检测
        """
        client = None
        connect_start = time.time()
        last_error = ""
        
        # 构建代理描述字符串 - 隐藏代理详细信息，保护用户隐私
        if proxy_info:
            proxy_type_display = "住宅代理" if proxy_info.get('is_residential', False) else "代理"
            proxy_used = f"使用{proxy_type_display}"
        else:
            proxy_used = "本地连接"
        
        try:
            # 快速预检测模式（仅首次尝试）
            if config.PROXY_FAST_MODE and attempt == 0:
                quick_result = await self._quick_connection_test(session_path)
                if not quick_result:
                    return "连接错误", "快速连接测试失败", account_name
            
            # 创建代理字典（如果提供了proxy_info）
            proxy_dict = None
            if proxy_info:
                proxy_dict = self.create_proxy_dict(proxy_info)
                if not proxy_dict:
                    return "连接错误", f"{proxy_used} | 代理配置错误", account_name
            
            # 根据代理类型调整超时时间
            if proxy_info and proxy_info.get('is_residential', False):
                client_timeout = config.RESIDENTIAL_PROXY_TIMEOUT
                connect_timeout = config.RESIDENTIAL_PROXY_TIMEOUT
            else:
                client_timeout = self.fast_timeout
                connect_timeout = self.connection_timeout if proxy_dict else 5
            
            # 创建客户端
            # Telethon expects session path without .session extension
            session_base = session_path.replace('.session', '') if session_path.endswith('.session') else session_path
            
            # 增强版控制台日志 - 问题3：显示设备和代理信息
            device_info = f"API_ID={config.API_ID}"
            if proxy_info:
                proxy_type = proxy_info.get('type', 'http').upper()
                is_residential = "住宅" if proxy_info.get('is_residential', False) else "普通"
                proxy_display = f"{is_residential}{proxy_type}代理"
                print(f"🔍 [{account_name}] 使用 {device_info} | {proxy_display} | 超时={client_timeout}s")
            else:
                print(f"🔍 [{account_name}] 使用 {device_info} | 本地连接 | 超时={connect_timeout}s")
            
            client = TelegramClient(
                session_base,
                int(config.API_ID),
                str(config.API_HASH),
                timeout=client_timeout,
                connection_retries=2,  # 增加连接重试次数
                retry_delay=1,
                proxy=proxy_dict
            )
            
            # 连接（带超时）
            print(f"⏳ [{account_name}] 正在连接到Telegram服务器...")
            try:
                await asyncio.wait_for(client.connect(), timeout=connect_timeout)
                print(f"✅ [{account_name}] 连接成功")
            except asyncio.TimeoutError:
                last_error = "连接超时"
                error_reason = "timeout" if config.PROXY_SHOW_FAILURE_REASON else "连接超时"
                return "连接错误", f"{proxy_used} | {error_reason}", account_name
            except Exception as e:
                error_msg = str(e).lower()
                # 检测冻结账户相关错误
                if "deactivated" in error_msg or "banned" in error_msg:
                    return "冻结", f"{proxy_used} | 账号已被冻结/停用", account_name
                
                # 分类错误原因
                if "timeout" in error_msg:
                    error_reason = "timeout"
                elif "connection refused" in error_msg or "refused" in error_msg:
                    error_reason = "connection_refused"
                elif "auth" in error_msg or "authentication" in error_msg:
                    error_reason = "auth_failed"
                elif "resolve" in error_msg or "dns" in error_msg:
                    error_reason = "dns_error"
                else:
                    error_reason = "network_error"
                
                if config.PROXY_SHOW_FAILURE_REASON:
                    return "连接错误", f"{proxy_used} | {error_reason}", account_name
                else:
                    return "连接错误", f"{proxy_used} | 连接失败", account_name
            
            # 2. 检查账号是否登录/授权（带超时）
            try:
                is_authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=15)
                if not is_authorized:
                    # 无法登录才是真正的封禁
                    return "封禁", "账号未登录或已失效", account_name
            except asyncio.TimeoutError:
                return "连接错误", f"{proxy_used} | 授权检查超时", account_name
            except Exception as e:
                error_msg = str(e).lower()
                # 检测冻结账户相关错误 - 这些错误意味着账号无法登录
                if "deactivated" in error_msg or "banned" in error_msg or "deleted" in error_msg:
                    return "冻结", f"{proxy_used} | 账号已被冻结/删除", account_name
                if "auth key" in error_msg or "unregistered" in error_msg:
                    return "封禁", f"{proxy_used} | 会话密钥无效", account_name
                return "连接错误", f"{proxy_used} | 授权检查失败: {str(e)[:30]}", account_name
            
            # 3. 获取账号基本信息验证（带超时）
            user_info = "账号"
            try:
                me = await asyncio.wait_for(client.get_me(), timeout=15)
                if not me:
                    # 能登录但无法获取信息 - 不是封禁，是连接问题
                    return "连接错误", f"{proxy_used} | 无法获取账号信息", account_name
                user_info = f"ID:{me.id}"
                if me.username:
                    user_info += f" @{me.username}"
                if me.first_name:
                    user_info += f" {me.first_name}"
            except asyncio.TimeoutError:
                return "连接错误", f"{proxy_used} | 获取账号信息超时", account_name
            except Exception as e:
                error_msg = str(e).lower()
                # 检测冻结账户相关错误 - 这些错误意味着账号已被系统冻结
                if "deactivated" in error_msg or "banned" in error_msg or "deleted" in error_msg:
                    return "冻结", f"{proxy_used} | 账号已被冻结/删除", account_name
                # 快速模式下用户信息获取失败不算严重错误 - 能登录就不是封禁
                if not config.PROXY_FAST_MODE:
                    return "连接错误", f"{proxy_used} | 账号信息获取失败: {str(e)[:30]}", account_name
            
            # 4. 发送消息给 SpamBot（带超时）
            try:
                await asyncio.wait_for(
                    client.send_message('SpamBot', '/start'), 
                    timeout=15
                )
                await asyncio.sleep(2)  # 等待响应
                
                # 获取最新消息（带超时）
                messages = await asyncio.wait_for(
                    client.get_messages('SpamBot', limit=5), 
                    timeout=15
                )
                
                if messages:
                    # 查找SpamBot的回复（跳过自己发送的消息）
                    spambot_reply = None
                    for msg in messages:
                        if msg.message and not msg.out:  # 不是自己发送的消息
                            spambot_reply = msg.message
                            break
                    
                    if spambot_reply:
                        english_reply = self.translate_to_english(spambot_reply)
                        status = self.analyze_spambot_response(english_reply.lower())
                        
                        # 如果是介绍页面，重试发送 /start
                        if status == 'intro':
                            print(f"🔄 [{account_name}] 检测到介绍页面，重试发送/start...")
                            await asyncio.sleep(1)
                            await asyncio.wait_for(
                                client.send_message('SpamBot', '/start'), 
                                timeout=15
                            )
                            await asyncio.sleep(2)
                            
                            # 重新获取消息
                            retry_messages = await asyncio.wait_for(
                                client.get_messages('SpamBot', limit=3), 
                                timeout=15
                            )
                            
                            # 再次查找SpamBot的回复
                            for retry_msg in retry_messages:
                                if retry_msg.message and not retry_msg.out:
                                    retry_reply = retry_msg.message
                                    english_retry = self.translate_to_english(retry_reply)
                                    retry_status = self.analyze_spambot_response(english_retry.lower())
                                    
                                    # 如果不再是intro，使用新状态
                                    if retry_status != 'intro':
                                        status = retry_status
                                        spambot_reply = retry_reply
                                        print(f"✅ [{account_name}] 重试成功，获取到状态: {status}")
                                        break
                            
                            # 如果重试后仍是intro或未知，返回未知状态
                            if status == 'intro':
                                print(f"⚠️ [{account_name}] 重试后仍是介绍页面，返回未知状态")
                                return "未知", f"{user_info} | {proxy_used} | SpamBot返回介绍页面（无法获取状态）", account_name
                        
                        # 如果状态是"未知"，不应该判定为封禁
                        if status == '未知':
                            return "未知", f"{user_info} | {proxy_used} | 无法识别SpamBot响应", account_name
                        
                        # 快速模式下简化回复信息
                        if config.PROXY_FAST_MODE:
                            reply_preview = spambot_reply[:20] + "..." if len(spambot_reply) > 20 else spambot_reply
                        else:
                            reply_preview = spambot_reply[:30] + "..." if len(spambot_reply) > 30 else spambot_reply
                        
                        # 构建详细信息字符串，包含连接时间
                        total_elapsed = time.time() - connect_start
                        info_str = f"{user_info} | {proxy_used}"
                        if config.PROXY_DEBUG_VERBOSE:
                            info_str += f" (ok {total_elapsed:.2f}s)"
                        info_str += f" | {reply_preview}"
                        
                        return status, info_str, account_name
                    else:
                        return "连接错误", f"{user_info} | {proxy_used} | SpamBot无响应", account_name
                else:
                    return "连接错误", f"{user_info} | {proxy_used} | SpamBot无响应", account_name
                    
            except asyncio.TimeoutError:
                last_error = "SpamBot通信超时"
                print(f"⏱️ [{account_name}] SpamBot通信超时")
                return "连接错误", f"{user_info} | {proxy_used} | SpamBot通信超时", account_name
            except Exception as e:
                error_str = str(e).lower()
                error_type = type(e).__name__
                
                # 打印详细的异常信息用于调试
                print(f"❌ [{account_name}] SpamBot通信异常: {error_type} - {str(e)[:100]}")
                
                # 检测用户屏蔽了SpamBot的情况 - 尝试自动解除屏蔽
                if "youblockeduser" in error_type.lower() or "you blocked" in error_str:
                    print(f"🔓 [{account_name}] 检测到用户屏蔽了SpamBot，尝试自动解除屏蔽...")
                    try:
                        # 解除屏蔽 SpamBot
                        from telethon.tl.functions.contacts import UnblockRequest
                        await client(UnblockRequest(id='SpamBot'))
                        print(f"✅ [{account_name}] 已自动解除对SpamBot的屏蔽")
                        
                        # 等待一下，然后重新尝试发送消息
                        await asyncio.sleep(1)
                        await asyncio.wait_for(
                            client.send_message('SpamBot', '/start'), 
                            timeout=15
                        )
                        await asyncio.sleep(2)
                        
                        # 重新获取消息
                        messages = await asyncio.wait_for(
                            client.get_messages('SpamBot', limit=5), 
                            timeout=15
                        )
                        
                        if messages:
                            for msg in messages:
                                if msg.message and not msg.out:
                                    spambot_reply = msg.message
                                    english_reply = self.translate_to_english(spambot_reply)
                                    status = self.analyze_spambot_response(english_reply.lower())
                                    
                                    if status == '未知':
                                        return "未知", f"{user_info} | {proxy_used} | 无法识别SpamBot响应", account_name
                                    
                                    reply_preview = spambot_reply[:30] + "..." if len(spambot_reply) > 30 else spambot_reply
                                    total_elapsed = time.time() - connect_start
                                    info_str = f"{user_info} | {proxy_used}"
                                    if config.PROXY_DEBUG_VERBOSE:
                                        info_str += f" (ok {total_elapsed:.2f}s)"
                                    info_str += f" | {reply_preview}"
                                    print(f"✅ [{account_name}] 解除屏蔽后成功获取状态: {status}")
                                    return status, info_str, account_name
                        
                        # 如果解除屏蔽后仍无响应
                        return "未知", f"{user_info} | {proxy_used} | 已解除屏蔽但SpamBot无响应", account_name
                        
                    except Exception as unblock_error:
                        print(f"⚠️ [{account_name}] 自动解除屏蔽失败: {str(unblock_error)[:50]}")
                        return "未知", f"{user_info} | {proxy_used} | 用户已屏蔽SpamBot（自动解除失败）", account_name
                
                # 检测冻结账户相关错误
                if "deactivated" in error_str or "banned" in error_str or "deleted" in error_str:
                    return "冻结", f"{user_info} | {proxy_used} | 账号已被冻结", account_name
                
                # 能登录但无法访问SpamBot的情况：检查特定的Telegram API错误
                # 只检查真正的权限/访问错误，不检查包含"limited"等词的一般错误
                if "peerflood" in error_type.lower() or "chatrestricted" in error_type.lower():
                    print(f"🚫 [{account_name}] 检测到账号受限错误: {error_type}")
                    return "连接错误", f"{user_info} | {proxy_used} | 无法访问SpamBot（账号受限）", account_name
                if ("peer" in error_str and "access" in error_str) or "userprivacy" in error_type.lower():
                    print(f"🚫 [{account_name}] 检测到权限问题: {error_type}")
                    return "连接错误", f"{user_info} | {proxy_used} | 无法访问SpamBot（权限问题）", account_name
                
                # 其他错误统一返回通信失败，并显示详细错误信息
                last_error = str(e)
                print(f"⚠️ [{account_name}] SpamBot通信失败，错误类型: {error_type}")
                return "连接错误", f"{user_info} | {proxy_used} | SpamBot通信失败: {error_type}", account_name
            
        except asyncio.TimeoutError:
            last_error = "连接超时"
            return "连接错误", f"{proxy_used} | 连接超时", account_name
            
        except ConnectionError as e:
            last_error = f"连接错误: {str(e)}"
            return "连接错误", f"{proxy_used} | 连接错误: {str(e)[:30]}", account_name
            
        except Exception as e:
            error_msg = str(e).lower()
            # 检测冻结账户相关错误
            if "deactivated" in error_msg or "banned" in error_msg or "deleted" in error_msg:
                return "冻结", f"{proxy_used} | 账号已被冻结/删除", account_name
            
            # 分类错误原因
            if "timeout" in error_msg:
                error_reason = "timeout"
            elif "connection" in error_msg or "network" in error_msg:
                error_reason = "connection_error"
            elif "resolve" in error_msg:
                error_reason = "dns_error"
            else:
                error_reason = "unknown"
            
            last_error = str(e)
            if config.PROXY_SHOW_FAILURE_REASON:
                return "连接错误", f"{proxy_used} | {error_reason}", account_name
            else:
                return "连接错误", f"{proxy_used} | 检测失败", account_name
        finally:
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
    
    async def _quick_connection_test(self, session_path: str) -> bool:
        """快速连接预测试"""
        try:
            # 检查session文件是否存在且有效
            if not os.path.exists(session_path):
                return False
            
            # 检查文件大小（太小的session文件通常无效）
            if os.path.getsize(session_path) < 100:
                return False
            
            return True
        except:
            return False
    
    def analyze_spambot_response(self, response: str) -> str:
        """更精准的 SpamBot 响应分析（增强版）
        
        支持多语言关键词匹配（中文、英文、俄文等）
        区分临时限制和永久限制
        识别更多状态类型
        
        检测优先级（从高到低）：
        1. 介绍页面（首次访问）- 需要重试
        2. 地理限制（判定为无限制）- 最高优先级
        3. 冻结（永久限制）- 最严重
        4. 临时限制
        5. 垃圾邮件限制
        6. 等待验证
        7. 无限制（正常）
        8. 未知响应（默认为未知而不是封禁）
        """
        if not response:
            return "无响应"
        
        response_lower = response.lower()
        # 翻译并转换为英文进行匹配
        response_en = self.translate_to_english(response).lower()
        
        # 1. 首先检查是否是介绍页面（首次访问SpamBot）
        intro_keywords = [
            "what can this bot do",
            "i'm telegram's official spam info bot",
            "hello! i'm telegram's official spam info bot",
            "this bot is part of telegram",
        ]
        for keyword in intro_keywords:
            if keyword in response_lower or keyword in response_en:
                return "intro"
        
        # 2. 检查地理限制（判定为无限制）- 最高优先级
        # "some phone numbers may trigger a harsh response" 是地理限制提示，不是双向限制
        for pattern in self.status_patterns["地理限制"]:
            pattern_lower = pattern.lower()
            if pattern_lower in response_lower or pattern_lower in response_en:
                return "无限制"
        
        # 3. 检查冻结/永久限制状态（最严重）
        # 注意：只有明确包含这些关键词才判定为冻结
        for pattern in self.status_patterns["冻结"]:
            pattern_lower = pattern.lower()
            if pattern_lower in response_lower or pattern_lower in response_en:
                return "冻结"
        
        # 4. 检查临时限制状态
        for pattern in self.status_patterns["临时限制"]:
            pattern_lower = pattern.lower()
            if pattern_lower in response_lower or pattern_lower in response_en:
                return "临时限制"
        
        # 5. 检查一般垃圾邮件限制
        for pattern in self.status_patterns["垃圾邮件"]:
            pattern_lower = pattern.lower()
            if pattern_lower in response_lower or pattern_lower in response_en:
                return "垃圾邮件"
        
        # 6. 检查等待验证状态
        for pattern in self.status_patterns["等待验证"]:
            pattern_lower = pattern.lower()
            if pattern_lower in response_lower or pattern_lower in response_en:
                return "等待验证"
        
        # 7. 检查无限制（正常状态）
        for pattern in self.status_patterns["无限制"]:
            pattern_lower = pattern.lower()
            if pattern_lower in response_lower or pattern_lower in response_en:
                return "无限制"
        
        # 8. 未知响应 - 返回"未知"而不是默认为封禁
        return "未知"
    
    def get_proxy_usage_stats(self) -> Dict[str, int]:
        """
        获取代理使用统计
        
        注意：统计的是账户数量，而不是代理尝试次数
        每个账户只统计最终结果（成功、失败或回退）
        """
        # 使用字典去重，确保每个账户只统计一次（取最后一条记录）
        account_records = {}
        for record in self.proxy_usage_records:
            account_records[record.account_name] = record
        
        stats = {
            "total": len(account_records),  # 账户总数
            "proxy_success": 0,      # 成功使用代理的账户数
            "proxy_failed": 0,       # 代理失败但未回退的账户数
            "local_fallback": 0,     # 代理失败后回退本地的账户数
            "local_only": 0          # 未尝试代理的账户数
        }
        
        for record in account_records.values():
            if record.proxy_attempted:
                # 尝试了代理
                if record.attempt_result == "success":
                    stats["proxy_success"] += 1
                elif record.fallback_used:
                    stats["local_fallback"] += 1
                else:
                    stats["proxy_failed"] += 1
            else:
                # 未尝试代理（本地连接或回退）
                if record.fallback_used:
                    stats["local_fallback"] += 1
                else:
                    stats["local_only"] += 1
        
        return stats
    
    async def check_tdata_with_spambot(self, tdata_path: str, tdata_name: str, db: 'Database') -> Tuple[str, str, str]:
        """基于opentele的真正TData SpamBot检测（带代理支持）"""
        if not OPENTELE_AVAILABLE:
            return "连接错误", "opentele库未安装", tdata_name
        
        # 检查是否应使用代理
        proxy_enabled = db.get_proxy_enabled() if db else True
        use_proxy = config.USE_PROXY and proxy_enabled and self.proxy_manager.proxies
        
        # 确定重试次数
        max_proxy_attempts = self.max_retries if use_proxy else 0
        
        # 尝试不同的代理
        all_timeout = True  # 标记是否所有代理都是超时
        last_result = None
        
        for proxy_attempt in range(max_proxy_attempts + 1):
            proxy_info = None
            
            # 获取代理（如果启用）
            if use_proxy and proxy_attempt < max_proxy_attempts:
                proxy_info = self.proxy_manager.get_next_proxy()
                if config.PROXY_DEBUG_VERBOSE and proxy_info:
                    print(f"[#{proxy_attempt + 1}] 使用代理检测TData {tdata_name}")
            
            # 尝试检测
            result = await self._single_tdata_check_with_proxy(
                tdata_path, tdata_name, proxy_info, proxy_attempt
            )
            last_result = result
            
            # 检查是否为超时错误
            is_timeout = "timeout" in result[1].lower() or "超时" in result[1]
            if not is_timeout and result[0] == "连接错误":
                all_timeout = False  # 有非超时的连接错误
            
            # 如果成功，返回
            if result[0] != "连接错误":
                return result
            
            # 如果到达最后一次尝试，跳出循环
            if proxy_attempt >= max_proxy_attempts:
                break
            
            # 重试间隔延迟
            if config.PROXY_DEBUG_VERBOSE:
                print(f"TData连接失败 ({result[1][:50]}), 重试下一个代理...")
            await asyncio.sleep(self.retry_delay)
        
        # 只有所有代理都超时时，才尝试本地连接
        if use_proxy and all_timeout:
            if config.PROXY_DEBUG_VERBOSE:
                print(f"所有代理均超时，回退到本地连接: {tdata_name}")
            return await self._single_tdata_check_with_proxy(tdata_path, tdata_name, None, max_proxy_attempts)
        
        # 如果不是超时错误，直接返回最后的错误结果
        if last_result:
            return last_result
        
        return "连接错误", f"检查失败 (重试{max_proxy_attempts}次): 多次尝试后仍然失败", tdata_name
    
    async def _single_tdata_check_with_proxy(self, tdata_path: str, tdata_name: str, 
                                              proxy_info: Optional[Dict], attempt: int) -> Tuple[str, str, str]:
        """带代理的单个TData检查（增强版）"""
        client = None
        session_name = None
        
        # 构建代理描述字符串
        if proxy_info:
            proxy_type_display = "住宅代理" if proxy_info.get('is_residential', False) else "代理"
            proxy_used = f"使用{proxy_type_display}"
        else:
            proxy_used = "本地连接"
        
        try:
            # 1. TData转Session（采用opentele方式）
            tdesk = TDesktop(tdata_path)
            
            if not tdesk.isLoaded():
                return "连接错误", f"{proxy_used} | TData未授权或无效", tdata_name
            
            # 临时session文件保存在sessions/temp目录
            os.makedirs(config.SESSIONS_BAK_DIR, exist_ok=True)
            # 使用time_ns()避免整数溢出问题
            temp_session_name = f"temp_{time.time_ns()}_{attempt}"
            session_name = os.path.join(config.SESSIONS_BAK_DIR, temp_session_name)
            
            # 创建代理字典（如果提供了proxy_info）
            proxy_dict = None
            if proxy_info:
                proxy_dict = self.create_proxy_dict(proxy_info)
                if not proxy_dict:
                    return "连接错误", f"{proxy_used} | 代理配置错误", tdata_name
            
            # 根据代理类型调整超时时间
            if proxy_info and proxy_info.get('is_residential', False):
                client_timeout = config.RESIDENTIAL_PROXY_TIMEOUT
                connect_timeout = config.RESIDENTIAL_PROXY_TIMEOUT
            else:
                client_timeout = self.fast_timeout
                connect_timeout = self.connection_timeout if proxy_dict else 6
            
            # 先转换为Telethon session文件（不连接）
            # 注意：ToTelethon会创建session文件但可能会自动连接，需要先断开
            temp_client = await tdesk.ToTelethon(
                session=session_name, 
                flag=UseCurrentSession, 
                api=API.TelegramDesktop
            )
            await temp_client.disconnect()
            
            # 使用session文件创建新的客户端（带或不带代理）
            client = TelegramClient(
                session_name,
                int(config.API_ID),
                str(config.API_HASH),
                timeout=client_timeout,
                connection_retries=2,
                retry_delay=1,
                proxy=proxy_dict  # None if no proxy
            )
            
            # 2. 连接测试（带超时）
            try:
                await asyncio.wait_for(client.connect(), timeout=connect_timeout)
            except asyncio.TimeoutError:
                return "连接错误", f"{proxy_used} | 连接超时", tdata_name
            except Exception as e:
                error_msg = str(e).lower()
                if "deactivated" in error_msg or "banned" in error_msg:
                    return "冻结", f"{proxy_used} | 账号已被冻结/停用", tdata_name
                
                if "timeout" in error_msg:
                    error_reason = "timeout"
                elif "connection refused" in error_msg or "refused" in error_msg:
                    error_reason = "connection_refused"
                elif "auth" in error_msg or "authentication" in error_msg:
                    error_reason = "auth_failed"
                elif "resolve" in error_msg or "dns" in error_msg:
                    error_reason = "dns_error"
                else:
                    error_reason = "network_error"
                
                if config.PROXY_SHOW_FAILURE_REASON:
                    return "连接错误", f"{proxy_used} | {error_reason}", tdata_name
                else:
                    return "连接错误", f"{proxy_used} | 连接失败", tdata_name
            
            # 3. 检查授权状态（带超时）
            try:
                is_authorized = await asyncio.wait_for(client.is_user_authorized(), timeout=15)
                if not is_authorized:
                    # 无法登录才是真正的封禁
                    return "封禁", f"{proxy_used} | 账号未授权", tdata_name
            except asyncio.TimeoutError:
                return "连接错误", f"{proxy_used} | 授权检查超时", tdata_name
            except Exception as e:
                error_msg = str(e).lower()
                # 这些错误意味着账号无法登录
                if "deactivated" in error_msg or "banned" in error_msg or "deleted" in error_msg:
                    return "冻结", f"{proxy_used} | 账号已被冻结/删除", tdata_name
                if "auth key" in error_msg or "unregistered" in error_msg:
                    return "封禁", f"{proxy_used} | 会话密钥无效", tdata_name
                return "连接错误", f"{proxy_used} | 授权检查失败: {str(e)[:30]}", tdata_name
            
            # 4. 获取手机号（带超时）
            try:
                me = await asyncio.wait_for(client.get_me(), timeout=15)
                phone = me.phone if me.phone else "未知号码"
            except asyncio.TimeoutError:
                phone = "未知号码"
                logger.warning(f"获取手机号超时: {tdata_name}")
            except Exception as e:
                error_msg = str(e).lower()
                # 检测冻结账户相关错误
                if "deactivated" in error_msg or "banned" in error_msg or "deleted" in error_msg:
                    return "冻结", f"{proxy_used} | 账号已被冻结/删除", tdata_name
                # 能登录但无法获取信息 - 不是封禁
                phone = "未知号码"
            
            # 5. 冻结检测（采用FloodError检测）
            try:
                from telethon.tl.functions.account import GetPrivacyRequest
                from telethon.tl.types import InputPrivacyKeyPhoneNumber
                
                privacy_key = InputPrivacyKeyPhoneNumber()
                await asyncio.wait_for(client(GetPrivacyRequest(key=privacy_key)), timeout=5)
            except Exception as e:
                error_str = str(e).lower()
                if 'flood' in error_str:
                    return "冻结", f"手机号:{phone} | {proxy_used} | 账号冻结", tdata_name
            
            # 6. SpamBot检测（带超时）
            # 定义快速模式等待时间为常量
            SPAMBOT_FAST_WAIT = 0.1
            try:
                await asyncio.wait_for(client.send_message('SpamBot', '/start'), timeout=5)
                await asyncio.sleep(config.SPAMBOT_WAIT_TIME if not config.PROXY_FAST_MODE else SPAMBOT_FAST_WAIT)
                
                entity = await client.get_entity(178220800)  # SpamBot固定ID
                messages_found = False
                spambot_reply = None
                
                async for message in client.iter_messages(entity, limit=5):
                    if message.raw_text and not message.out:  # 找到SpamBot的回复
                        messages_found = True
                        spambot_reply = message.raw_text
                        text = spambot_reply.lower()
                        
                        # 智能翻译和状态判断
                        english_text = self.translate_to_english(text)
                        
                        # 使用统一的analyze_spambot_response方法
                        status = self.analyze_spambot_response(english_text)
                        
                        # 如果是介绍页面，重试
                        if status == 'intro':
                            print(f"🔄 [TData:{tdata_name}] 检测到介绍页面，重试发送/start...")
                            await asyncio.sleep(1)
                            await asyncio.wait_for(client.send_message('SpamBot', '/start'), timeout=5)
                            await asyncio.sleep(config.SPAMBOT_WAIT_TIME if not config.PROXY_FAST_MODE else SPAMBOT_FAST_WAIT)
                            
                            # 重新获取消息
                            async for retry_message in client.iter_messages(entity, limit=3):
                                if retry_message.raw_text and not retry_message.out:
                                    retry_text = retry_message.raw_text
                                    english_retry = self.translate_to_english(retry_text.lower())
                                    retry_status = self.analyze_spambot_response(english_retry)
                                    
                                    if retry_status != 'intro':
                                        status = retry_status
                                        spambot_reply = retry_text
                                        print(f"✅ [TData:{tdata_name}] 重试成功，获取到状态: {status}")
                                        break
                            
                            # 如果重试后仍是intro
                            if status == 'intro':
                                print(f"⚠️ [TData:{tdata_name}] 重试后仍是介绍页面，返回未知状态")
                                return "未知", f"手机号:{phone} | {proxy_used} | SpamBot返回介绍页面（无法获取状态）", tdata_name
                        
                        # 如果是未知状态
                        if status == '未知':
                            return "未知", f"手机号:{phone} | {proxy_used} | 无法识别SpamBot响应", tdata_name
                        
                        # 返回检测到的状态
                        return status, f"手机号:{phone} | {proxy_used} | {spambot_reply[:30]}...", tdata_name
                
                # 如果没有找到消息回复 - 能登录但无响应不是封禁
                if not messages_found:
                    return "连接错误", f"手机号:{phone} | {proxy_used} | SpamBot无响应", tdata_name
        
            except asyncio.TimeoutError:
                print(f"⏱️ [TData:{tdata_name}] SpamBot检测超时")
                return "连接错误", f"手机号:{phone} | {proxy_used} | SpamBot检测超时", tdata_name
            except Exception as e:
                error_str = str(e).lower()
                error_type = type(e).__name__
                
                # 打印详细的异常信息用于调试
                print(f"❌ [TData:{tdata_name}] SpamBot通信异常: {error_type} - {str(e)[:100]}")
                
                # 检测用户屏蔽了SpamBot的情况 - 尝试自动解除屏蔽
                if "youblockeduser" in error_type.lower() or "you blocked" in error_str:
                    print(f"🔓 [TData:{tdata_name}] 检测到用户屏蔽了SpamBot，尝试自动解除屏蔽...")
                    try:
                        # 解除屏蔽 SpamBot
                        from telethon.tl.functions.contacts import UnblockRequest
                        await client(UnblockRequest(id='SpamBot'))
                        print(f"✅ [TData:{tdata_name}] 已自动解除对SpamBot的屏蔽")
                        
                        # 等待一下，然后重新尝试
                        await asyncio.sleep(1)
                        await asyncio.wait_for(client.send_message('SpamBot', '/start'), timeout=5)
                        await asyncio.sleep(config.SPAMBOT_WAIT_TIME if not config.PROXY_FAST_MODE else 0.1)
                        
                        entity = await client.get_entity(178220800)  # SpamBot固定ID
                        async for message in client.iter_messages(entity, limit=5):
                            if message.raw_text and not message.out:
                                spambot_reply = message.raw_text
                                english_text = self.translate_to_english(spambot_reply.lower())
                                status = self.analyze_spambot_response(english_text)
                                
                                if status == '未知':
                                    return "未知", f"手机号:{phone} | {proxy_used} | 无法识别SpamBot响应", tdata_name
                                
                                print(f"✅ [TData:{tdata_name}] 解除屏蔽后成功获取状态: {status}")
                                return status, f"手机号:{phone} | {proxy_used} | {spambot_reply[:30]}...", tdata_name
                        
                        # 如果解除屏蔽后仍无响应
                        return "未知", f"手机号:{phone} | {proxy_used} | 已解除屏蔽但SpamBot无响应", tdata_name
                        
                    except Exception as unblock_error:
                        print(f"⚠️ [TData:{tdata_name}] 自动解除屏蔽失败: {str(unblock_error)[:50]}")
                        return "未知", f"手机号:{phone} | {proxy_used} | 用户已屏蔽SpamBot（自动解除失败）", tdata_name
                
                # 检测账号被系统冻结的错误
                if "deactivated" in error_str or "deleted" in error_str:
                    return "冻结", f"手机号:{phone} | {proxy_used} | 账号已被冻结", tdata_name
                
                # 能登录但无法访问SpamBot - 检查特定的Telegram API错误
                if "peerflood" in error_type.lower() or "chatrestricted" in error_type.lower():
                    print(f"🚫 [TData:{tdata_name}] 检测到账号受限错误: {error_type}")
                    return "连接错误", f"手机号:{phone} | {proxy_used} | 无法访问SpamBot（账号受限）", tdata_name
                if ("peer" in error_str and "access" in error_str) or "userprivacy" in error_type.lower():
                    print(f"🚫 [TData:{tdata_name}] 检测到权限问题: {error_type}")
                    return "连接错误", f"手机号:{phone} | {proxy_used} | 无法访问SpamBot（权限问题）", tdata_name
                
                print(f"⚠️ [TData:{tdata_name}] SpamBot通信失败，错误类型: {error_type}")
                return "连接错误", f"手机号:{phone} | {proxy_used} | SpamBot检测失败: {error_type}", tdata_name
                
        except Exception as e:
            error_str = str(e).lower()
            if 'database is locked' in error_str:
                return "连接错误", f"{proxy_used} | TData文件被占用", tdata_name
            elif 'timeout' in error_str or 'connection' in error_str:
                return "连接错误", f"{proxy_used} | 连接超时", tdata_name
            else:
                return "连接错误", f"{proxy_used} | 连接失败: {str(e)[:30]}", tdata_name
        finally:
            # 清理资源
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            # 清理临时session文件
            if session_name:
                try:
                    session_file = f"{session_name}.session"
                    if os.path.exists(session_file):
                        os.remove(session_file)
                    session_journal = f"{session_name}.session-journal"
                    if os.path.exists(session_journal):
                        os.remove(session_journal)
                except:
                    pass
