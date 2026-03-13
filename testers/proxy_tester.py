Copilot said: 好的！直接给你完整的 `testers/proxy_tester.py` 文件： ```bash cd /www/sessionbot/sessiontdata cat > testers/proxy_tester.py <<
好的！直接给你完整的 testers/proxy_tester.py 文件：

bash
cd /www/sessionbot/sessiontdata
cat > testers/proxy_tester.py << 'EOF'
"""
testers.proxy_tester - Proxy testing and rotation module
"""
import asyncio
import logging
import random
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from telethon import TelegramClient
    from telethon.errors import (
        AuthKeyUnregisteredError,
        PhoneNumberBannedError,
        UserDeactivatedBanError,
        FloodWaitError
    )
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False

try:
    import socks
    PROXY_SUPPORT = True
except ImportError:
    PROXY_SUPPORT = False

from core.config import Config

config = Config()


class ProxyTester:
    """代理测试器"""
    
    def __init__(self, proxy_list: List[str]):
        self.proxy_list = proxy_list
        self.working_proxies = []
        self.failed_proxies = []
    
    async def test_proxy(self, proxy_string: str) -> bool:
        """测试单个代理"""
        try:
            proxy_dict = self.parse_proxy(proxy_string)
            if not proxy_dict:
                return False
            
            # 简单测试：尝试连接
            client = TelegramClient(
                'test_session',
                config.API_ID,
                config.API_HASH,
                proxy=proxy_dict
            )
            
            await client.connect()
            await client.disconnect()
            
            self.working_proxies.append(proxy_string)
            return True
            
        except Exception as e:
            logger.debug(f"代理测试失败 {proxy_string}: {e}")
            self.failed_proxies.append(proxy_string)
            return False
    
    def parse_proxy(self, proxy_string: str) -> Optional[Dict]:
        """解析代理字符串"""
        try:
            if not proxy_string or proxy_string.strip() == '':
                return None
            
            parts = proxy_string.strip().split(':')
            if len(parts) < 3:
                return None
            
            proxy_type = parts[0].lower()
            addr = parts[1]
            port = int(parts[2])
            
            proxy_dict = {
                'proxy_type': proxy_type,
                'addr': addr,
                'port': port
            }
            
            if len(parts) >= 4:
                proxy_dict['username'] = parts[3]
            if len(parts) >= 5:
                proxy_dict['password'] = parts[4]
            
            return proxy_dict
            
        except Exception as e:
            logger.error(f"解析代理失败 {proxy_string}: {e}")
            return None
    
    async def test_all(self) -> Tuple[List[str], List[str]]:
        """测试所有代理"""
        tasks = [self.test_proxy(proxy) for proxy in self.proxy_list]
        await asyncio.gather(*tasks, return_exceptions=True)
        return self.working_proxies, self.failed_proxies


class ProxyRotator:
    """代理轮换器"""
    
    def __init__(self, proxy_list: List[str]):
        self.proxy_list = proxy_list
        self.current_index = 0
        self.proxy_usage = {}
    
    def get_next_proxy(self) -> Optional[str]:
        """获取下一个代理"""
        if not self.proxy_list:
            return None
        
        proxy = self.proxy_list[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.proxy_list)
        
        # 记录使用次数
        self.proxy_usage[proxy] = self.proxy_usage.get(proxy, 0) + 1
        
        return proxy
    
    def get_random_proxy(self) -> Optional[str]:
        """获取随机代理"""
        if not self.proxy_list:
            return None
        
        proxy = random.choice(self.proxy_list)
        self.proxy_usage[proxy] = self.proxy_usage.get(proxy, 0) + 1
        
        return proxy
    
    def remove_proxy(self, proxy: str):
        """移除失效的代理"""
        if proxy in self.proxy_list:
            self.proxy_list.remove(proxy)
            logger.info(f"已移除失效代理: {proxy}")
    
    def get_usage_stats(self) -> Dict[str, int]:
        """获取代理使用统计"""
        return self.proxy_usage
