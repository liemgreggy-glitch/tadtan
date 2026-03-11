"""
managers.device_params - Device parameter management
"""
import logging
import os
import random
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class DeviceParamsManager:
    """设备参数管理器 - 从device_params文件夹读取并随机选择设备参数"""
    
    def __init__(self, params_dir: str = "device_params"):
        self.params_dir = params_dir
        self.params = {}
        self.load_all_params()
    
    def load_all_params(self):
        """加载所有设备参数文件"""
        if not os.path.exists(self.params_dir):
            print(f"⚠️ 设备参数目录不存在: {self.params_dir}")
            return
        
        param_files = {
            'api_credentials': 'api_id+api_hash.txt',
            'app_name': 'app_name.txt',
            'app_version': 'app_version.txt',
            'cpu_cores': 'cpu_cores.txt',
            'device_sdk': 'device+sdk.txt',
            'device_model': 'device_model.txt',
            'lang_code': 'lang_code.txt',
            'ram_size': 'ram_size.txt',
            'screen_resolution': 'screen_resolution.txt',
            'system_lang_code': 'system_lang_code.txt',
            'system_version': 'system_version.txt',
            'timezone': 'timezone.txt',
            'user_agent': 'user_agent.txt'
        }
        
        for param_name, filename in param_files.items():
            filepath = os.path.join(self.params_dir, filename)
            try:
                if os.path.exists(filepath):
                    with open(filepath, 'r', encoding='utf-8') as f:
                        lines = [line.strip() for line in f if line.strip() and not line.startswith('#')]
                        self.params[param_name] = lines
                        print(f"✅ 加载设备参数: {param_name} ({len(lines)} 项)")
                else:
                    print(f"⚠️ 设备参数文件不存在: {filename}")
            except Exception as e:
                print(f"❌ 加载设备参数失败 {filename}: {e}")
        
        total_params = sum(len(v) for v in self.params.values())
        print(f"📱 设备参数管理器初始化完成，共加载 {total_params} 个参数项")
    
    def get_random_device_params(self) -> Dict[str, Any]:
        """获取一组随机设备参数"""
        params = {}
        
        # API凭据（api_id和api_hash）
        if 'api_credentials' in self.params and self.params['api_credentials']:
            cred = random.choice(self.params['api_credentials'])
            if ':' in cred:
                try:
                    api_id, api_hash = cred.split(':', 1)
                    params['api_id'] = int(api_id.strip())
                    params['api_hash'] = api_hash.strip()
                except (ValueError, AttributeError) as e:
                    print(f"⚠️ 解析API凭据失败: {cred} - {e}")
        
        # 其他参数
        for key in ['app_name', 'app_version', 'device_model', 'lang_code', 
                    'system_lang_code', 'system_version', 'timezone', 'user_agent']:
            if key in self.params and self.params[key]:
                params[key] = random.choice(self.params[key])
        
        # 数值类型参数
        if 'cpu_cores' in self.params and self.params['cpu_cores']:
            try:
                params['cpu_cores'] = int(random.choice(self.params['cpu_cores']))
            except (ValueError, AttributeError) as e:
                print(f"⚠️ 解析CPU核心数失败: {e}")
        
        if 'ram_size' in self.params and self.params['ram_size']:
            try:
                params['ram_size'] = int(random.choice(self.params['ram_size']))
            except (ValueError, AttributeError) as e:
                print(f"⚠️ 解析RAM大小失败: {e}")
        
        # 设备和SDK
        if 'device_sdk' in self.params and self.params['device_sdk']:
            device_sdk = random.choice(self.params['device_sdk'])
            if ':' in device_sdk:
                device, sdk = device_sdk.split(':', 1)
                params['device'] = device.strip()
                params['sdk'] = sdk.strip()
        
        # 屏幕分辨率
        if 'screen_resolution' in self.params and self.params['screen_resolution']:
            resolution = random.choice(self.params['screen_resolution'])
            if 'x' in resolution:
                try:
                    width, height = resolution.split('x', 1)
                    params['screen_width'] = int(width.strip())
                    params['screen_height'] = int(height.strip())
                except (ValueError, AttributeError) as e:
                    print(f"⚠️ 解析屏幕分辨率失败: {resolution} - {e}")
        
        return params
    
    def get_random_api_credentials(self) -> Tuple[Optional[int], Optional[str]]:
        """获取随机API凭据（api_id和api_hash）"""
        if 'api_credentials' in self.params and self.params['api_credentials']:
            cred = random.choice(self.params['api_credentials'])
            if ':' in cred:
                api_id, api_hash = cred.split(':', 1)
                return int(api_id.strip()), api_hash.strip()
        return None, None


class DeviceParamsLoader:
    """设备参数加载器 - 从device_params目录加载并随机组合参数
    
    Loads device parameters from text files in the device_params directory
    and provides methods to get random or compatible parameter combinations.
    """
    
    def __init__(self, params_dir: str = None):
        """初始化设备参数加载器
        
        Args:
            params_dir: 参数文件目录路径，默认使用脚本目录下的device_params
        """
        if params_dir is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            params_dir = os.path.join(script_dir, "device_params")
        
        self.params_dir = params_dir
        self.params: Dict[str, List[str]] = {}
        self.load_all_params()
    
    def load_all_params(self) -> None:
        """加载所有参数文件"""
        if not os.path.exists(self.params_dir):
            print(f"⚠️ 设备参数目录不存在: {self.params_dir}")
            return
        
        # 定义参数文件名到参数键的映射
        param_files = {
            'api_id+api_hash.txt': 'api_credentials',
            'app_version.txt': 'app_version',
            'device+sdk.txt': 'device_sdk',
            'lang_code.txt': 'lang_code',
            'system_lang_code.txt': 'system_lang_code',
            'system_version.txt': 'system_version',
            'app_name.txt': 'app_name',
            'device_model.txt': 'device_model',
            'timezone.txt': 'timezone',
            'screen_resolution.txt': 'screen_resolution',
            'user_agent.txt': 'user_agent',
            'cpu_cores.txt': 'cpu_cores',
            'ram_size.txt': 'ram_size'
        }
        
        for filename, param_key in param_files.items():
            file_path = os.path.join(self.params_dir, filename)
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        lines = [line.strip() for line in f if line.strip()]
                        self.params[param_key] = lines
                        print(f"✅ 加载设备参数 {filename}: {len(lines)} 项")
                except Exception as e:
                    print(f"❌ 加载设备参数失败 {filename}: {e}")
            else:
                print(f"⚠️ 设备参数文件不存在: {filename}")
    
    def _get_random_param(self, param_key: str, default: str = "") -> str:
        """获取指定参数的随机值
        
        Args:
            param_key: 参数键名
            default: 默认值（当参数不存在时）
            
        Returns:
            随机选择的参数值或默认值
        """
        if param_key in self.params and self.params[param_key]:
            return random.choice(self.params[param_key])
        return default
    
    def get_random_device_config(self) -> Dict[str, Any]:
        """获取随机设备配置
        
        Returns:
            包含所有随机设备参数的字典
        """
        config_dict = {}
        
        # API credentials (format: api_id:api_hash)
        api_cred = self._get_random_param('api_credentials', '')
        if api_cred and ':' in api_cred:
            api_id, api_hash = api_cred.split(':', 1)
            try:
                config_dict['api_id'] = int(api_id)
                config_dict['api_hash'] = api_hash
            except ValueError:
                # Skip invalid API credentials
                pass
        
        # App version
        config_dict['app_version'] = self._get_random_param('app_version', '4.12.2 x64')
        
        # Device and SDK (format: device:sdk)
        device_sdk = self._get_random_param('device_sdk', 'PC 64bit:Windows 10')
        if ':' in device_sdk:
            device, sdk = device_sdk.split(':', 1)
            config_dict['device'] = device
            config_dict['sdk'] = sdk
        else:
            config_dict['device'] = device_sdk
            config_dict['sdk'] = 'Windows 10'
        
        # Language codes
        config_dict['lang_code'] = self._get_random_param('lang_code', 'en')
        config_dict['system_lang_code'] = self._get_random_param('system_lang_code', 'en-US')
        
        # System version
        config_dict['system_version'] = self._get_random_param('system_version', 'Windows 10 Pro 19045')
        
        # App name
        config_dict['app_name'] = self._get_random_param('app_name', 'Telegram Desktop')
        
        # Device model
        config_dict['device_model'] = self._get_random_param('device_model', 'PC 64bit')
        
        # Timezone
        config_dict['timezone'] = self._get_random_param('timezone', 'UTC+0')
        
        # Screen resolution
        config_dict['screen_resolution'] = self._get_random_param('screen_resolution', '1920x1080')
        
        # User agent
        config_dict['user_agent'] = self._get_random_param('user_agent', 
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        # CPU cores
        cpu_cores = self._get_random_param('cpu_cores', '8')
        try:
            config_dict['cpu_cores'] = int(cpu_cores)
        except ValueError:
            config_dict['cpu_cores'] = 8
        
        # RAM size (in MB)
        ram_size = self._get_random_param('ram_size', '16384')
        try:
            config_dict['ram_size'] = int(ram_size)
        except ValueError:
            config_dict['ram_size'] = 16384
        
        return config_dict
    
    def get_compatible_params(self) -> Dict[str, Any]:
        """获取兼容的参数组合（智能匹配）
        
        智能匹配规则:
        - Windows 11 系统配合较新的 Telegram 版本
        - Windows 10 系统可以配合任意版本
        - 语言代码与系统语言代码匹配
        
        Returns:
            包含兼容设备参数的字典
        """
        config = self.get_random_device_config()
        
        # 智能匹配: Windows 11 使用较新版本
        if 'Windows 11' in config.get('system_version', ''):
            # 确保使用 4.x 版本的 Telegram
            newer_versions = [v for v in self.params.get('app_version', []) if v.startswith('4.')]
            if newer_versions:
                config['app_version'] = random.choice(newer_versions)
        
        # 智能匹配: 语言代码与系统语言代码应该一致
        lang_code = config.get('lang_code', 'en')
        system_lang_codes = self.params.get('system_lang_code', [])
        
        # 找到匹配的系统语言代码
        matching_system_langs = [slc for slc in system_lang_codes if slc.startswith(lang_code)]
        if matching_system_langs:
            config['system_lang_code'] = random.choice(matching_system_langs)
        
        # 智能匹配: 高端配置（多核CPU）配合更多内存
        cpu_cores = config.get('cpu_cores', 8)
        if cpu_cores >= 16:
            # 高核心数配合更大内存
            high_ram = []
            for r in self.params.get('ram_size', []):
                try:
                    if int(r) >= 32768:
                        high_ram.append(r)
                except ValueError:
                    continue
            if high_ram:
                try:
                    config['ram_size'] = int(random.choice(high_ram))
                except ValueError:
                    pass
        
        return config
