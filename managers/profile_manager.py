"""
managers.profile_manager - Account profile management
"""
import asyncio
import logging
import random
import secrets
import string
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from models.dataclasses import ProfileUpdateConfig

logger = logging.getLogger(__name__)

try:
    from faker import Faker
    FAKER_AVAILABLE = True
except ImportError:
    FAKER_AVAILABLE = False

try:
    import phonenumbers
    PHONENUMBERS_AVAILABLE = True
except ImportError:
    PHONENUMBERS_AVAILABLE = False

try:
    from telethon import TelegramClient
    from telethon.errors import UsernameOccupiedError, UsernameInvalidError
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False

try:
    from opentele.td import TDesktop
    from opentele.api import UseCurrentSession
    OPENTELE_AVAILABLE = True
except ImportError:
    OPENTELE_AVAILABLE = False


class ProfileManager:
    """账号资料管理器 - 使用Faker动态生成随机不重样的本地化内容"""
    
    def __init__(self, proxy_manager: 'ProxyManager', db: 'Database'):
        self.proxy_manager = proxy_manager
        self.db = db
        self.faker_instances = {}  # 存储不同语言的Faker实例
        self.used_names = set()  # 记录已使用的姓名，确保不重复
        self.used_usernames = set()  # 记录已使用的用户名
        self.init_faker_instances()
    
    def init_faker_instances(self):
        """初始化各国语言的Faker实例"""
        try:
            from faker import Faker
            
            # 创建不同语言的Faker实例
            # Faker支持的locale: https://faker.readthedocs.io/en/master/locales.html
            self.faker_instances = {
                'CN': Faker('zh_CN'),   # 中文（中国）
                'HK': Faker('zh_TW'),   # 中文（台湾/香港）
                'MO': Faker('zh_TW'),   # 中文（澳门）
                'TW': Faker('zh_TW'),   # 中文（台湾）
                'US': Faker('en_US'),   # 英语（美国）
                'GB': Faker('en_GB'),   # 英语（英国）
                'CA': Faker('en_CA'),   # 英语（加拿大）
                'AU': Faker('en_AU'),   # 英语（澳大利亚）
                'NZ': Faker('en_NZ'),   # 英语（新西兰）
                'ID': Faker('id_ID'),   # 印尼语
                'RU': Faker('ru_RU'),   # 俄语
                'UA': Faker('uk_UA'),   # 乌克兰语
                'BY': Faker('ru_RU'),   # 白俄罗斯（使用俄语）
                'KZ': Faker('ru_RU'),   # 哈萨克斯坦（使用俄语）
                'JP': Faker('ja_JP'),   # 日语
                'KR': Faker('ko_KR'),   # 韩语
                'DE': Faker('de_DE'),   # 德语
                'FR': Faker('fr_FR'),   # 法语
                'ES': Faker('es_ES'),   # 西班牙语
                'IT': Faker('it_IT'),   # 意大利语
                'PT': Faker('pt_PT'),   # 葡萄牙语
                'BR': Faker('pt_BR'),   # 葡萄牙语（巴西）
                'TR': Faker('tr_TR'),   # 土耳其语
                'PL': Faker('pl_PL'),   # 波兰语
                'NL': Faker('nl_NL'),   # 荷兰语
                'SE': Faker('sv_SE'),   # 瑞典语
                'NO': Faker('no_NO'),   # 挪威语
                'DK': Faker('da_DK'),   # 丹麦语
                'FI': Faker('fi_FI'),   # 芬兰语
                'TH': Faker('th_TH'),   # 泰语
                'VN': Faker('vi_VN'),   # 越南语
                'PH': Faker('fil_PH'),  # 菲律宾语
                'IN': Faker('en_IN'),   # 印度（使用英语）
                'PK': Faker('en_IN'),   # 巴基斯坦（使用英语）
                'BD': Faker('en_IN'),   # 孟加拉国（使用英语）
                'IR': Faker('fa_IR'),   # 波斯语（伊朗）
                'SA': Faker('ar_SA'),   # 阿拉伯语（沙特）
                'AE': Faker('ar_SA'),   # 阿拉伯语（阿联酋）
                'EG': Faker('ar_EG'),   # 阿拉伯语（埃及）
                'IL': Faker('he_IL'),   # 希伯来语（以色列）
                'GR': Faker('el_GR'),   # 希腊语
                'CZ': Faker('cs_CZ'),   # 捷克语
                'HU': Faker('hu_HU'),   # 匈牙利语
                'RO': Faker('ro_RO'),   # 罗马尼亚语
                'SK': Faker('sk_SK'),   # 斯洛伐克语
                'HR': Faker('hr_HR'),   # 克罗地亚语
                'BG': Faker('bg_BG'),   # 保加利亚语
                'MX': Faker('es_MX'),   # 西班牙语（墨西哥）
                'AR': Faker('es_AR'),   # 西班牙语（阿根廷）
                'CO': Faker('es_CO'),   # 西班牙语（哥伦比亚）
                'CL': Faker('es_CL'),   # 西班牙语（智利）
            }
            
            print(f"✅ Faker实例初始化完成，支持 {len(self.faker_instances)} 个国家/地区")
        except Exception as e:
            logger.error(f"初始化Faker实例失败: {e}")
            # 至少提供一个默认的英语实例
            from faker import Faker
            self.faker_instances = {'DEFAULT': Faker('en_US')}
    
    def get_country_from_phone(self, phone: str) -> str:
        """根据手机号获取国家代码（ISO 3166-1 alpha-2）"""
        try:
            import phonenumbers
            # 确保手机号以+开头
            if not phone.startswith('+'):
                phone = '+' + phone
            
            parsed = phonenumbers.parse(phone, None)
            country_code = phonenumbers.region_code_for_number(parsed)
            
            logger.info(f"手机号 {phone} 解析为国家: {country_code}")
            return country_code if country_code else 'US'  # 默认返回美国
        except Exception as e:
            logger.warning(f"解析手机号国家失败 {phone}: {e}")
            return 'US'  # 默认返回美国
    
    def generate_random_name(self, country_code: str) -> Tuple[str, str]:
        """根据国家代码生成随机不重样的本地化姓名
        
        Args:
            country_code: ISO 3166-1 alpha-2 国家代码（如 CN, US, RU, ID 等）
            
        Returns:
            (first_name, last_name) 元组
        """
        try:
            # 获取对应国家的Faker实例，如果没有则使用默认
            faker = self.faker_instances.get(country_code.upper(), 
                                            self.faker_instances.get('DEFAULT', 
                                            self.faker_instances.get('US')))
            
            # 尝试生成不重复的姓名，最多尝试10次
            for _ in range(10):
                # 根据国家选择姓名格式
                if country_code.upper() in ['CN', 'HK', 'TW', 'MO']:
                    # 中文姓名：姓+名，通常2-3个字
                    full_name = faker.name()
                    # 中文姓名不分first/last，全部作为first_name
                    first_name = full_name
                    last_name = ''
                elif country_code.upper() in ['JP', 'KR']:
                    # 日韩姓名：也是姓在前，名在后
                    full_name = faker.name()
                    # 尝试分割
                    parts = full_name.split()
                    if len(parts) >= 2:
                        last_name = parts[0]  # 姓
                        first_name = ' '.join(parts[1:])  # 名
                    else:
                        first_name = full_name
                        last_name = ''
                else:
                    # 西方姓名：名在前，姓在后
                    first_name = faker.first_name()
                    last_name = faker.last_name()
                    full_name = f"{first_name} {last_name}"
                
                # 检查是否重复
                if full_name not in self.used_names:
                    self.used_names.add(full_name)
                    logger.info(f"生成姓名 [{country_code}]: {first_name} {last_name}")
                    return (first_name, last_name)
            
            # 如果10次都重复，则返回最后一次生成的（虽然重复但总比失败好）
            logger.warning(f"姓名生成重复，使用最后一次结果: {first_name} {last_name}")
            return (first_name, last_name)
            
        except Exception as e:
            logger.error(f"生成随机姓名失败 [{country_code}]: {e}")
            # 失败时返回简单的随机名字
            return (f"User{random.randint(1000, 9999)}", '')
    
    def generate_random_bio(self, country_code: str) -> str:
        """根据国家代码生成随机不重样的本地化简介
        
        Args:
            country_code: ISO 3166-1 alpha-2 国家代码（如 CN, US, RU, ID 等）
            
        Returns:
            本地化的个人简介文本
        """
        try:
            # 获取对应国家的Faker实例
            faker = self.faker_instances.get(country_code.upper(), 
                                            self.faker_instances.get('DEFAULT', 
                                            self.faker_instances.get('US')))
            
            # 根据国家生成不同风格的简介
            bio_templates = []
            
            if country_code.upper() in ['CN', 'HK', 'TW', 'MO']:
                # 中文简介模板
                templates = [
                    lambda: f"{faker.job()}，{faker.catch_phrase()}",
                    lambda: f"来自{faker.city()}，{faker.job()}",
                    lambda: f"{faker.catch_phrase()}",
                    lambda: f"{faker.job()} | {faker.city()}",
                    lambda: f"热爱生活 | {faker.job()}",
                ]
            elif country_code.upper() in ['RU', 'UA', 'BY', 'KZ']:
                # 俄语简介模板
                templates = [
                    lambda: f"{faker.job()} | {faker.city()}",
                    lambda: f"{faker.catch_phrase()}",
                    lambda: f"{faker.job()} из {faker.city()}",
                ]
            elif country_code.upper() == 'ID':
                # 印尼简介模板
                templates = [
                    lambda: f"{faker.job()} | {faker.city()}",
                    lambda: f"{faker.catch_phrase()}",
                    lambda: f"Suka {faker.job()}",
                ]
            else:
                # 英文及其他语言简介模板
                templates = [
                    lambda: f"{faker.job()} | {faker.city()}",
                    lambda: f"{faker.catch_phrase()}",
                    lambda: f"{faker.job()} from {faker.city()}",
                    lambda: faker.sentence(nb_words=6)[:-1],  # 6个词的句子
                ]
            
            # 随机选择一个模板并生成
            bio = random.choice(templates)()
            
            # 限制长度（Telegram bio最多70个字符）
            if len(bio) > 70:
                bio = bio[:67] + '...'
            
            logger.info(f"生成简介 [{country_code}]: {bio}")
            return bio
            
        except Exception as e:
            logger.error(f"生成随机简介失败 [{country_code}]: {e}")
            return ''
    
    def generate_random_username(self) -> str:
        """生成随机用户名"""
        # 生成8-15位的随机用户名（字母+数字）
        length = random.randint(8, 15)
        chars = string.ascii_lowercase + string.digits
        username = ''.join(random.choice(chars) for _ in range(length))
        # 确保以字母开头
        if username[0].isdigit():
            username = random.choice(string.ascii_lowercase) + username[1:]
        return username
    
    async def update_profile_name(self, client, first_name: str, last_name: str = "") -> bool:
        """修改账号姓名"""
        try:
            from telethon.tl.functions.account import UpdateProfileRequest
            await client(UpdateProfileRequest(
                first_name=first_name,
                last_name=last_name
            ))
            logger.info(f"成功修改姓名: {first_name} {last_name}")
            return True
        except Exception as e:
            logger.error(f"修改姓名失败: {e}")
            return False
    
    async def update_profile_bio(self, client, bio: str) -> bool:
        """修改账号简介"""
        try:
            from telethon.tl.functions.account import UpdateProfileRequest
            await client(UpdateProfileRequest(about=bio))
            logger.info(f"成功修改简介: {bio}")
            return True
        except Exception as e:
            logger.error(f"修改简介失败: {e}")
            return False
    
    async def update_profile_username(self, client, username: str) -> bool:
        """修改账号用户名"""
        try:
            from telethon.tl.functions.account import UpdateUsernameRequest
            await client(UpdateUsernameRequest(username=username))
            logger.info(f"成功修改用户名: {username}")
            return True
        except UsernameOccupiedError:
            logger.warning(f"用户名已被占用: {username}")
            return False
        except UsernameInvalidError:
            logger.warning(f"用户名无效: {username}")
            return False
        except Exception as e:
            logger.error(f"修改用户名失败: {e}")
            return False
    
    async def update_profile_photo(self, client, photo_path: str) -> bool:
        """修改账号头像"""
        try:
            from telethon.tl.functions.photos import UploadProfilePhotoRequest
            await client(UploadProfilePhotoRequest(
                file=await client.upload_file(photo_path)
            ))
            logger.info(f"成功上传头像: {photo_path}")
            return True
        except Exception as e:
            logger.error(f"上传头像失败: {e}")
            return False
    
    async def delete_profile_photos(self, client, delete_all: bool = True) -> bool:
        """删除账号头像"""
        try:
            from telethon.tl.functions.photos import DeletePhotosRequest, GetUserPhotosRequest
            
            me = await client.get_me()
            photos = await client(GetUserPhotosRequest(
                user_id=me,
                offset=0,
                max_id=0,
                limit=100
            ))
            
            if hasattr(photos, 'photos') and photos.photos:
                photo_ids = list(photos.photos)
                await client(DeletePhotosRequest(id=photo_ids))
                logger.info(f"成功删除 {len(photo_ids)} 个头像")
                return True
            else:
                logger.info("没有头像需要删除")
                return True
        except Exception as e:
            logger.error(f"删除头像失败: {e}")
            return False
    
    async def batch_update_profiles(self, files: List[Tuple[str, str]], 
                                     file_type: str,
                                     config: ProfileUpdateConfig,
                                     progress_callback) -> Dict:
        """批量更新账号资料
        
        Args:
            files: 文件列表 [(账号名, 文件路径), ...]
            file_type: 文件类型 ('tdata', 'session', 'session-json')
            config: 资料更新配置
            progress_callback: 进度回调函数
            
        Returns:
            更新结果统计
        """
        results = {
            'total': len(files),
            'success': 0,
            'failed': 0,
            'details': []
        }
        
        for idx, (account_name, file_path) in enumerate(files):
            try:
                await progress_callback(f"处理账号 {idx + 1}/{len(files)}: {account_name}")
                
                # 创建客户端连接
                client = None
                session_path = None
                
                try:
                    # 根据文件类型创建客户端
                    if file_type == 'tdata':
                        # TData 转换为 session
                        tdesk = TDesktop(file_path)
                        session_path = f"/tmp/profile_update_{secrets.token_hex(8)}.session"
                        client = await tdesk.ToTelethon(session_path, flag=UseCurrentSession)
                        # 重要：TData转Session后必须显式连接
                        if not client.is_connected():
                            await client.connect()
                    elif file_type in ['session', 'session-json']:
                        session_path = file_path
                        # 从session文件创建客户端
                        # 需要api_id和api_hash（从db或config获取）
                        api_id = config.get('api_id', 2040)
                        api_hash = config.get('api_hash', 'b18441a1ff607e10a989891a5462e627')
                        client = TelegramClient(session_path, api_id, api_hash)
                        await client.connect()
                    
                    if not client or not await client.is_user_authorized():
                        raise Exception("客户端未授权")
                    
                    # 获取账号信息
                    me = await client.get_me()
                    phone = me.phone if hasattr(me, 'phone') else None
                    country = self.get_country_from_phone(phone) if phone else 'US'
                    
                    detail = {
                        'account': account_name,
                        'phone': phone,
                        'actions': []
                    }
                    
                    # 根据配置更新资料
                    await asyncio.sleep(random.uniform(1, 3))  # 随机延迟避免限流
                    
                    # 1. 更新姓名
                    if config.update_name:
                        first_name = None
                        last_name = ''
                        
                        if config.mode == 'random':
                            first_name, last_name = self.generate_random_name(country)
                        elif config.custom_names:
                            # 循环使用自定义姓名列表
                            full_name = config.custom_names[idx % len(config.custom_names)]
                            parts = full_name.split(' ', 1)
                            first_name = parts[0]
                            last_name = parts[1] if len(parts) > 1 else ''
                        
                        if first_name:
                            if await self.update_profile_name(client, first_name, last_name):
                                detail['actions'].append(f"✅ 姓名: {first_name} {last_name}")
                            else:
                                detail['actions'].append(f"❌ 姓名更新失败")
                    
                    # 2. 处理头像
                    if config.update_photo:
                        if config.photo_action == 'delete_all':
                            if await self.delete_profile_photos(client):
                                detail['actions'].append("✅ 删除所有头像")
                            else:
                                detail['actions'].append("❌ 删除头像失败")
                        elif config.photo_action == 'custom' and config.custom_photos:
                            photo_path = config.custom_photos[idx % len(config.custom_photos)]
                            if await self.update_profile_photo(client, photo_path):
                                detail['actions'].append(f"✅ 上传头像")
                            else:
                                detail['actions'].append("❌ 上传头像失败")
                    
                    # 3. 更新简介
                    if config.update_bio:
                        bio = ''
                        if config.bio_action == 'clear':
                            bio = ''
                        elif config.bio_action == 'random':
                            bio = self.generate_random_bio(country)
                        elif config.bio_action == 'custom' and config.custom_bios:
                            bio = config.custom_bios[idx % len(config.custom_bios)]
                        
                        if await self.update_profile_bio(client, bio):
                            detail['actions'].append(f"✅ 简介: {bio[:20]}...")
                        else:
                            detail['actions'].append("❌ 简介更新失败")
                    
                    # 4. 更新用户名
                    if config.update_username:
                        username = ''
                        if config.username_action == 'delete':
                            username = ''
                        elif config.username_action == 'random':
                            username = self.generate_random_username()
                        elif config.username_action == 'custom' and config.custom_usernames:
                            username = config.custom_usernames[idx % len(config.custom_usernames)]
                        
                        if await self.update_profile_username(client, username):
                            detail['actions'].append(f"✅ 用户名: {username if username else '已删除'}")
                        else:
                            detail['actions'].append("❌ 用户名更新失败")
                    
                    results['success'] += 1
                    results['details'].append(detail)
                    
                except Exception as e:
                    logger.error(f"处理账号 {account_name} 失败: {e}")
                    results['failed'] += 1
                    results['details'].append({
                        'account': account_name,
                        'error': str(e)
                    })
                finally:
                    if client:
                        await client.disconnect()
                    # 清理临时session文件
                    if session_path and session_path.startswith('/tmp/'):
                        try:
                            import os
                            os.remove(session_path)
                        except:
                            pass
                
            except Exception as e:
                logger.error(f"批量更新过程错误: {e}")
                results['failed'] += 1
        
        return results
