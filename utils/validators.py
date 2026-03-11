"""
utils.validators - Validation utility functions
"""
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def detect_tdata_structure(account_path: str) -> Optional[Tuple]:
    """检测 TData 目录结构类型
    
    Args:
        account_path: 账号目录路径
        
    Returns:
        ('type1', tdata_path) - key_datas在tdata目录内
        ('type2', tdata_path, key_datas_path) - key_datas与tdata同级
        None - 未找到有效的TData结构
    """
    tdata_path = os.path.join(account_path, 'tdata')
    
    # 方式1: key_datas 在 tdata 目录内
    key_in_tdata = os.path.join(tdata_path, 'key_datas')
    if os.path.exists(key_in_tdata):
        logger.info(f"检测到TData结构类型1: key_datas在tdata内 - {account_path}")
        return ('type1', tdata_path)
    
    # 方式2: key_datas 与 tdata 同级
    key_beside_tdata = os.path.join(account_path, 'key_datas')
    if os.path.exists(key_beside_tdata) and os.path.exists(tdata_path):
        logger.info(f"检测到TData结构类型2: key_datas与tdata同级 - {account_path}")
        return ('type2', tdata_path, key_beside_tdata)
    
    logger.warning(f"未找到有效的TData结构 - {account_path}")
    return None


def is_valid_tdata(tdata_path: str) -> bool:
    """
    检查 tdata 目录是否有效
    
    有效的 tdata 目录应该包含:
    - 一个类似 D877F783D5D3EF8C 的子目录
    - key_datas 或 key_data 文件可以在：
      1. D877F783D5D3EF8C 子目录内（标准结构）
      2. 与 D877F783D5D3EF8C 同级（变体结构）
    
    Args:
        tdata_path: tdata 目录路径
        
    Returns:
        bool: 是否为有效的 tdata 目录
    """
    if not os.path.isdir(tdata_path):
        return False
    
    try:
        has_d877_dir = False
        has_key_file = False
        
        for item in os.listdir(tdata_path):
            item_path = os.path.join(tdata_path, item)
            
            # 检查是否有 D877 开头的目录
            if os.path.isdir(item_path) and item.startswith('D877'):
                has_d877_dir = True
                
                # 检查 D877 目录内是否有 key_datas 或 key_data（标准结构）
                key_datas_path = os.path.join(item_path, 'key_datas')
                key_data_path = os.path.join(item_path, 'key_data')
                if os.path.exists(key_datas_path) or os.path.exists(key_data_path):
                    return True
            
            # 检查与 D877 同级的 key_datas 或 key_data 文件（变体结构）
            if item in ('key_datas', 'key_data') and os.path.isfile(item_path):
                has_key_file = True
        
        # 如果有 D877 目录且有同级的 key 文件，也认为是有效的
        if has_d877_dir and has_key_file:
            return True
            
    except (OSError, PermissionError) as e:
        logger.warning(f"检查tdata目录失败 {tdata_path}: {e}")
        return False
    
    return False
