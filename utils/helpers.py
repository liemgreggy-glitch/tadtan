"""
utils.helpers - General-purpose helper functions
"""
import logging
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

try:
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

try:
    from i18n import get_text as t
    I18N_AVAILABLE = True
except ImportError:
    I18N_AVAILABLE = False
    def t(user_id, key):
        return key

from utils.validators import is_valid_tdata

logger = logging.getLogger(__name__)


def generate_progress_bar(current: int, total: int, width: int = 20) -> str:
    """生成文本进度条
    
    Args:
        current: 当前进度
        total: 总数
        width: 进度条宽度（字符数）
        
    Returns:
        格式化的进度条字符串
    """
    if total == 0:
        return "░" * width + " 0.0%"
    
    # 输入验证
    if current < 0:
        current = 0
    
    percentage = current / total
    filled = int(width * percentage)
    empty = width - filled
    
    bar = "▓" * filled + "░" * empty
    percent_text = f"{percentage * 100:.1f}%"
    
    return f"{bar} {percent_text}"


def format_time(seconds: float) -> str:
    """格式化时间显示
    
    Args:
        seconds: 秒数
        
    Returns:
        格式化的时间字符串 (HH:MM:SS 或 MM:SS)
    """
    if seconds < 0:
        return "00:00"
    
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"


def get_back_to_menu_keyboard(user_id: int = None):
    """返回主菜单按钮
    
    Args:
        user_id: User ID for language selection (optional)
    
    Returns:
        InlineKeyboardMarkup: 包含"返回主菜单"按钮的键盘布局
    """
    if user_id:
        button_text = t(user_id, 'btn_back_to_menu')
    else:
        button_text = "返回主菜单"  # Fallback to Chinese
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(button_text, callback_data="back_to_main")]
    ])


def extract_phone_from_path(path: str) -> Optional[str]:
    """从路径中提取手机号
    
    Args:
        path: 文件或目录路径
        
    Returns:
        提取的手机号，如果未找到则返回None
    """
    basename = os.path.basename(path.rstrip('/\\'))
    # 移除扩展名
    name = os.path.splitext(basename)[0]
    # 提取数字（手机号通常10-15位，使用单词边界确保匹配完整数字）
    match = re.search(r'\b\d{10,15}\b', name)
    return match.group() if match else None


def extract_phone_from_tdata_path(tdata_path: str) -> Optional[str]:
    """从 TData 路径提取手机号
    
    支持的路径结构：
    - /tmp/xxx/+8613812345678/tdata/D877F783D5D3EF8C
    - /tmp/xxx/+8613812345678/tdata
    - /tmp/xxx/8613812345678/tdata/D877F783D5D3EF8C
    
    Args:
        tdata_path: TData 路径（可能是 tdata 目录或其子目录）
        
    Returns:
        手机号（带+前缀），如果未找到则返回None
    """
    try:
        # 标准化路径分隔符
        path_parts = tdata_path.replace('\\', '/').split('/')
        
        # 从路径各部分查找手机号
        for part in path_parts:
            if not part:
                continue
            
            # 查找以 + 开头的文件夹名（手机号）
            if part.startswith('+') and len(part) > 5:
                # 验证去掉+后是否全是数字
                phone_digits = part[1:]
                if phone_digits.isdigit() and len(phone_digits) >= 10:
                    return part
            
            # 也支持纯数字格式（不带+）
            if part.isdigit() and len(part) >= 10:
                return '+' + part
        
        return None
    except Exception as e:
        logger.warning(f"从TData路径提取手机号失败: {e}")
        return None


def scan_tdata_accounts(base_path: str) -> list:
    """
    统一的 tdata 账号扫描函数
    
    灵活识别：只要手机号文件夹内包含有效的 tdata 相关文件即可识别
    支持多种路径结构：
    - ✅ +8613812345678/tdata/D877F783D5D3EF8C/key_datas (标准结构)
    - ✅ 79001234567/D877F783D5D3EF8C/key_datas (无tdata子目录)
    - ✅ 79001234567/其他子目录/tdata/D877F783D5D3EF8C/key_datas (深层嵌套)
    - ✅ 79001234567/key_datas (直接在根目录)
    
    关键要求：必须以手机号文件夹为根，不识别无手机号文件夹的账号
    
    以手机号文件夹为单位识别账号，每个手机号=一个账号
    
    Args:
        base_path: 解压后的根目录
        
    Returns:
        账号列表，每个账号包含:
        - phone: 手机号（文件夹名）
        - tdata_path: tdata 或账号根目录路径
        - account_path: 账号根目录（手机号文件夹）
    """
    accounts = []
    seen_phones = set()  # 用于去重
    
    def is_likely_phone_number(folder_name: str) -> bool:
        """检查文件夹名是否像手机号"""
        # 移除可能的+前缀
        clean_name = folder_name.lstrip('+')
        # 手机号通常是10-15位数字
        return clean_name.isdigit() and 10 <= len(clean_name) <= 15
    
    def has_tdata_files(dir_path: str) -> bool:
        """检查目录树中是否包含 tdata 相关文件（key_datas, key_data, D877F783D5D3EF8C等）"""
        try:
            for root, dirs, files in os.walk(dir_path):
                # 检查是否有 key_datas 或 key_data 文件
                if 'key_datas' in files or 'key_data' in files:
                    return True
                # 检查是否有 D877F783D5D3EF8C 目录
                for d in dirs:
                    if d.startswith('D877'):
                        # 检查 D877 目录下是否有 key_datas 或 key_data
                        d877_path = os.path.join(root, d)
                        if os.path.exists(os.path.join(d877_path, 'key_datas')) or \
                           os.path.exists(os.path.join(d877_path, 'key_data')):
                            return True
        except (OSError, PermissionError) as e:
            logger.warning(f"检查tdata文件失败 {dir_path}: {e}")
        return False
    
    def find_tdata_path(account_path: str) -> str:
        """在账号目录中查找 tdata 路径，优先返回标准 tdata 子目录，否则返回账号根目录"""
        # 优先查找标准的 tdata 子目录
        tdata_path = os.path.join(account_path, 'tdata')
        if os.path.isdir(tdata_path) and is_valid_tdata(tdata_path):
            return tdata_path
        
        # 如果没有标准 tdata 子目录，但账号目录包含 tdata 文件，返回账号根目录
        if has_tdata_files(account_path):
            return account_path
        
        return None
    
    def scan_directory(dir_path):
        """递归扫描目录"""
        if not os.path.isdir(dir_path):
            return
        
        try:
            for item in os.listdir(dir_path):
                item_path = os.path.join(dir_path, item)
                
                if not os.path.isdir(item_path):
                    continue
                
                # 检查文件夹名是否像手机号
                if is_likely_phone_number(item):
                    # 查找 tdata 路径
                    tdata_path = find_tdata_path(item_path)
                    if tdata_path:
                        phone = item  # 文件夹名就是手机号
                        
                        # 去重：同一个手机号只添加一次
                        if phone not in seen_phones:
                            seen_phones.add(phone)
                            accounts.append({
                                'phone': phone,
                                'tdata_path': tdata_path,
                                'account_path': item_path
                            })
                            logger.info(f"找到账号: {phone} -> {tdata_path}")
                    else:
                        # 虽然文件夹名像手机号，但不包含 tdata 文件，继续递归扫描
                        scan_directory(item_path)
                else:
                    # 不像手机号的文件夹，递归扫描子目录
                    scan_directory(item_path)
        except (OSError, PermissionError) as e:
            logger.warning(f"扫描目录失败 {dir_path}: {e}")
    
    scan_directory(base_path)
    logger.info(f"扫描完成: 共找到 {len(accounts)} 个唯一账号")
    return accounts


def copy_session_to_temp(session_path: str) -> Tuple[str, str]:
    """复制session文件到临时目录避免并发冲突
    
    Args:
        session_path: 原始session文件路径
        
    Returns:
        (temp_session_base, temp_dir): 临时session路径（不含.session后缀）和临时目录路径
        注意：返回的路径不包含.session后缀，与TelegramClient的使用方式一致
    """
    # 创建临时目录
    temp_dir = tempfile.mkdtemp(prefix="session_temp_")
    
    # 生成唯一的session文件名（已包含.session后缀）
    temp_session_name = f"{uuid.uuid4().hex}.session"
    temp_session_path = os.path.join(temp_dir, temp_session_name)
    
    # 移除.session后缀（如果存在）因为我们需要复制所有相关文件
    # 使用rsplit来处理边缘情况
    if session_path.endswith('.session'):
        session_base = session_path.rsplit('.session', 1)[0]
    else:
        session_base = session_path
    
    # temp_session_path 一定以 .session 结尾（见1089行），所以直接移除
    temp_session_base = temp_session_path[:-8]  # 移除 '.session' (8个字符)
    
    try:
        # 复制主session文件
        if os.path.exists(f"{session_base}.session"):
            shutil.copy2(f"{session_base}.session", f"{temp_session_base}.session")
        
        # 复制journal文件（如果存在）
        if os.path.exists(f"{session_base}.session-journal"):
            shutil.copy2(f"{session_base}.session-journal", f"{temp_session_base}.session-journal")
        
        # 返回临时session路径（不含.session后缀）
        return temp_session_base, temp_dir
    except (OSError, IOError) as e:
        logger.error(f"复制session文件失败: {e}")
        # 如果复制失败，清理临时目录并返回原始路径
        shutil.rmtree(temp_dir, ignore_errors=True)
        return session_base, None
    except Exception as e:
        # 记录意外错误并重新抛出
        logger.error(f"复制session文件时发生意外错误: {e}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def cleanup_temp_session(temp_dir: Optional[str]):
    """清理临时session文件
    
    Args:
        temp_dir: 临时目录路径
    """
    if temp_dir and os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.debug(f"已清理临时目录: {temp_dir}")
        except (OSError, IOError, PermissionError) as e:
            logger.warning(f"清理临时目录失败: {e}")


def process_accounts_with_dedup(accounts: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """处理账号列表并去重
    
    Args:
        accounts: 账号列表 [(账号名, 路径), ...]
        
    Returns:
        去重后的账号列表
    """
    processed_phones = set()
    unique_accounts = []
    
    for account_name, account_path in accounts:
        phone = extract_phone_from_path(account_path)
        if phone and phone not in processed_phones:
            processed_phones.add(phone)
            unique_accounts.append((account_name, account_path))
            logger.info(f"添加账号: {phone}")
        else:
            logger.info(f"跳过重复手机号: {phone or account_name}")
    
    logger.info(f"去重完成: 原始 {len(accounts)} 个，去重后 {len(unique_accounts)} 个")
    return unique_accounts


def deduplicate_accounts_by_phone(accounts: List[Dict]) -> List[Dict]:
    """按手机号去重账号列表
    
    Args:
        accounts: 账号字典列表，每个字典包含 phone, session_path, original_path, format 等字段
        
    Returns:
        去重后的账号列表
    """
    seen_phones = set()
    unique_accounts = []
    
    for account in accounts:
        phone = account.get('phone')
        if phone and phone not in seen_phones:
            seen_phones.add(phone)
            unique_accounts.append(account)
        else:
            logger.warning(f"⚠️ 重复账号已跳过: {phone}")
    
    logger.info(f"去重完成: 原始 {len(accounts)} 个，去重后 {len(unique_accounts)} 个")
    return unique_accounts


def create_zip_with_unique_paths(accounts: List[Tuple[str, str]], output_path: str) -> bool:
    """创建ZIP，使用手机号作为前缀避免重名
    
    Args:
        accounts: 账号列表 [(账号名, 路径), ...]
        output_path: 输出ZIP文件路径
        
    Returns:
        是否成功
    """
    try:
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            added_paths = set()
            
            for account_name, account_path in accounts:
                phone = extract_phone_from_path(account_path) or account_name
                
                if os.path.isdir(account_path):
                    # 目录：遍历所有文件
                    for root, dirs, files in os.walk(account_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            # 使用手机号作为前缀，确保唯一
                            # 计算相对于账号目录的路径
                            rel_path = os.path.relpath(file_path, account_path)
                            arc_name = f"{phone}/{rel_path}"
                            
                            if arc_name not in added_paths:
                                added_paths.add(arc_name)
                                zf.write(file_path, arc_name)
                                logger.debug(f"添加文件到ZIP: {arc_name}")
                else:
                    # 单文件
                    filename = os.path.basename(account_path)
                    arc_name = f"{phone}/{filename}"
                    
                    if arc_name not in added_paths:
                        added_paths.add(arc_name)
                        zf.write(account_path, arc_name)
                        logger.debug(f"添加文件到ZIP: {arc_name}")
        
        logger.info(f"ZIP创建成功: {output_path}，共 {len(added_paths)} 个文件")
        return True
    except Exception as e:
        logger.error(f"创建ZIP失败: {e}")
        return False


# ================================
# Helper functions extracted from tdata.py
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

class ProxyRotator:
    """代理轮换器 - 用于2FA重置防封"""
    def __init__(self, proxies: list):
        self.proxies = proxies
        self.index = 0
        self.lock = None  # 将在异步环境中初始化
    
    def get_next_proxy(self):
        """获取下一个代理，用完后循环复用（线程安全）"""
        if not self.proxies:
            return None
        
        proxy = self.proxies[self.index]
        self.index = (self.index + 1) % len(self.proxies)
        return proxy


def utc_to_beijing(utc_time):
    """将 UTC 时间转换为北京时间 (UTC+8)"""
    if utc_time is None:
        return "N/A"
    
    # 如果是字符串，先转换为 datetime
    if isinstance(utc_time, str):
        utc_time = datetime.fromisoformat(utc_time.replace('Z', '+00:00'))
    
    # 如果没有时区信息，假设是 UTC
    if utc_time.tzinfo is None:
        utc_time = utc_time.replace(tzinfo=timezone.utc)
    
    # 转换为北京时间 (UTC+8)
    beijing_tz = timezone(timedelta(hours=8))
    beijing_time = utc_time.astimezone(beijing_tz)
    
    return beijing_time.strftime('%Y-%m-%d %H:%M:%S')


