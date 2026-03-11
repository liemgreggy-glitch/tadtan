"""
models.dataclasses - Dataclass definitions for the Telegram account detection bot.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional

from core.constants import BEIJING_TZ


@dataclass
class CleanupAction:
    """清理操作记录"""
    chat_id: int
    title: str
    chat_type: str  # 'user', 'group', 'channel', 'bot'
    actions_done: List[str] = field(default_factory=list)
    status: str = 'pending'  # 'pending', 'success', 'partial', 'failed', 'skipped'
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(BEIJING_TZ).isoformat())


@dataclass
class ProfileUpdateConfig:
    """资料更新配置"""
    mode: str  # 'random' 或 'custom'

    # 姓名配置
    update_name: bool = True
    custom_names: List[str] = field(default_factory=list)  # 自定义姓名列表

    # 头像配置
    update_photo: bool = False
    photo_action: str = 'keep'  # 'keep', 'delete_all', 'custom'
    custom_photos: List[str] = field(default_factory=list)  # 自定义头像路径列表

    # 简介配置
    update_bio: bool = False
    bio_action: str = 'keep'  # 'keep', 'clear', 'random', 'custom'
    custom_bios: List[str] = field(default_factory=list)  # 自定义简介列表

    # 用户名配置
    update_username: bool = False
    username_action: str = 'keep'  # 'keep', 'delete', 'random', 'custom'
    custom_usernames: List[str] = field(default_factory=list)  # 自定义用户名列表


@dataclass
class ProxyUsageRecord:
    """代理使用记录"""
    account_name: str
    proxy_attempted: Optional[str]  # Format: "type host:port" or None for local
    attempt_result: str  # "success", "timeout", "connection_refused", "auth_failed", "dns_error", etc.
    fallback_used: bool  # True if fell back to local connection
    error: Optional[str]  # Error message if any
    is_residential: bool  # Whether it's a residential proxy
    elapsed: float  # Time elapsed in seconds


@dataclass
class BatchCreationConfig:
    """批量创建配置"""
    creation_type: str  # 'group' or 'channel'
    count_per_account: int  # 每个账号创建的数量
    admin_username: str = ""  # 管理员用户名（单个，向后兼容）
    admin_usernames: List[str] = field(default_factory=list)  # 管理员用户名列表（支持多个）
    group_names: List[str] = field(default_factory=list)  # 群组/频道名称列表
    group_descriptions: List[str] = field(default_factory=list)  # 群组/频道简介列表
    username_mode: str = "auto"  # 'auto' (自动生成), 'custom' (自定义)
    custom_usernames: List[str] = field(default_factory=list)  # 自定义用户名列表


@dataclass
class BatchCreationResult:
    """创建结果"""
    account_name: str
    phone: str
    creation_type: str  # 'group' or 'channel'
    name: str
    description: str = ""
    username: Optional[str] = None
    invite_link: Optional[str] = None
    status: str = 'pending'  # 'success', 'failed', 'skipped'
    error: Optional[str] = None
    creator_id: Optional[int] = None
    creator_username: Optional[str] = None
    admin_username: Optional[str] = None  # 向后兼容，保留单个
    admin_usernames: List[str] = field(default_factory=list)  # 成功添加的管理员列表
    admin_failures: List[str] = field(default_factory=list)  # 添加失败的管理员及原因
    created_at: str = field(default_factory=lambda: datetime.now(BEIJING_TZ).isoformat())


@dataclass
class BatchAccountInfo:
    """账号信息"""
    session_path: str
    file_name: str
    file_type: str  # 'session' or 'tdata'
    phone: Optional[str] = None
    is_valid: bool = False
    client: Optional[Any] = None
    daily_created: int = 0
    daily_remaining: int = 0
    validation_error: Optional[str] = None
    # 连接参数（用于在新事件循环中重新连接）
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    proxy_dict: Optional[Any] = None
    # TData转换后的Session路径（仅用于TData类型）
    converted_session_path: Optional[str] = None
