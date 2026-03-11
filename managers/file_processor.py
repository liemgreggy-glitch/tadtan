"""
managers.file_processor - File processing for account checking
"""
import asyncio
import logging
import os
import shutil
import time
import zipfile
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from core.config import Config
from core.constants import (
    TDATA_PIPELINE_CONVERT_CONCURRENT,
    TDATA_PIPELINE_CHECK_CONCURRENT,
    TDATA_PIPELINE_CONVERT_TIMEOUT,
)
from utils.helpers import scan_tdata_accounts

logger = logging.getLogger(__name__)

config = Config()

try:
    from telethon import TelegramClient
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False

try:
    from opentele.api import API, UseCurrentSession
    from opentele.td import TDesktop
    OPENTELE_AVAILABLE = True
except ImportError:
    OPENTELE_AVAILABLE = False

    class TDesktop:
        """Fallback class when opentele not available"""
        pass

    class UseCurrentSession:
        """Fallback class when opentele not available"""
        pass


class FileProcessor:
    """文件处理器"""
    
    def __init__(self, checker: 'SpamBotChecker', db: 'Database'):
        self.checker = checker
        self.db = db
    
    async def convert_tdata_and_check(self, tdata_path: str, tdata_name: str) -> Tuple[str, str, str]:
        """
        将TData转换为临时Session并使用Session检查方法（带代理支持）
        这样可以利用Session检查的代理支持和准确性
        所有操作都会先通过代理连接
        """
        if not OPENTELE_AVAILABLE:
            return "连接错误", "opentele库未安装，无法转换TData", tdata_name
        
        temp_session_path = None
        temp_client = None
        
        try:
            # 1. 加载TData
            tdesk = TDesktop(tdata_path)
            
            if not tdesk.isLoaded():
                return "连接错误", "TData未授权或无效", tdata_name
            
            # 2. 创建临时Session文件
            os.makedirs(config.SESSIONS_BAK_DIR, exist_ok=True)
            temp_session_name = f"tdata_check_{time.time_ns()}"
            temp_session_path = os.path.join(config.SESSIONS_BAK_DIR, temp_session_name)
            
            # 3. 转换TData为Session（使用代理连接）
            # 问题1: TData格式统一转成session来操作任务
            print(f"🔄 [{tdata_name}] 开始TData转Session转换...")
            try:
                # 先转换为Session文件（不自动连接）
                temp_client = await tdesk.ToTelethon(
                    session=temp_session_path,
                    flag=UseCurrentSession,
                    api=API.TelegramDesktop
                )
                # 立即断开，避免非代理连接
                await temp_client.disconnect()
                print(f"✅ [{tdata_name}] TData转换完成")
                
                # 检查Session文件是否生成
                session_file = f"{temp_session_path}.session"
                if not os.path.exists(session_file):
                    return "连接错误", "Session转换失败：文件未生成", tdata_name
                
                # 获取代理配置
                proxy_enabled = self.db.get_proxy_enabled() if self.db else True
                use_proxy = config.USE_PROXY and proxy_enabled and self.checker.proxy_manager.proxies
                
                # 问题3: 控制台显示代理链接信息
                if use_proxy:
                    print(f"📡 [{tdata_name}] 代理模式已启用，可用代理: {len(self.checker.proxy_manager.proxies)}个")
                    proxy_info = self.checker.proxy_manager.get_next_proxy()
                    if proxy_info:
                        proxy_type = proxy_info.get('type', 'http').upper()
                        is_residential = "住宅" if proxy_info.get('is_residential', False) else "普通"
                        print(f"🔗 [{tdata_name}] 选择{is_residential}{proxy_type}代理进行连接测试")
                        
                        proxy_dict = self.checker.create_proxy_dict(proxy_info)
                        if proxy_dict:
                            # 使用代理重新创建客户端
                            temp_client = TelegramClient(
                                temp_session_path,
                                int(config.API_ID),
                                str(config.API_HASH),
                                proxy=proxy_dict
                            )
                            # 测试代理连接
                            try:
                                print(f"⏳ [{tdata_name}] 通过代理连接Telegram服务器...")
                                await asyncio.wait_for(temp_client.connect(), timeout=10)
                                await temp_client.disconnect()
                                print(f"✅ [{tdata_name}] 代理连接测试成功")
                            except Exception as e:
                                print(f"⚠️ [{tdata_name}] 代理连接测试失败: {str(e)[:50]}")
                                print(f"   将在后续检查时重试其他代理")
                        else:
                            print(f"⚠️ [{tdata_name}] 代理配置失败，将在检查时重试")
                    else:
                        print(f"⚠️ [{tdata_name}] 无可用代理，将在检查时使用本地连接")
                else:
                    print(f"ℹ️ [{tdata_name}] 代理未启用或无可用代理，使用本地连接")
                    
            except Exception as e:
                return "连接错误", f"TData转换失败: {str(e)[:50]}", tdata_name
            
            # 4. 使用Session检查方法（带代理支持）
            # 这里会自动使用代理进行完整的账号检查
            status, info, account_name = await self.checker.check_account_status(
                session_file, tdata_name, self.db
            )
            
            return status, info, account_name
            
        except Exception as e:
            error_msg = str(e)
            if 'database is locked' in error_msg.lower():
                return "连接错误", "TData文件被占用", tdata_name
            else:
                return "连接错误", f"TData处理失败: {error_msg[:50]}", tdata_name
        finally:
            # 清理临时客户端连接
            if temp_client:
                try:
                    await temp_client.disconnect()
                except:
                    pass
            
            # 清理临时Session文件
            if temp_session_path:
                try:
                    session_file = f"{temp_session_path}.session"
                    if os.path.exists(session_file):
                        os.remove(session_file)
                    session_journal = f"{temp_session_path}.session-journal"
                    if os.path.exists(session_journal):
                        os.remove(session_journal)
                except Exception as e:
                    logger.warning(f"清理临时Session文件失败: {e}")
    
    def extract_phone_from_tdata_directory(self, tdata_path: str) -> str:
        """
        从TData目录结构中提取手机号
        
        TData目录结构通常是：
        /path/to/phone_number/tdata/D877F783D5D3EF8C/
        或者
        /path/to/tdata/D877F783D5D3EF8C/ (tdata本身在根目录)
        """
        try:
            # 方法1: 从路径中提取 - 找到tdata目录的父目录
            path_parts = tdata_path.split(os.sep)
            
            # 找到"tdata"在路径中的位置
            tdata_index = -1
            for i, part in enumerate(path_parts):
                if part == "tdata":
                    tdata_index = i
                    break
            
            # 如果找到tdata，检查它的父目录
            if tdata_index > 0:
                phone_candidate = path_parts[tdata_index - 1]
                
                # 验证是否为手机号格式
                # 支持格式：+998xxxxxxxxx 或 998xxxxxxxxx 或其他数字
                if phone_candidate.startswith('+'):
                    phone_candidate = phone_candidate[1:]  # 移除+号
                
                if phone_candidate.isdigit() and len(phone_candidate) >= 10:
                    return phone_candidate
            
            # 方法2: 遍历路径中的所有部分，找到看起来像手机号的部分
            for part in reversed(path_parts):
                if part == "tdata" or part == "D877F783D5D3EF8C":
                    continue
                
                # 检查是否为手机号格式
                clean_part = part.lstrip('+')
                if clean_part.isdigit() and len(clean_part) >= 10:
                    return clean_part
            
            # 方法3: 如果都失败了，生成一个基于路径hash的标识符
            import hashlib
            path_hash = hashlib.md5(tdata_path.encode()).hexdigest()[:10]
            return f"tdata_{path_hash}"
            
        except Exception as e:
            print(f"⚠️ 提取手机号失败: {e}")
            # 返回一个基于时间戳的标识符
            return f"tdata_{int(time.time())}"
    
    def _get_account_root_from_tdata_path(self, tdata_root_path: str) -> str:
        """
        从tdata路径提取账号根目录
        
        如果路径以"tdata"结尾，返回其父目录（账号根目录）
        否则返回路径本身
        
        Args:
            tdata_root_path: TData根目录路径（可能是 account/tdata 或其他）
            
        Returns:
            账号根目录路径（通常是手机号目录）
        """
        if os.path.basename(tdata_root_path).lower() == "tdata":
            return os.path.dirname(tdata_root_path)
        return tdata_root_path
    
    def _validate_tdata_structure(self, d877_path: str, check_parent_for_keys: bool = False) -> Tuple[bool, Optional[str]]:
        """
        验证TData目录结构是否有效
        
        Args:
            d877_path: D877F783D5D3EF8C 目录的完整路径
            check_parent_for_keys: 是否检查父目录中的key_data(s)文件（某些TData变体将key文件放在D877目录外）
            
        Returns:
            (is_valid, maps_file_path): 是否有效以及maps文件路径
        """
        try:
            maps_file = os.path.join(d877_path, "maps")
            
            # 首先检查maps文件（必须在D877目录内）
            if not os.path.exists(maps_file):
                return False, None
            
            # 检查maps文件大小（有效的TData maps文件通常大于30字节）
            try:
                maps_size = os.path.getsize(maps_file)
                if maps_size < 30:
                    return False, None
            except:
                return False, None
            
            # 检查key_data(s)文件 - 可能在D877目录内或父目录
            key_data_file = os.path.join(d877_path, "key_data")
            key_datas_file = os.path.join(d877_path, "key_datas")
            has_key_file = os.path.exists(key_data_file) or os.path.exists(key_datas_file)
            
            # 如果D877目录内没有找到key文件，且允许检查父目录
            if not has_key_file and check_parent_for_keys:
                parent_dir = os.path.dirname(d877_path)
                parent_key_data = os.path.join(parent_dir, "key_data")
                parent_key_datas = os.path.join(parent_dir, "key_datas")
                has_key_file = os.path.exists(parent_key_data) or os.path.exists(parent_key_datas)
                
                if has_key_file:
                    print(f"📍 检测到key_datas在D877F783D5D3EF8C的父目录中（变体结构）")
            
            if not has_key_file:
                return False, None
            
            return True, maps_file
        except Exception as e:
            print(f"⚠️ 验证TData结构失败: {e}")
            return False, None
    
    def scan_zip_file(self, zip_path: str, user_id: int, task_id: str) -> Tuple[List[Tuple[str, str]], str, str]:
        """扫描ZIP文件 - 使用统一的tdata扫描逻辑"""
        session_files = []
        tdata_folders = []
        seen_session_files = set()  # 防止重复计数Session文件（基于规范化路径）
        
        # 在uploads目录下为每个任务创建专属文件夹
        task_upload_dir = os.path.join(config.UPLOADS_DIR, f"task_{task_id}")
        os.makedirs(task_upload_dir, exist_ok=True)
        
        print(f"📁 任务上传目录: {task_upload_dir}")
        
        try:
            # 解压到任务专属目录
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(task_upload_dir)
            
            print(f"📦 文件解压完成: {task_upload_dir}")
            
            # 先扫描Session文件
            for root, dirs, files in os.walk(task_upload_dir):
                for file in files:
                    if file.endswith('.session'):
                        # 【修复】过滤掉系统文件和临时文件
                        # 排除: tdata.session (系统文件), batch_validate_*.session (临时文件)
                        if file == 'tdata.session' or file.startswith('batch_validate_') or file.startswith('temp_') or file.startswith('user_'):
                            print(f"⏭️ 跳过系统/临时文件: {file}")
                            continue
                        
                        file_full_path = os.path.join(root, file)
                        
                        # 【关键修复】使用规范化路径防止重复计数
                        # 处理符号链接、硬链接、相对路径等情况
                        normalized_path = os.path.normpath(os.path.abspath(file_full_path))
                        
                        if normalized_path in seen_session_files:
                            print(f"⏭️ 跳过重复Session文件: {file}")
                            continue
                        
                        seen_session_files.add(normalized_path)
                        session_files.append((file_full_path, file))
                        
                        # 检查是否有对应的JSON文件
                        json_path = file_full_path.replace('.session', '.json')
                        if os.path.exists(json_path):
                            print(f"📄 找到Session文件: {file} (带JSON)")
                        else:
                            print(f"📄 找到Session文件: {file} (纯Session，无JSON)")
            
            # 使用统一的tdata扫描函数
            print(f"📂 开始扫描TData账号...")
            tdata_accounts = scan_tdata_accounts(task_upload_dir)
            
            # 将tdata账号转换为与旧格式兼容的元组列表
            for account in tdata_accounts:
                phone = account['phone']
                tdata_path = account['tdata_path']
                tdata_folders.append((tdata_path, phone))
                print(f"📂 找到TData账号: {phone} -> {tdata_path}")
        
        except Exception as e:
            print(f"❌ 文件扫描失败: {e}")
            shutil.rmtree(task_upload_dir, ignore_errors=True)
            return [], "", "error"
        
        # 优先级调整：如果同时存在Session和TData，将TData也转换为Session一起检查
        # 这样可以检查所有账号，而不是忽略TData文件
        if session_files:
            print(f"📱 检测到Session文件，优先使用Session检测（准确性更高）")
            print(f"✅ 找到 {len(session_files)} 个Session文件")
            if tdata_folders:
                print(f"📂 同时发现 {len(tdata_folders)} 个TData文件夹")
                
                # 去重：如果Session和TData中有相同账号，优先使用Session
                # 提取Session文件的账号标识（去掉.session后缀）
                session_accounts = set()
                for _, session_name in session_files:
                    account_id = session_name.replace('.session', '')
                    session_accounts.add(account_id)
                
                # 过滤TData，只保留没有对应Session的账号
                filtered_tdata = []
                duplicate_count = 0
                for tdata_path, tdata_name in tdata_folders:
                    if tdata_name not in session_accounts:
                        filtered_tdata.append((tdata_path, tdata_name))
                    else:
                        duplicate_count += 1
                
                if duplicate_count > 0:
                    print(f"🔄 去重: 发现 {duplicate_count} 个重复账号（Session和TData相同），优先使用Session")
                
                if filtered_tdata:
                    print(f"🔄 将剩余 {len(filtered_tdata)} 个TData文件转换为Session一起检查")
                    # 将去重后的TData和Session合并返回
                    all_files = session_files + filtered_tdata
                    print(f"📊 总计: {len(all_files)} 个唯一账号 (Session: {len(session_files)}, TData: {len(filtered_tdata)})")
                    return all_files, task_upload_dir, "mixed"
                else:
                    print(f"ℹ️ 所有TData账号都有对应的Session文件，无需额外处理")
                    return session_files, task_upload_dir, "session"
            return session_files, task_upload_dir, "session"
        elif tdata_folders:
            print(f"🎯 检测到TData文件，使用TData检测")
            print(f"✅ 找到 {len(tdata_folders)} 个唯一TData文件夹")
            return tdata_folders, task_upload_dir, "tdata"
        else:
            print("❌ 未找到有效的账号文件")
            print("💡 正确的 TData 格式要求:")
            print("   ⚠️ 必须以手机号文件夹开头！")
            print("")
            print("   ✅ 支持的目录结构示例:")
            print("   • 手机号/tdata/D877F783D5D3EF8C/key_datas (标准：key在D877内)")
            print("   • 手机号/tdata/key_datas + D877F783D5D3EF8C/ (变体：key与D877同级)")
            print("   • 手机号/D877F783D5D3EF8C/key_datas (无tdata子目录)")
            print("   • 手机号/其他路径/tdata/... (深层嵌套)")
            print("")
            print("   ❌ 以下结构不被支持:")
            print("   • tdata/D877F783D5D3EF8C/ (缺少手机号文件夹)")
            print("   • D877F783D5D3EF8C/ (缺少手机号文件夹)")
            print("")
            print("   📌 示例:")
            print("   ✅ +8613812345678/tdata/D877F783D5D3EF8C/key_datas")
            print("   ✅ 79001234567/tdata/key_datas (与D877同级)")
            print("   ✅ 79001234567/D877F783D5D3EF8C/key_datas")
            print("   ❌ tdata/D877F783D5D3EF8C/key_datas (无手机号)")
            shutil.rmtree(task_upload_dir, ignore_errors=True)
            return [], "", "none"
    
    async def check_accounts_with_realtime_updates(self, files: List[Tuple[str, str]], file_type: str, update_callback) -> Dict[str, List[Tuple[str, str, str]]]:
        """实时更新检查"""

        # TData类型使用两阶段流水线（更高并发，速度接近Session）
        if file_type == "tdata":
            async def pipeline_callback(done, total, results, speed, elapsed, stage=None):
                if update_callback:
                    await update_callback(done, total, results, speed, elapsed)
            return await self.check_tdata_accounts_pipeline(files, pipeline_callback)

        results = {
            "无限制": [],
            "垃圾邮件": [],
            "冻结": [],
            "封禁": [],
            "连接错误": []
        }
        
        # 状态映射：将各种限制状态映射到正确的分类
        # 临时限制是账号因垃圾邮件行为被限制，应归类为垃圾邮件（spam）
        # 等待验证是账号需要验证，归类为封禁
        # 无响应是网络问题，归类为连接错误
        status_mapping = {
            "临时限制": "垃圾邮件",
            "等待验证": "封禁",
            "无响应": "连接错误",
        }
        
        total = len(files)
        processed = 0
        start_time = time.time()
        last_update_time = 0
        
        async def process_single_account(file_path, file_name):
            nonlocal processed, last_update_time
            try:
                # 问题3: 显示检查进度
                print(f"\n{'='*60}")
                print(f"📋 开始检查账号 [{processed + 1}/{total}]: {file_name}")
                print(f"{'='*60}")
                
                if file_type == "session":
                    status, info, account_name = await self.checker.check_account_status(file_path, file_name, self.db)
                elif file_type == "mixed":
                    # 混合类型：需要判断当前文件是session还是tdata
                    if file_path.endswith('.session'):
                        # Session文件
                        status, info, account_name = await self.checker.check_account_status(file_path, file_name, self.db)
                    else:
                        # TData文件夹
                        print(f"📂 [{file_name}] 格式: TData - 将自动转换为Session进行检查")
                        status, info, account_name = await self.convert_tdata_and_check(file_path, file_name)
                else:  # tdata
                    # 问题1: TData格式统一转换为Session后检查（更准确）
                    print(f"📂 [{file_name}] 格式: TData - 将自动转换为Session进行检查")
                    status, info, account_name = await self.convert_tdata_and_check(file_path, file_name)
                
                # 将状态映射到正确的分类
                mapped_status = status_mapping.get(status, status)
                
                # 如果状态不在结果字典中，记录警告并归类为连接错误
                if mapped_status not in results:
                    print(f"⚠️ 未知状态 '{mapped_status}'，归类为连接错误: {file_name}")
                    mapped_status = "连接错误"
                
                results[mapped_status].append((file_path, file_name, info))
                processed += 1
                
                # 显示检测结果（如果状态被映射，显示原始状态和映射后的状态）
                status_display = f"'{status}' (归类为 '{mapped_status}')" if status != mapped_status else status
                # 防止除以零错误
                progress_pct = int((processed / total) * 100) if total > 0 else 0
                print(f"✅ 检测完成 [{processed}/{total}] ({progress_pct}%): {file_name} -> {status_display}")
                print(f"{'='*60}\n")
                
                # 控制更新频率，每3秒或每10个账号更新一次
                current_time = time.time()
                if (current_time - last_update_time >= 3) or (processed % 10 == 0) or (processed == total):
                    if update_callback:
                        elapsed = time.time() - start_time
                        speed = processed / elapsed if elapsed > 0 else 0
                        await update_callback(processed, total, results, speed, elapsed)
                        last_update_time = current_time
                
            except Exception as e:
                results["连接错误"].append((file_path, file_name, f"异常: {str(e)[:20]}"))
                processed += 1
                print(f"❌ 检测失败 {processed}/{total}: {file_name} -> {str(e)}")
        
        # 分批并发执行
        batch_size = config.MAX_CONCURRENT_CHECKS
        for i in range(0, len(files), batch_size):
            batch = files[i:i + batch_size]
            tasks = [process_single_account(file_path, file_name) for file_path, file_name in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
        
        return results

    async def check_tdata_accounts_pipeline(self, files: List[Tuple[str, str]], update_callback) -> Dict[str, List[Tuple[str, str, str]]]:
        """流水线：TData转换完成后立即并发检测（转换和检测同步推进，不等待全部转换完毕）"""
        results = {
            "无限制": [],
            "垃圾邮件": [],
            "冻结": [],
            "封禁": [],
            "连接错误": []
        }

        status_mapping = {
            "临时限制": "垃圾邮件",
            "等待验证": "封禁",
            "无响应": "连接错误",
        }

        total = len(files)
        start_time = time.time()
        last_update_time = 0

        convert_semaphore = asyncio.Semaphore(TDATA_PIPELINE_CONVERT_CONCURRENT)
        check_semaphore = asyncio.Semaphore(TDATA_PIPELINE_CHECK_CONCURRENT)

        # 收集所有检测任务，以便最终等待
        check_tasks: List[asyncio.Task] = []
        processed = 0      # 最终完成数（转换失败 + 检测完成）
        convert_done = 0   # 转换尝试完成数（用于转换阶段的进度显示）

        print(f"\n{'='*60}")
        print(f"🚀 [流水线] 启动：{total} 个TData，转换并发={TDATA_PIPELINE_CONVERT_CONCURRENT}，检测并发={TDATA_PIPELINE_CHECK_CONCURRENT}")
        print(f"{'='*60}")

        async def check_one(tdata_path: str, tdata_name: str, session_file: str):
            nonlocal processed, last_update_time
            async with check_semaphore:
                try:
                    status, info, account_name = await self.checker.check_account_status(session_file, tdata_name, self.db)
                    mapped_status = status_mapping.get(status, status)
                    if mapped_status not in results:
                        print(f"⚠️ [检测] 未知状态 '{mapped_status}'，归类为连接错误: {tdata_name}")
                        mapped_status = "连接错误"
                    results[mapped_status].append((tdata_path, tdata_name, info))
                    print(f"✅ [检测] [{tdata_name}] -> {mapped_status}")
                except Exception as e:
                    results["连接错误"].append((tdata_path, tdata_name, f"异常: {str(e)[:20]}"))
                    print(f"❌ [检测] [{tdata_name}] -> 异常: {str(e)[:50]}")

            processed += 1
            current_time = time.time()
            if update_callback and ((current_time - last_update_time >= 3) or (processed % 10 == 0) or (processed == total)):
                elapsed = current_time - start_time
                speed = processed / elapsed if elapsed > 0 else 0
                await update_callback(processed, total, results, speed, elapsed)
                last_update_time = current_time

            # 检测完成后立即清理该账号的临时session文件
            try:
                if os.path.exists(session_file):
                    os.remove(session_file)
                journal = session_file + "-journal"
                if os.path.exists(journal):
                    os.remove(journal)
            except Exception as e:
                logger.warning(f"清理临时Session文件失败: {e}")

        async def convert_one(index: int, tdata_path: str, tdata_name: str):
            nonlocal processed, last_update_time, convert_done
            session_file = None
            error = None
            async with convert_semaphore:
                if not OPENTELE_AVAILABLE:
                    error = "opentele库未安装，无法转换TData"
                else:
                    try:
                        tdesk = TDesktop(tdata_path)
                        if not tdesk.isLoaded():
                            error = "TData未授权或无效"
                        else:
                            os.makedirs(config.SESSIONS_BAK_DIR, exist_ok=True)
                            temp_session_name = f"tdata_pipe_{time.time_ns()}_{index}"
                            temp_session_path = os.path.join(config.SESSIONS_BAK_DIR, temp_session_name)
                            temp_client = await asyncio.wait_for(
                                tdesk.ToTelethon(session=temp_session_path, flag=UseCurrentSession, api=API.TelegramDesktop),
                                timeout=TDATA_PIPELINE_CONVERT_TIMEOUT
                            )
                            await temp_client.disconnect()
                            candidate = f"{temp_session_path}.session"
                            if os.path.exists(candidate):
                                session_file = candidate
                                print(f"✅ [转换] [{tdata_name}] 转换完成，立即提交检测")
                            else:
                                error = "Session转换失败：文件未生成"
                    except asyncio.TimeoutError:
                        error = f"TData转换超时（{TDATA_PIPELINE_CONVERT_TIMEOUT}秒）"
                        print(f"⏱️ [转换] [{tdata_name}] {error}")
                    except Exception as e:
                        error = f"TData转换失败: {str(e)[:50]}"
                        print(f"❌ [转换] [{tdata_name}] {error}")

            if session_file:
                # 转换成功：立即创建检测任务，不等待其他转换完成
                task = asyncio.create_task(check_one(tdata_path, tdata_name, session_file))
                check_tasks.append(task)
            else:
                # 转换失败：直接记录错误，并计入进度
                results["连接错误"].append((tdata_path, tdata_name, error or "TData转换失败"))
                processed += 1

            # 无论转换成功还是失败，都更新进度回调，让用户看到实时进展
            convert_done += 1
            current_time = time.time()
            if update_callback and ((current_time - last_update_time >= 3) or (convert_done % 10 == 0) or (convert_done == total)):
                elapsed = current_time - start_time
                speed = convert_done / elapsed if elapsed > 0 else 0
                await update_callback(convert_done, total, results, speed, elapsed)
                last_update_time = current_time

        # 并发执行所有转换任务；每个转换完成后立即异步提交检测任务
        await asyncio.gather(*[convert_one(i, fp, fn) for i, (fp, fn) in enumerate(files)], return_exceptions=True)

        # 等待所有已提交的检测任务完成
        if check_tasks:
            await asyncio.gather(*check_tasks, return_exceptions=True)

        print(f"\n{'='*60}")
        print(f"🏁 [流水线] 全部完成：{total} 个TData账号检测完毕")
        print(f"{'='*60}\n")

        return results

    async def check_tdata_structure_async(self, tdata_path: str, tdata_name: str) -> Tuple[str, str, str]:
        """异步TData检查（已废弃，保留向后兼容）"""
        try:
            d877_path = os.path.join(tdata_path, "D877F783D5D3EF8C")
            maps_path = os.path.join(d877_path, "maps")
            
            if not os.path.exists(maps_path):
                return "连接错误", "TData结构无效", tdata_name
            
            maps_size = os.path.getsize(maps_path)
            if maps_size < 30:
                return "连接错误", "TData数据不完整", tdata_name
            
            return "无限制", f"TData有效 | {maps_size}字节", tdata_name
            
        except Exception as e:
            return "连接错误", f"TData检查失败", tdata_name
    
    def translate_spambot_reply(self, text: str) -> str:
        """智能翻译SpamBot回复"""
        # 常见俄语到英语的翻译
        translations = {
            'ограничения': 'limitations',
            'ограничено': 'limited', 
            'заблокирован': 'blocked',
            'спам': 'spam',
            'нарушение': 'violation',
            'жалобы': 'complaints',
            'хорошие новости': 'good news',
            'нет ограничений': 'no limitations',
            'свободны': 'free'
        }
        
        result = text.lower()
        for ru, en in translations.items():
            result = result.replace(ru, en)
        
        return result
    
    def create_result_zips(self, results: Dict[str, List[Tuple[str, str, str]]], task_id: str, file_type: str) -> List[Tuple[str, str, int]]:
        """创建结果ZIP（修复版 - 解决目录重名问题并优化路径长度）"""
        result_files = []
        
        # 优化路径结构：使用短时间戳创建简洁的结果目录
        # 从 /www/sessionbot/results/task_5611529170/ 
        # 优化为 /www/sessionbot/results/conv_123456/
        timestamp_short = str(int(time.time()))[-6:]  # 只取后6位
        task_results_dir = os.path.join(config.RESULTS_DIR, f"conv_{timestamp_short}")
        os.makedirs(task_results_dir, exist_ok=True)
        
        print(f"📁 任务结果目录: {task_results_dir}")
        
        for status, files in results.items():
            if not files:
                continue
            
            print(f"📦 正在创建 {status} 结果文件，包含 {len(files)} 个账号")
            
            # 为每个状态创建唯一的临时目录（优化路径长度）
            # 使用短时间戳（只取后6位）+ status 以进一步缩短路径
            timestamp_short = str(int(time.time()))[-6:]
            status_temp_dir = os.path.join(task_results_dir, f"{status}_{timestamp_short}")
            os.makedirs(status_temp_dir, exist_ok=True)
            
            # 确保每个TData有唯一目录名
            used_names = set()
            
            try:
                for index, (file_path, file_name, info) in enumerate(files):
                    if file_type == "session":
                        # 复制session文件
                        dest_path = os.path.join(status_temp_dir, file_name)
                        shutil.copy2(file_path, dest_path)
                        print(f"📄 复制Session文件: {file_name}")
                        
                        # 查找对应的json文件
                        json_name = file_name.replace('.session', '.json')
                        json_path = os.path.join(os.path.dirname(file_path), json_name)
                        if os.path.exists(json_path):
                            json_dest = os.path.join(status_temp_dir, json_name)
                            shutil.copy2(json_path, json_dest)
                            print(f"📄 复制JSON文件: {json_name}")
                    
                    elif file_type == "tdata":
                        # 直接使用原始文件夹名称（通常是手机号）
                        original_name = file_name
                        
                        # 确保名称唯一性
                        unique_name = original_name
                        counter = 1
                        while unique_name in used_names:
                            unique_name = f"{original_name}_{counter}"
                            counter += 1
                        
                        used_names.add(unique_name)
                        
                        # 创建 +手机号/tdata/ 结构
                        phone_dir = os.path.join(status_temp_dir, unique_name)
                        target_dir = os.path.join(phone_dir, "tdata")
                        os.makedirs(target_dir, exist_ok=True)
                        
                        # 复制TData文件
                        if os.path.exists(file_path) and os.path.isdir(file_path):
                            for item in os.listdir(file_path):
                                item_path = os.path.join(file_path, item)
                                dest_path = os.path.join(target_dir, item)
                                if os.path.isdir(item_path):
                                    shutil.copytree(item_path, dest_path)
                                else:
                                    shutil.copy2(item_path, dest_path)
                            print(f"📂 复制TData: {unique_name}")
                
                # 创建ZIP文件
                zip_filename = f"{status}_{len(files)}个.zip"
                zip_path = os.path.join(task_results_dir, zip_filename)
                
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files_list in os.walk(status_temp_dir):
                        for file in files_list:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, status_temp_dir)
                            zipf.write(file_path, arcname)
                
                result_files.append((zip_path, status, len(files)))
                print(f"✅ 创建成功: {zip_filename}")
                
            except Exception as e:
                print(f"❌ 创建{status}结果文件失败: {e}")
            finally:
                # 清理临时状态目录
                if os.path.exists(status_temp_dir):
                    shutil.rmtree(status_temp_dir, ignore_errors=True)
        
        return result_files
