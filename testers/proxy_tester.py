"""
testers.proxy_tester - Proxy testing and rotation utilities
"""
import asyncio
import logging
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.constants import BEIJING_TZ

logger = logging.getLogger(__name__)

try:
    import socks
    SOCKS_AVAILABLE = True
except ImportError:
    SOCKS_AVAILABLE = False

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

try:
    import tdata as _tdata_module
    config = _tdata_module.config
except ImportError:
    from core.config import Config
    config = Config()


class ProxyTester:
    """代理测试器 - 快速验证和清理代理"""
    
    def __init__(self, proxy_manager):
        self.proxy_manager = proxy_manager
        self.test_url = "http://httpbin.org/ip"
        self.test_timeout = config.PROXY_CHECK_TIMEOUT
        self.max_concurrent = config.PROXY_CHECK_CONCURRENT
        
    async def test_proxy_connection(self, proxy_info: Dict) -> Tuple[bool, str, float]:
        """测试单个代理连接（支持住宅代理更长超时）"""
        start_time = time.time()
        
        # 住宅代理使用更长的超时时间
        is_residential = proxy_info.get('is_residential', False)
        test_timeout = config.RESIDENTIAL_PROXY_TIMEOUT if is_residential else self.test_timeout
        
        try:
            import aiohttp
            import aiosocks
            
            connector = None
            
            # 根据代理类型创建连接器
            if proxy_info['type'] == 'socks5':
                connector = aiosocks.SocksConnector.from_url(
                    f"socks5://{proxy_info['username']}:{proxy_info['password']}@{proxy_info['host']}:{proxy_info['port']}"
                    if proxy_info.get('username') and proxy_info.get('password')
                    else f"socks5://{proxy_info['host']}:{proxy_info['port']}"
                )
            elif proxy_info['type'] == 'socks4':
                connector = aiosocks.SocksConnector.from_url(
                    f"socks4://{proxy_info['host']}:{proxy_info['port']}"
                )
            else:  # HTTP代理
                proxy_url = f"http://{proxy_info['username']}:{proxy_info['password']}@{proxy_info['host']}:{proxy_info['port']}" \
                    if proxy_info.get('username') and proxy_info.get('password') \
                    else f"http://{proxy_info['host']}:{proxy_info['port']}"
                
                connector = aiohttp.TCPConnector()
            
            timeout = aiohttp.ClientTimeout(total=test_timeout)
            
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout
            ) as session:
                if proxy_info['type'] in ['socks4', 'socks5']:
                    async with session.get(self.test_url) as response:
                        if response.status == 200:
                            elapsed = time.time() - start_time
                            proxy_type = "住宅代理" if is_residential else "代理"
                            return True, f"{proxy_type}连接成功 {elapsed:.2f}s", elapsed
                else:
                    # HTTP代理
                    proxy_url = f"http://{proxy_info['username']}:{proxy_info['password']}@{proxy_info['host']}:{proxy_info['port']}" \
                        if proxy_info.get('username') and proxy_info.get('password') \
                        else f"http://{proxy_info['host']}:{proxy_info['port']}"
                    
                    async with session.get(self.test_url, proxy=proxy_url) as response:
                        if response.status == 200:
                            elapsed = time.time() - start_time
                            proxy_type = "住宅代理" if is_residential else "代理"
                            return True, f"{proxy_type}连接成功 {elapsed:.2f}s", elapsed
                            
        except ImportError:
            # 如果没有aiohttp和aiosocks，使用基础方法
            return await self.basic_test_proxy(proxy_info, start_time, is_residential)
        except Exception as e:
            elapsed = time.time() - start_time
            error_msg = str(e)
            if "timeout" in error_msg.lower():
                return False, f"连接超时 {elapsed:.2f}s", elapsed
            elif "connection" in error_msg.lower():
                return False, f"连接失败 {elapsed:.2f}s", elapsed
            else:
                return False, f"错误: {error_msg[:20]} {elapsed:.2f}s", elapsed
        
        elapsed = time.time() - start_time
        return False, f"未知错误 {elapsed:.2f}s", elapsed
    
    async def basic_test_proxy(self, proxy_info: Dict, start_time: float, is_residential: bool = False) -> Tuple[bool, str, float]:
        """基础代理测试（不依赖aiohttp）"""
        try:
            import socket
            
            # 住宅代理使用更长的超时时间
            test_timeout = config.RESIDENTIAL_PROXY_TIMEOUT if is_residential else self.test_timeout
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(test_timeout)
            
            result = sock.connect_ex((proxy_info['host'], proxy_info['port']))
            elapsed = time.time() - start_time
            sock.close()
            
            if result == 0:
                return True, f"端口开放 {elapsed:.2f}s", elapsed
            else:
                return False, f"端口关闭 {elapsed:.2f}s", elapsed
                
        except Exception as e:
            elapsed = time.time() - start_time
            return False, f"测试失败: {str(e)[:20]} {elapsed:.2f}s", elapsed
    
    async def test_all_proxies(self, progress_callback=None) -> Tuple[List[Dict], List[Dict], Dict]:
        """测试所有代理"""
        if not self.proxy_manager.proxies:
            return [], [], {}
        
        print(f"🧪 开始测试 {len(self.proxy_manager.proxies)} 个代理...")
        print(f"⚡ 并发数: {self.max_concurrent}, 超时: {self.test_timeout}秒")
        
        working_proxies = []
        failed_proxies = []
        statistics = {
            'total': len(self.proxy_manager.proxies),
            'tested': 0,
            'working': 0,
            'failed': 0,
            'avg_response_time': 0,
            'start_time': time.time()
        }
        
        # 创建信号量控制并发
        semaphore = asyncio.Semaphore(self.max_concurrent)
        response_times = []
        
        async def test_single_proxy(proxy_info):
            async with semaphore:
                success, message, response_time = await self.test_proxy_connection(proxy_info)
                
                statistics['tested'] += 1
                
                if success:
                    working_proxies.append(proxy_info)
                    statistics['working'] += 1
                    response_times.append(response_time)
                    # 隐藏代理详细信息
                    print(f"✅ 代理测试通过 - {message}")
                else:
                    failed_proxies.append(proxy_info)
                    statistics['failed'] += 1
                    # 隐藏代理详细信息
                    print(f"❌ 代理测试失败 - {message}")
                
                # 更新统计
                if response_times:
                    statistics['avg_response_time'] = sum(response_times) / len(response_times)
                
                # 调用进度回调
                if progress_callback:
                    await progress_callback(statistics['tested'], statistics['total'], statistics)
        
        # 分批处理代理（使用较大批次以提高速度）
        batch_size = config.PROXY_BATCH_SIZE
        for i in range(0, len(self.proxy_manager.proxies), batch_size):
            batch = self.proxy_manager.proxies[i:i + batch_size]
            tasks = [test_single_proxy(proxy) for proxy in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
            
            # 批次间短暂休息（减少到0.05秒以提高速度）
            await asyncio.sleep(0.05)
        
        total_time = time.time() - statistics['start_time']
        test_speed = statistics['total'] / total_time if total_time > 0 else 0
        
        print(f"\n📊 代理测试完成:")
        print(f"   总计: {statistics['total']} 个")
        print(f"   可用: {statistics['working']} 个 ({statistics['working']/statistics['total']*100:.1f}%)")
        print(f"   失效: {statistics['failed']} 个 ({statistics['failed']/statistics['total']*100:.1f}%)")
        print(f"   平均响应: {statistics['avg_response_time']:.2f} 秒")
        print(f"   测试速度: {test_speed:.1f} 代理/秒")
        print(f"   总耗时: {total_time:.1f} 秒")
        
        return working_proxies, failed_proxies, statistics
    
    async def cleanup_and_update_proxies(self, auto_confirm: bool = False) -> Tuple[bool, str]:
        """清理并更新代理文件"""
        if not config.PROXY_AUTO_CLEANUP and not auto_confirm:
            return False, "自动清理已禁用"
        
        # 备份原始文件
        if not self.proxy_manager.backup_proxy_file():
            return False, "备份失败"
        
        # 测试所有代理
        working_proxies, failed_proxies, stats = await self.test_all_proxies()
        
        if not working_proxies:
            return False, "没有可用的代理"
        
        # 保存分类结果
        working_file = self.proxy_manager.save_working_proxies(working_proxies)
        failed_file = self.proxy_manager.save_failed_proxies(failed_proxies)
        
        # 更新原始代理文件为可用代理
        try:
            with open(self.proxy_manager.proxy_file, 'w', encoding='utf-8') as f:
                f.write("# 自动清理后的可用代理文件\n")
                f.write(f"# 清理时间: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S CST')}\n")
                f.write(f"# 原始数量: {stats['total']}, 可用数量: {stats['working']}\n\n")
                
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
            
            # 重新加载代理
            self.proxy_manager.load_proxies()
            
            result_msg = f"""✅ 代理清理完成!
            
📊 清理统计:
• 原始代理: {stats['total']} 个
• 可用代理: {stats['working']} 个 
• 失效代理: {stats['failed']} 个
• 成功率: {stats['working']/stats['total']*100:.1f}%

📁 文件保存:
• 主文件: {self.proxy_manager.proxy_file} (已更新为可用代理)
• 可用代理: {working_file}
• 失效代理: {failed_file}
• 备份文件: {self.proxy_manager.proxy_file.replace('.txt', '_backup.txt')}"""
            
            return True, result_msg
            
        except Exception as e:
            return False, f"更新代理文件失败: {e}"


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
