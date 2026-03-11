"""
utils.async_helpers - Async utility functions
"""
import asyncio
import logging
import os
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.constants import TDATA_CONVERT_TIMEOUT
from core.config import Config
from models.dataclasses import ProfileUpdateConfig
from utils.helpers import copy_session_to_temp, cleanup_temp_session, extract_phone_from_path

logger = logging.getLogger(__name__)

config = Config()

# 并发控制参数
MAX_CONCURRENT = 15  # 最大并发数
DELAY_BETWEEN = 0.3  # 任务间延迟（秒）

try:
    from telethon import TelegramClient
    from telethon.errors import (
        SessionPasswordNeededError, AuthKeyError,
        UsernameOccupiedError, UsernameInvalidError,
    )
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False

try:
    import socks
    PROXY_SUPPORT = True
except ImportError:
    PROXY_SUPPORT = False

try:
    from opentele.td import TDesktop
    from opentele.api import UseCurrentSession
    OPENTELE_AVAILABLE = True
except ImportError:
    OPENTELE_AVAILABLE = False


async def safe_process_with_retry(func, *args, max_retries=3, **kwargs):
    """带重试的安全执行
    
    Args:
        func: 要执行的异步函数
        *args: 位置参数
        max_retries: 最大重试次数
        **kwargs: 关键字参数
        
    Returns:
        函数执行结果
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_error = e
            logger.warning(f"执行失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))  # 递增延迟
                continue
    raise last_error


async def _process_session_internal(session_path: str, api_id: int, api_hash: str,
                                    proxy: Optional[Dict], profile_data: Dict,
                                    proxy_manager: 'ProxyManager' = None,
                                    db: 'Database' = None) -> Dict:
    """内部session处理函数（不含超时逻辑）
    
    Args:
        session_path: session 文件路径
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        proxy: 代理配置字典
        profile_data: 资料更新数据
        proxy_manager: 代理管理器实例（可选）
        db: 数据库实例（可选）
        
    Returns:
        处理结果字典
    """
    temp_dir = None
    temp_session = None
    client = None
    
    try:
        # 复制 session 到临时目录，避免并发冲突
        temp_session, temp_dir = copy_session_to_temp(session_path)
        
        # 创建代理配置（如果提供）
        proxy_dict = None
        if proxy:
            proxy_type_map = {
                'http': socks.HTTP,
                'socks4': socks.SOCKS4,
                'socks5': socks.SOCKS5
            }
            proxy_type = proxy_type_map.get(proxy.get('type', 'http').lower(), socks.HTTP)
            
            proxy_dict = {
                'proxy_type': proxy_type,
                'addr': proxy['host'],
                'port': proxy['port'],
                'username': proxy.get('username'),
                'password': proxy.get('password'),
                'rdns': True
            }
        
        # 使用临时 session 连接
        # 根据代理类型选择合适的超时时间
        timeout = 30 if proxy and proxy.get('is_residential', False) else 10
        
        client = TelegramClient(
            temp_session,
            api_id,
            api_hash,
            proxy=proxy_dict,
            timeout=timeout,
            connection_retries=3,
            retry_delay=1
        )
        
        await client.connect()
        
        if not await client.is_user_authorized():
            return {'success': False, 'error': '账号未授权'}
        
        # 获取账号信息
        me = await client.get_me()
        phone = me.phone if hasattr(me, 'phone') else None
        
        # 修改资料
        result = {
            'success': True,
            'phone': phone,
            'actions': []
        }
        
        # 更新姓名
        if profile_data.get('update_name'):
            first_name = profile_data.get('first_name', '')
            last_name = profile_data.get('last_name', '')
            try:
                from telethon.tl.functions.account import UpdateProfileRequest
                await client(UpdateProfileRequest(
                    first_name=first_name,
                    last_name=last_name
                ))
                result['actions'].append(f"✅ 姓名: {first_name} {last_name}")
            except Exception as e:
                result['actions'].append(f"❌ 姓名更新失败: {str(e)[:50]}")
        
        # 更新简介
        if profile_data.get('update_bio'):
            bio = profile_data.get('bio', '')
            try:
                from telethon.tl.functions.account import UpdateProfileRequest
                await client(UpdateProfileRequest(about=bio))
                result['actions'].append(f"✅ 简介: {bio[:20]}...")
            except Exception as e:
                result['actions'].append(f"❌ 简介更新失败: {str(e)[:50]}")
        
        # 更新用户名
        if profile_data.get('update_username'):
            username = profile_data.get('username', '')
            try:
                from telethon.tl.functions.account import UpdateUsernameRequest
                await client(UpdateUsernameRequest(username=username))
                result['actions'].append(f"✅ 用户名: {username if username else '已删除'}")
            except UsernameOccupiedError:
                result['actions'].append(f"❌ 用户名已被占用")
            except UsernameInvalidError:
                result['actions'].append(f"❌ 用户名格式无效")
            except Exception as e:
                result['actions'].append(f"❌ 用户名更新失败: {str(e)[:50]}")
        
        # 更新头像
        if profile_data.get('update_photo'):
            photo_action = profile_data.get('photo_action', 'keep')
            if photo_action == 'delete_all':
                try:
                    from telethon.tl.functions.photos import DeletePhotosRequest, GetUserPhotosRequest
                    photos = await client(GetUserPhotosRequest(
                        user_id=me,
                        offset=0,
                        max_id=0,
                        limit=100
                    ))
                    if hasattr(photos, 'photos') and photos.photos:
                        await client(DeletePhotosRequest(id=list(photos.photos)))
                        result['actions'].append(f"✅ 删除所有头像")
                    else:
                        result['actions'].append("✅ 没有头像需要删除")
                except Exception as e:
                    result['actions'].append(f"❌ 删除头像失败: {str(e)[:50]}")
            elif photo_action == 'custom':
                photo_path = profile_data.get('photo_path')
                if photo_path and os.path.exists(photo_path):
                    try:
                        from telethon.tl.functions.photos import UploadProfilePhotoRequest
                        await client(UploadProfilePhotoRequest(
                            file=await client.upload_file(photo_path)
                        ))
                        result['actions'].append(f"✅ 上传头像")
                    except Exception as e:
                        result['actions'].append(f"❌ 上传头像失败: {str(e)[:50]}")
        
        await client.disconnect()
        return result
        
    except Exception as e:
        logger.error(f"处理session失败: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        # 清理临时目录
        if client:
            try:
                await client.disconnect()
            except:
                pass
        cleanup_temp_session(temp_dir)


async def safe_process_session(session_path: str, api_id: int, api_hash: str,
                                proxy: Optional[Dict], profile_data: Dict,
                                proxy_manager: 'ProxyManager' = None,
                                db: 'Database' = None,
                                timeout: int = 30) -> Dict:
    """安全处理 session，避免数据库锁定，带超时保护
    
    Args:
        session_path: session 文件路径
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        proxy: 代理配置字典
        profile_data: 资料更新数据
        proxy_manager: 代理管理器实例（可选）
        db: 数据库实例（可选）
        timeout: 处理超时时间（秒），默认30秒
        
    Returns:
        处理结果字典
    """
    try:
        # 使用asyncio.wait_for添加超时保护
        result = await asyncio.wait_for(
            _process_session_internal(session_path, api_id, api_hash, proxy, profile_data, proxy_manager, db),
            timeout=timeout
        )
        return result
    except asyncio.TimeoutError:
        logger.warning(f"账号处理超时（{timeout}秒）: {session_path}")
        return {
            'success': False,
            'error': f'操作超时（{timeout}秒）',
            'error_type': 'Timeout'
        }
    except Exception as e:
        logger.error(f"账号处理失败: {e}")
        return {
            'success': False,
            'error': str(e),
            'error_type': type(e).__name__
        }


async def batch_convert_tdata_to_session(tdata_list: List[Tuple[str, str]],
                                         bot_instance: 'EnhancedBot') -> List[Dict]:
    """并发转换 TData 为 Session
    
    Args:
        tdata_list: TData文件列表 [(文件名, 文件路径), ...]
        bot_instance: EnhancedBot实例，用于访问convert_tdata_to_session方法
        
    Returns:
        转换结果列表
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results = []
    
    async def convert_with_limit(tdata_name: str, tdata_path: str):
        async with semaphore:
            try:
                await asyncio.sleep(DELAY_BETWEEN)  # 小延迟避免请求过快
                
                # 获取随机API凭据
                api_id, api_hash = bot_instance.device_params_manager.get_random_api_credentials()
                if not api_id or not api_hash:
                    api_id = 2040
                    api_hash = 'b18441a1ff607e10a989891a5462e627'
                
                # 添加30秒超时保护
                try:
                    status, info, name = await asyncio.wait_for(
                        bot_instance.convert_tdata_to_session(
                            tdata_path, tdata_name, api_id, api_hash
                        ),
                        timeout=TDATA_CONVERT_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    return {
                        'success': False, 
                        'error': f'TData转换超时（{TDATA_CONVERT_TIMEOUT}秒）', 
                        'error_type': 'Timeout',
                        'tdata': tdata_path,
                        'name': tdata_name
                    }
                
                if status == "转换成功":
                    # 从sessions目录查找转换后的session文件
                    # 文件名应该是手机号.session
                    phone = info.split('手机号: ')[1].split(' |')[0] if '手机号: ' in info else tdata_name
                    session_path = os.path.join(config.SESSIONS_DIR, f"{phone}.session")
                    
                    return {
                        'success': True, 
                        'session': session_path if os.path.exists(session_path) else None,
                        'tdata': tdata_path,
                        'name': tdata_name,
                        'info': info
                    }
                else:
                    return {
                        'success': False, 
                        'error': info, 
                        'tdata': tdata_path,
                        'name': tdata_name
                    }
            except Exception as e:
                logger.error(f"转换TData失败 {tdata_name}: {e}")
                return {
                    'success': False, 
                    'error': str(e), 
                    'tdata': tdata_path,
                    'name': tdata_name
                }
    
    # 并发执行所有转换
    tasks = [convert_with_limit(name, path) for name, path in tdata_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 处理异常结果
    processed_results = []
    for result in results:
        if isinstance(result, Exception):
            processed_results.append({
                'success': False,
                'error': str(result)
            })
        else:
            processed_results.append(result)
    
    return processed_results


async def batch_update_profiles_concurrent(session_list: List[Tuple[str, str]],
                                          profile_config: ProfileUpdateConfig,
                                          profile_manager: 'ProfileManager',
                                          proxy_manager: 'ProxyManager',
                                          db: 'Database',
                                          device_params_manager: 'DeviceParamsManager') -> List[Dict]:
    """并发修改 Session 资料
    
    Args:
        session_list: Session文件列表 [(文件名, 文件路径), ...]
        profile_config: 资料更新配置
        profile_manager: 资料管理器实例
        proxy_manager: 代理管理器实例
        db: 数据库实例
        device_params_manager: 设备参数管理器实例
        
    Returns:
        更新结果列表
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results = []
    
    # 准备代理列表（如果启用）
    proxies = []
    if proxy_manager.is_proxy_mode_active(db):
        # 循环使用可用代理
        proxy_count = len(proxy_manager.proxies)
        if proxy_count > 0:
            proxies = [proxy_manager.get_next_proxy() for _ in range(len(session_list))]
    
    # 如果没有代理，使用None填充
    if not proxies:
        proxies = [None] * len(session_list)
    
    async def update_with_limit(idx: int, session_name: str, session_path: str, proxy: Optional[Dict]):
        async with semaphore:
            try:
                await asyncio.sleep(DELAY_BETWEEN)  # 小延迟避免请求过快
                
                # 获取随机API凭据
                api_id, api_hash = device_params_manager.get_random_api_credentials()
                if not api_id or not api_hash:
                    api_id = 2040
                    api_hash = 'b18441a1ff607e10a989891a5462e627'
                
                # 准备资料数据
                profile_data = {}
                
                # 处理姓名
                if profile_config.update_name:
                    if profile_config.mode == 'random':
                        # 需要获取账号的国家信息来生成对应语言的姓名
                        # 先快速连接获取手机号
                        temp_session, temp_dir = copy_session_to_temp(session_path)
                        try:
                            client = TelegramClient(temp_session, api_id, api_hash)
                            await client.connect()
                            if await client.is_user_authorized():
                                me = await client.get_me()
                                phone = me.phone if hasattr(me, 'phone') else None
                                country = profile_manager.get_country_from_phone(phone) if phone else 'US'
                                first_name, last_name = profile_manager.generate_random_name(country)
                                profile_data['first_name'] = first_name
                                profile_data['last_name'] = last_name
                            await client.disconnect()
                        finally:
                            cleanup_temp_session(temp_dir)
                    elif profile_config.custom_names:
                        # 循环使用自定义姓名
                        full_name = profile_config.custom_names[idx % len(profile_config.custom_names)]
                        parts = full_name.split(' ', 1)
                        profile_data['first_name'] = parts[0]
                        profile_data['last_name'] = parts[1] if len(parts) > 1 else ''
                    
                    profile_data['update_name'] = True
                
                # 处理简介
                if profile_config.update_bio:
                    if profile_config.bio_action == 'clear':
                        profile_data['bio'] = ''
                    elif profile_config.bio_action == 'random':
                        # 使用默认国家生成
                        profile_data['bio'] = profile_manager.generate_random_bio('US')
                    elif profile_config.bio_action == 'custom' and profile_config.custom_bios:
                        profile_data['bio'] = profile_config.custom_bios[idx % len(profile_config.custom_bios)]
                    
                    profile_data['update_bio'] = True
                
                # 处理用户名
                if profile_config.update_username:
                    if profile_config.username_action == 'delete':
                        profile_data['username'] = ''
                    elif profile_config.username_action == 'random':
                        profile_data['username'] = profile_manager.generate_random_username()
                    elif profile_config.username_action == 'custom' and profile_config.custom_usernames:
                        profile_data['username'] = profile_config.custom_usernames[idx % len(profile_config.custom_usernames)]
                    
                    profile_data['update_username'] = True
                
                # 处理头像
                if profile_config.update_photo:
                    profile_data['update_photo'] = True
                    profile_data['photo_action'] = profile_config.photo_action
                    
                    if profile_config.photo_action == 'custom' and profile_config.custom_photos:
                        profile_data['photo_path'] = profile_config.custom_photos[idx % len(profile_config.custom_photos)]
                
                # 使用safe_process_session处理
                result = await safe_process_session(
                    session_path, api_id, api_hash, proxy, profile_data,
                    proxy_manager, db
                )
                
                result['session'] = session_path
                result['name'] = session_name
                return result
                
            except Exception as e:
                logger.error(f"更新资料失败 {session_name}: {e}")
                return {
                    'success': False, 
                    'session': session_path,
                    'name': session_name, 
                    'error': str(e)
                }
    
    # 并发执行所有修改
    tasks = [
        update_with_limit(idx, name, path, proxy) 
        for idx, ((name, path), proxy) in enumerate(zip(session_list, proxies))
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 处理异常结果
    processed_results = []
    for result in results:
        if isinstance(result, Exception):
            processed_results.append({
                'success': False,
                'error': str(result)
            })
        else:
            processed_results.append(result)
    
    return processed_results
