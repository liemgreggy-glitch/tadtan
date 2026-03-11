"""
managers.proxy_manager - Proxy management
"""
import logging
import os
import random
import re
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.config import Config
from core.constants import BEIJING_TZ

logger = logging.getLogger(__name__)

config = Config()


class ProxyManager:
    """代理管理器"""
    
    def __init__(self, proxy_file: str = "proxy.txt"):
        self.proxy_file = proxy_file
        self.proxies = []
        self.current_index = 0
        self.load_proxies()
    
    def is_proxy_mode_active(self, db: 'Database') -> bool:
        """判断代理模式是否真正启用（USE_PROXY=true 且存在有效代理 且数据库开关启用）"""
        try:
            proxy_enabled = db.get_proxy_enabled()
            has_valid_proxies = len(self.proxies) > 0
            return config.USE_PROXY and proxy_enabled and has_valid_proxies
        except:
            return config.USE_PROXY and len(self.proxies) > 0
    
    def get_proxy_activation_detail(self, db: 'Database') -> str:
        """获取代理模式激活状态的详细信息"""
        details = []
        details.append(f"ENV USE_PROXY: {config.USE_PROXY}")
        
        try:
            proxy_enabled = db.get_proxy_enabled()
            details.append(f"DB proxy_enabled: {proxy_enabled}")
        except Exception as e:
            details.append(f"DB proxy_enabled: error ({str(e)[:30]})")
        
        details.append(f"Valid proxies loaded: {len(self.proxies)}")
        details.append(f"Proxy mode active: {self.is_proxy_mode_active(db)}")
        
        return " | ".join(details)
    
    def load_proxies(self):
        """加载代理列表"""
        if not os.path.exists(self.proxy_file):
            print(f"⚠️ 代理文件不存在: {self.proxy_file}")
            print(f"💡 创建示例代理文件...")
            self.create_example_proxy_file()
            return
        
        try:
            with open(self.proxy_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            self.proxies = []
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    proxy_info = self.parse_proxy_line(line)
                    if proxy_info:
                        self.proxies.append(proxy_info)
            
            print(f"📡 加载了 {len(self.proxies)} 个代理")
            
        except Exception as e:
            print(f"❌ 加载代理文件失败: {e}")
    
    def create_example_proxy_file(self):
        """创建示例代理文件"""
        example_content = """# 代理文件示例 - proxy.txt
# 支持的格式：
# HTTP代理：ip:port 或 http://ip:port
# HTTP认证：ip:port:username:password 或 http://ip:port:username:password
# SOCKS5：socks5:ip:port:username:password 或 socks5://ip:port:username:password
# SOCKS4：socks4:ip:port 或 socks4://ip:port
# ABCProxy住宅代理：host:port:username:password 或 http://host:port:username:password

# 示例（请替换为真实代理）
# 127.0.0.1:8080
# http://127.0.0.1:8080
# 127.0.0.1:1080:user:pass
# socks5:127.0.0.1:1080:user:pass
# socks5://127.0.0.1:1080:user:pass
# socks4:127.0.0.1:1080

# ABCProxy住宅代理示例（两种格式都支持）：
# f01a4db3d3952561.abcproxy.vip:4950:FlBaKtPm7l-zone-abc:00937128
# http://f01a4db3d3952561.abcproxy.vip:4950:FlBaKtPm7l-zone-abc:00937128

# 注意：
# - 以#开头的行为注释行，会被忽略
# - 支持标准格式和URL格式（带 :// 的格式）
# - 住宅代理（如ABCProxy）会自动使用更长的超时时间（30秒）
# - 系统会自动检测住宅代理并优化连接参数
"""
        try:
            with open(self.proxy_file, 'w', encoding='utf-8') as f:
                f.write(example_content)
            print(f"✅ 已创建示例代理文件: {self.proxy_file}")
        except Exception as e:
            print(f"❌ 创建示例代理文件失败: {e}")
    
    def is_residential_proxy(self, host: str) -> bool:
        """检测是否为住宅代理"""
        host_lower = host.lower()
        for pattern in config.RESIDENTIAL_PROXY_PATTERNS:
            if pattern.strip().lower() in host_lower:
                return True
        return False
    
    def parse_proxy_line(self, line: str) -> Optional[Dict]:
        """解析代理行（支持ABCProxy等住宅代理格式）"""
        try:
            # 先处理URL格式的代理（如 http://host:port:user:pass 或 socks5://host:port）
            # 移除协议前缀（如果存在）
            original_line = line
            proxy_type = 'http'  # 默认类型
            
            # 检查并移除协议前缀
            if '://' in line:
                protocol, rest = line.split('://', 1)
                proxy_type = protocol.lower()
                line = rest  # 现在 line 是 host:port:user:pass 格式
            
            parts = line.split(':')
            
            if len(parts) == 2:
                # ip:port
                host = parts[0].strip()
                return {
                    'type': proxy_type,
                    'host': host,
                    'port': int(parts[1].strip()),
                    'username': None,
                    'password': None,
                    'is_residential': self.is_residential_proxy(host)
                }
            elif len(parts) == 4:
                # ip:port:username:password 或 ABCProxy格式
                # 例如: f01a4db3d3952561.abcproxy.vip:4950:FlBaKtPm7l-zone-abc:00937128
                host = parts[0].strip()
                return {
                    'type': proxy_type,
                    'host': host,
                    'port': int(parts[1].strip()),
                    'username': parts[2].strip(),
                    'password': parts[3].strip(),
                    'is_residential': self.is_residential_proxy(host)
                }
            elif len(parts) >= 3 and parts[0].lower() in ['socks5', 'socks4', 'http', 'https']:
                # 旧格式: socks5:ip:port or socks5:ip:port:username:password (无 ://)
                # 这种情况下 parts[0] 是协议类型
                proxy_type = parts[0].lower()
                host = parts[1].strip()
                port = int(parts[2].strip())
                username = parts[3].strip() if len(parts) > 3 else None
                password = parts[4].strip() if len(parts) > 4 else None
                
                return {
                    'type': proxy_type,
                    'host': host,
                    'port': port,
                    'username': username,
                    'password': password,
                    'is_residential': self.is_residential_proxy(host)
                }
        except Exception as e:
            print(f"❌ 解析代理行失败: {line} - {e}")
        
        return None
    
    def get_next_proxy(self) -> Optional[Dict]:
        """获取下一个代理"""
        if not self.proxies:
            return None
        
        proxy = self.proxies[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.proxies)
        return proxy
    
    def get_random_proxy(self) -> Optional[Dict]:
        """获取随机代理"""
        if not self.proxies:
            return None
        return random.choice(self.proxies)
    
    def remove_proxy(self, proxy_to_remove: Dict):
        """从内存中移除代理"""
        self.proxies = [p for p in self.proxies if not (
            p['host'] == proxy_to_remove['host'] and p['port'] == proxy_to_remove['port']
        )]
    
    def backup_proxy_file(self) -> bool:
        """备份原始代理文件"""
        try:
            if os.path.exists(self.proxy_file):
                backup_file = self.proxy_file.replace('.txt', '_backup.txt')
                shutil.copy2(self.proxy_file, backup_file)
                print(f"✅ 代理文件已备份到: {backup_file}")
                return True
        except Exception as e:
            print(f"❌ 备份代理文件失败: {e}")
        return False
    
    def save_working_proxies(self, working_proxies: List[Dict]):
        """保存可用代理到新文件"""
        try:
            working_file = self.proxy_file.replace('.txt', '_working.txt')
            with open(working_file, 'w', encoding='utf-8') as f:
                f.write("# 可用代理文件 - 自动生成\n")
                f.write(f"# 生成时间: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S CST')}\n")
                f.write(f"# 总数: {len(working_proxies)}个\n\n")
                
                for proxy in working_proxies:
                    if proxy['username'] and proxy['password']:
                        if proxy['type'] == 'http':
                            line = f"{proxy['host']}:{proxy['port']}:{proxy['username']}:{proxy['password']}\n"
                        else:
                            line = f"{proxy['type']}:{proxy['host']}:{proxy['port']}:{proxy['username']}:{proxy['password']}\n"
                    else:
                        if proxy['type'] == 'http':
                            line = f"{proxy['host']}:{proxy['port']}\n"
                        else:
                            line = f"{proxy['type']}:{proxy['host']}:{proxy['port']}\n"
                    f.write(line)
            
            print(f"✅ 可用代理已保存到: {working_file}")
            return working_file
        except Exception as e:
            print(f"❌ 保存可用代理失败: {e}")
            return None
    
    def save_failed_proxies(self, failed_proxies: List[Dict]):
        """保存失效代理到备份文件"""
        try:
            failed_file = self.proxy_file.replace('.txt', '_failed.txt')
            with open(failed_file, 'w', encoding='utf-8') as f:
                f.write("# 失效代理文件 - 自动生成\n")
                f.write(f"# 生成时间: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S CST')}\n")
                f.write(f"# 总数: {len(failed_proxies)}个\n\n")
                
                for proxy in failed_proxies:
                    if proxy['username'] and proxy['password']:
                        if proxy['type'] == 'http':
                            line = f"{proxy['host']}:{proxy['port']}:{proxy['username']}:{proxy['password']}\n"
                        else:
                            line = f"{proxy['type']}:{proxy['host']}:{proxy['port']}:{proxy['username']}:{proxy['password']}\n"
                    else:
                        if proxy['type'] == 'http':
                            line = f"{proxy['host']}:{proxy['port']}\n"
                        else:
                            line = f"{proxy['type']}:{proxy['host']}:{proxy['port']}\n"
                    f.write(line)
            
            print(f"✅ 失效代理已保存到: {failed_file}")
            return failed_file
        except Exception as e:
            print(f"❌ 保存失效代理失败: {e}")
            return None
