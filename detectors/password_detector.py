"""
detectors.password_detector - Password detection for session files
"""
import json
import logging
import os
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PasswordDetector:
    """密码自动检测器 - 支持TData和Session格式"""
    
    def __init__(self):
        # TData格式的密码文件关键词（优先级从高到低）
        # 使用关键词匹配，支持任意大小写组合
        self.tdata_password_keywords = [
            '2fa',      # 匹配 2fa.txt, 2FA.txt, 2Fa.TXT 等
            'twofa',    # 匹配 twofa.txt, TwoFA.txt, TWOFA.TXT 等
            'password', # 匹配 password.txt, Password.txt, PASSWORD.TXT 等
            '两步验证',  # 匹配中文文件名
            '密码',      # 匹配中文 密码.txt
            'pass',     # 匹配 pass.txt 等简写
        ]
        # Session JSON中的密码字段名
        self.session_password_fields = ['twoFA', '2fa', 'password', 'two_fa', 'twofa']
    
    def detect_tdata_password(self, tdata_path: str) -> Optional[str]:
        """
        检测TData格式的密码
        
        Args:
            tdata_path: TData 目录路径或包含 tdata 的父目录
            
        Returns:
            检测到的密码，如果未找到则返回 None
        """
        try:
            # 可能的搜索路径
            search_paths = []
            
            # 情况1: tdata_path 本身就是 tdata 目录
            if os.path.basename(tdata_path).lower() == 'tdata':
                search_paths.append(tdata_path)
                search_paths.append(os.path.dirname(tdata_path))  # 父目录
                logger.debug(f"TData目录检测: {tdata_path} 本身是tdata目录")
            # 情况2: tdata_path 是包含 tdata 的父目录
            elif os.path.isdir(os.path.join(tdata_path, 'tdata')):
                search_paths.append(os.path.join(tdata_path, 'tdata'))
                search_paths.append(tdata_path)
                logger.debug(f"TData目录检测: {tdata_path} 包含tdata子目录")
            # 情况3: tdata_path 是其他目录（可能是D877目录或账号根目录）
            else:
                search_paths.append(tdata_path)
                parent_dir = os.path.dirname(tdata_path)
                if parent_dir:
                    search_paths.append(parent_dir)
                    # 也检查父目录的父目录（处理深层嵌套）
                    grandparent_dir = os.path.dirname(parent_dir)
                    if grandparent_dir:
                        search_paths.append(grandparent_dir)
                logger.debug(f"TData目录检测: {tdata_path} 是其他目录，搜索多级父目录")
            
            logger.info(f"开始在 {len(search_paths)} 个路径中搜索密码文件")
            logger.debug(f"搜索路径: {search_paths}")
            
            # 在所有可能的路径中搜索密码文件
            for search_path in search_paths:
                if not os.path.isdir(search_path):
                    logger.debug(f"跳过非目录路径: {search_path}")
                    continue
                
                logger.debug(f"搜索目录: {search_path}")
                logger.debug(f"目录内容: {os.listdir(search_path) if os.path.isdir(search_path) else '无法列出'}")
                
                # 获取目录中的所有文件（不区分大小写匹配）
                try:
                    files_in_dir = os.listdir(search_path)
                except Exception as e:
                    logger.warning(f"无法列出目录 {search_path}: {e}")
                    continue
                
                # 按关键词优先级匹配文件
                for keyword in self.tdata_password_keywords:
                    # 在目录中查找包含关键词的文件（不区分大小写）
                    for actual_file in files_in_dir:
                        # 检查文件名（不含扩展名）是否包含关键词
                        file_lower = actual_file.lower()
                        keyword_lower = keyword.lower()
                        
                        # 匹配条件：文件名包含关键词，且是文本文件
                        if keyword_lower in file_lower and actual_file.lower().endswith('.txt'):
                            password_file = os.path.join(search_path, actual_file)
                            
                            if os.path.isfile(password_file):
                                logger.info(f"找到密码文件: {password_file} (匹配关键词: {keyword})")
                                try:
                                    # 先尝试UTF-8编码
                                    with open(password_file, 'r', encoding='utf-8') as f:
                                        password = f.read().strip()
                                        if password:  # 确保不是空文件
                                            logger.info(f"从 {password_file} 检测到密码 (UTF-8编码)")
                                            return password
                                        else:
                                            logger.warning(f"密码文件为空: {password_file}")
                                            # 继续查找其他文件
                                except UnicodeDecodeError:
                                    # 尝试GBK编码
                                    try:
                                        with open(password_file, 'r', encoding='gbk') as f:
                                            password = f.read().strip()
                                            if password:
                                                logger.info(f"从 {password_file} 检测到密码 (GBK编码)")
                                                return password
                                            else:
                                                logger.warning(f"密码文件为空: {password_file}")
                                    except Exception as e:
                                        logger.warning(f"读取密码文件失败 {password_file} (GBK): {e}")
                                        continue
                                except Exception as e:
                                    logger.warning(f"读取密码文件失败 {password_file}: {e}")
                                    continue
            
            logger.debug(f"未在 TData 目录中找到密码文件: {tdata_path}")
            logger.debug(f"搜索的关键词: {self.tdata_password_keywords}")
            return None
            
        except Exception as e:
            logger.error(f"检测TData密码时出错: {e}")
            return None
    
    def detect_session_password(self, json_path: str) -> Optional[str]:
        """
        检测Session JSON中的密码
        
        Args:
            json_path: JSON配置文件路径
            
        Returns:
            检测到的密码，如果未找到则返回None
        """
        try:
            if not os.path.exists(json_path):
                print(f"ℹ️ JSON文件不存在: {json_path}")
                return None
            
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 搜索密码字段
            for field_name in self.session_password_fields:
                if field_name in data:
                    password = data[field_name]
                    if password and isinstance(password, str) and password.strip():
                        # Security: Don't log actual password, only field name
                        print(f"✅ 在JSON中检测到密码字段: {field_name}")
                        return password.strip()
            
            print(f"ℹ️ 未在JSON中找到密码字段")
            return None
            
        except json.JSONDecodeError as e:
            print(f"❌ JSON解析失败: {e}")
            return None
        except Exception as e:
            print(f"❌ Session密码检测失败: {e}")
            return None
    
    def detect_password(self, file_path: str, file_type: str) -> Optional[str]:
        """
        自动检测密码（根据文件类型）
        
        Args:
            file_path: 文件路径（TData目录或Session文件）
            file_type: 文件类型（'tdata' 或 'session'）
            
        Returns:
            检测到的密码，如果未找到则返回None
        """
        if file_type == 'tdata':
            return self.detect_tdata_password(file_path)
        elif file_type == 'session':
            # 对于session文件，尝试查找对应的JSON文件
            json_path = file_path.replace('.session', '.json')
            return self.detect_session_password(json_path)
        else:
            print(f"❌ 不支持的文件类型: {file_type}")
            return None
