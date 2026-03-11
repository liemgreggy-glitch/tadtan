"""
core.database - Database operations and management
"""
import logging
import os
import random
import sqlite3
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.constants import BEIJING_TZ

logger = logging.getLogger(__name__)

# Lazy import to avoid circular imports
def _get_config():
    from core.config import Config
    return Config()

try:
    import tdata as _tdata_module
    config = _tdata_module.config
except ImportError:
    from core.config import Config
    config = Config()


class Database:
    def __init__(self, db_name: str):
        self.db_name = db_name
        self.init_database()
    
    def init_database(self):
        conn = sqlite3.connect(self.db_name)
        c = conn.cursor()
        
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                register_time TEXT,
                last_active TEXT,
                status TEXT DEFAULT ''
            )
        """)
        
        c.execute("""
            CREATE TABLE IF NOT EXISTS memberships (
                user_id INTEGER PRIMARY KEY,
                level TEXT,
                trial_expiry_time TEXT,
                created_at TEXT
            )
        """)
        
        # 新增管理员表
        c.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                added_by INTEGER,
                added_time TEXT,
                is_super_admin INTEGER DEFAULT 0
            )
        """)
        
        # 新增代理设置表
        c.execute("""
            CREATE TABLE IF NOT EXISTS proxy_settings (
                id INTEGER PRIMARY KEY DEFAULT 1,
                proxy_enabled INTEGER DEFAULT 1,
                updated_time TEXT,
                updated_by INTEGER
            )
        """)
        
        # 广播消息表
        c.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                buttons_json TEXT,
                target TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                total INTEGER DEFAULT 0,
                success INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                duration_sec REAL DEFAULT 0
            )
        """)
        
        # 广播日志表
        c.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broadcast_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                sent_at TEXT NOT NULL,
                FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id)
            )
        """)
        
        # 兑换码表
        c.execute("""
            CREATE TABLE IF NOT EXISTS redeem_codes (
                code TEXT PRIMARY KEY,
                level TEXT DEFAULT '会员',
                days INTEGER NOT NULL,
                status TEXT DEFAULT 'active',
                created_by INTEGER,
                created_at TEXT,
                redeemed_by INTEGER,
                redeemed_at TEXT
            )
        """)
        
        # 忘记2FA日志表
        c.execute("""
            CREATE TABLE IF NOT EXISTS forget_2fa_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT,
                account_name TEXT,
                phone TEXT,
                file_type TEXT,
                proxy_used TEXT,
                status TEXT,
                error TEXT,
                cooling_until TEXT,
                elapsed REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 批量创建记录表
        c.execute("""
            CREATE TABLE IF NOT EXISTS batch_creations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                creation_type TEXT NOT NULL,
                name TEXT NOT NULL,
                username TEXT,
                invite_link TEXT,
                creator_id INTEGER,
                created_at TEXT NOT NULL,
                date TEXT NOT NULL
            )
        """)
        
        # 迁移：添加expiry_time列到memberships表
        try:
            c.execute("ALTER TABLE memberships ADD COLUMN expiry_time TEXT")
            print("✅ 已添加 memberships.expiry_time 列")
        except sqlite3.OperationalError:
            # 列已存在，忽略
            pass
        
        conn.commit()
        conn.close()
    
    def save_user(self, user_id: int, username: str, first_name: str, status: str = ""):
        conn = None
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
            
            # Check if user exists (optimized query)
            c.execute("SELECT 1 FROM users WHERE user_id = ? LIMIT 1", (user_id,))
            exists = c.fetchone() is not None
            
            if exists:
                # Update existing user, preserve register_time
                c.execute("""
                    UPDATE users 
                    SET username = ?, first_name = ?, last_active = ?, status = ?
                    WHERE user_id = ?
                """, (username, first_name, now, status, user_id))
            else:
                # Insert new user
                c.execute("""
                    INSERT INTO users 
                    (user_id, username, first_name, register_time, last_active, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (user_id, username, first_name, now, now, status))
            
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ 保存用户失败: {e}")
            return False
        finally:
            if conn:
                conn.close()
    
    def save_membership(self, user_id: int, level: str):
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            now = datetime.now(BEIJING_TZ)
            
            if level == "体验会员":
                expiry = now + timedelta(seconds=config.TRIAL_DURATION_SECONDS)
                c.execute("""
                    INSERT OR REPLACE INTO memberships 
                    (user_id, level, trial_expiry_time, created_at)
                    VALUES (?, ?, ?, ?)
                """, (user_id, level, expiry.strftime("%Y-%m-%d %H:%M:%S"), 
                      now.strftime("%Y-%m-%d %H:%M:%S")))
            
            conn.commit()
            conn.close()
            return True
        except:
            return False
    
    def check_membership(self, user_id: int) -> Tuple[bool, str, str]:
        # 管理员优先
        if self.is_admin(user_id):
            return True, "管理员", "永久有效"
        
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            c.execute("SELECT level, trial_expiry_time, expiry_time FROM memberships WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            conn.close()
            
            if not row:
                return False, "无会员", "未订阅"
            
            level, trial_expiry_time, expiry_time = row
            
            # 优先检查新的expiry_time字段
            if expiry_time:
                try:
                    # Database stores naive datetime strings, parse them and compare with naive Beijing time
                    # .replace(tzinfo=None) converts timezone-aware Beijing time to naive for comparison
                    expiry_dt = datetime.strptime(expiry_time, "%Y-%m-%d %H:%M:%S")
                    if expiry_dt > datetime.now(BEIJING_TZ).replace(tzinfo=None):
                        return True, level, expiry_dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    pass
            
            # 兼容旧的trial_expiry_time字段
            if level == "体验会员" and trial_expiry_time:
                # Database stores naive datetime strings, compare with naive Beijing time
                expiry_dt = datetime.strptime(trial_expiry_time, "%Y-%m-%d %H:%M:%S")
                if expiry_dt > datetime.now(BEIJING_TZ).replace(tzinfo=None):
                    return True, level, expiry_dt.strftime("%Y-%m-%d %H:%M:%S")
            
            return False, "无会员", "已过期"
        except:
            return False, "无会员", "检查失败"
    
    def is_admin(self, user_id: int) -> bool:
        """检查用户是否为管理员"""
        # 检查配置文件中的管理员
        if user_id in config.ADMIN_IDS:
            return True
        
        # 检查数据库中的管理员
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            c.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            conn.close()
            return row is not None
        except:
            return False
    
    def add_admin(self, user_id: int, username: str, first_name: str, added_by: int) -> bool:
        """添加管理员"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
            
            c.execute("""
                INSERT OR REPLACE INTO admins 
                (user_id, username, first_name, added_by, added_time)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, username, first_name, added_by, now))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ 添加管理员失败: {e}")
            return False
    
    def remove_admin(self, user_id: int) -> bool:
        """移除管理员"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            c.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ 移除管理员失败: {e}")
            return False
    
    def get_all_admins(self) -> List[Tuple]:
        """获取所有管理员"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            
            # 获取数据库中的管理员
            c.execute("""
                SELECT user_id, username, first_name, added_time 
                FROM admins 
                ORDER BY added_time DESC
            """)
            db_admins = c.fetchall()
            conn.close()
            
            # 合并配置文件中的管理员
            all_admins = []
            
            # 添加配置文件管理员
            for admin_id in config.ADMIN_IDS:
                all_admins.append((admin_id, "配置文件管理员", "", "系统内置"))
            
            # 添加数据库管理员
            all_admins.extend(db_admins)
            
            return all_admins
        except Exception as e:
            print(f"❌ 获取管理员列表失败: {e}")
            return []
    
    def get_user_by_username(self, username: str) -> Optional[Tuple]:
        """根据用户名获取用户信息"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            username = username.replace("@", "")  # 移除@符号
            c.execute("SELECT user_id, username, first_name FROM users WHERE username = ?", (username,))
            row = c.fetchone()
            conn.close()
            return row
        except:
            return None
    
    def get_proxy_enabled(self) -> bool:
        """获取代理开关状态"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            c.execute("SELECT proxy_enabled FROM proxy_settings WHERE id = 1")
            row = c.fetchone()
            conn.close()
            
            if row:
                return bool(row[0])
            else:
                # 初始化默认设置
                self.set_proxy_enabled(True, None)
                return True
        except:
            return True  # 默认启用
    
    def set_proxy_enabled(self, enabled: bool, user_id: Optional[int]) -> bool:
        """设置代理开关状态"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
            
            c.execute("""
                INSERT OR REPLACE INTO proxy_settings 
                (id, proxy_enabled, updated_time, updated_by)
                VALUES (1, ?, ?, ?)
            """, (int(enabled), now, user_id))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ 设置代理开关失败: {e}")
            return False
    
    def grant_membership_days(self, user_id: int, days: int, level: str = "会员") -> bool:
        """授予用户会员（天数累加）"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            now = datetime.now(BEIJING_TZ)
            
            # 检查是否已有会员记录
            c.execute("SELECT expiry_time FROM memberships WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            
            if row and row[0]:
                # 已有到期时间，从到期时间继续累加
                try:
                    # Database stores naive datetime strings, compare with naive Beijing time
                    current_expiry = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                    # 如果到期时间在未来，从到期时间累加
                    if current_expiry > now.replace(tzinfo=None):
                        new_expiry = current_expiry + timedelta(days=days)
                    else:
                        # 已过期，从当前时间累加
                        new_expiry = now + timedelta(days=days)
                except:
                    new_expiry = now + timedelta(days=days)
            else:
                # 没有记录或没有到期时间，从当前时间累加
                new_expiry = now + timedelta(days=days)
            
            c.execute("""
                INSERT OR REPLACE INTO memberships 
                (user_id, level, expiry_time, created_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, level, new_expiry.strftime("%Y-%m-%d %H:%M:%S"), 
                  now.strftime("%Y-%m-%d %H:%M:%S")))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ 授予会员失败: {e}")
            return False
    
    def revoke_membership(self, user_id: int) -> bool:
        """撤销用户会员"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            c.execute("DELETE FROM memberships WHERE user_id = ?", (user_id,))
            rows_deleted = c.rowcount
            conn.commit()
            conn.close()
            return rows_deleted > 0
        except Exception as e:
            print(f"❌ 撤销会员失败: {e}")
            return False
    
    def redeem_code(self, user_id: int, code: str) -> Tuple[bool, str, int]:
        """兑换卡密"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            
            # 查询卡密
            c.execute("""
                SELECT code, level, days, status 
                FROM redeem_codes 
                WHERE code = ?
            """, (code.upper(),))
            row = c.fetchone()
            
            if not row:
                conn.close()
                return False, "卡密不存在", 0
            
            code_val, level, days, status = row
            
            # 检查状态
            if status == 'used':
                conn.close()
                return False, "卡密已被使用", 0
            elif status == 'expired':
                conn.close()
                return False, "卡密已过期", 0
            elif status != 'active':
                conn.close()
                return False, "卡密状态无效", 0
            
            # 标记为已使用
            now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("""
                UPDATE redeem_codes 
                SET status = 'used', redeemed_by = ?, redeemed_at = ?
                WHERE code = ?
            """, (user_id, now, code.upper()))
            
            conn.commit()
            conn.close()
            
            # 授予会员
            if self.grant_membership_days(user_id, days, level):
                return True, f"成功兑换{days}天{level}", days
            else:
                return False, "兑换失败，请联系管理员", 0
                
        except Exception as e:
            print(f"❌ 兑换卡密失败: {e}")
            return False, f"兑换失败: {str(e)}", 0
    
    def create_redeem_code(self, level: str, days: int, code: Optional[str], created_by: int) -> Tuple[bool, str, str]:
        """生成兑换码"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            
            # 如果没有提供code，自动生成
            if not code:
                # 生成8位大写字母数字组合
                while True:
                    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                    # 检查是否已存在
                    c.execute("SELECT code FROM redeem_codes WHERE code = ?", (code,))
                    if not c.fetchone():
                        break
            else:
                code = code.upper()[:10]  # 最多10位
                # 检查是否已存在
                c.execute("SELECT code FROM redeem_codes WHERE code = ?", (code,))
                if c.fetchone():
                    conn.close()
                    return False, code, "卡密已存在"
            
            # 插入卡密
            now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("""
                INSERT INTO redeem_codes 
                (code, level, days, status, created_by, created_at)
                VALUES (?, ?, ?, 'active', ?, ?)
            """, (code, level, days, created_by, now))
            
            conn.commit()
            conn.close()
            return True, code, "生成成功"
            
        except Exception as e:
            print(f"❌ 生成卡密失败: {e}")
            return False, "", f"生成失败: {str(e)}"
    
    def get_user_id_by_username(self, username: str) -> Optional[int]:
        """根据用户名获取用户ID"""
        user_info = self.get_user_by_username(username)
        if user_info:
            return user_info[0]  # user_id是第一个字段
        return None
    
    def get_user_statistics(self) -> Dict[str, Any]:
        """获取用户统计信息"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            
            # 总用户数
            c.execute("SELECT COUNT(*) FROM users")
            total_users = c.fetchone()[0]
            
            # 今日活跃用户
            today = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d')
            c.execute("SELECT COUNT(*) FROM users WHERE last_active LIKE ?", (f"{today}%",))
            today_active = c.fetchone()[0]
            
            # 本周活跃用户
            week_ago = (datetime.now(BEIJING_TZ) - timedelta(days=7)).strftime('%Y-%m-%d')
            c.execute("SELECT COUNT(*) FROM users WHERE last_active >= ?", (week_ago,))
            week_active = c.fetchone()[0]
            
            # 会员统计
            c.execute("SELECT COUNT(*) FROM memberships WHERE level = '体验会员'")
            trial_members = c.fetchone()[0]
            
            # 有效会员（未过期）
            now = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
            c.execute("SELECT COUNT(*) FROM memberships WHERE trial_expiry_time > ?", (now,))
            active_members = c.fetchone()[0]
            
            # 最近注册用户（7天内）
            c.execute("SELECT COUNT(*) FROM users WHERE register_time >= ?", (week_ago,))
            recent_users = c.fetchone()[0]
            
            conn.close()
            
            return {
                'total_users': total_users,
                'today_active': today_active,
                'week_active': week_active,
                'trial_members': trial_members,
                'active_members': active_members,
                'recent_users': recent_users
            }
        except Exception as e:
            print(f"❌ 获取用户统计失败: {e}")
            return {}

    def get_recent_users(self, limit: int = 20) -> List[Tuple]:
        """获取最近注册的用户"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            c.execute("""
                SELECT user_id, username, first_name, register_time, last_active, status
                FROM users 
                ORDER BY register_time DESC 
                LIMIT ?
            """, (limit,))
            result = c.fetchall()
            conn.close()
            return result
        except Exception as e:
            print(f"❌ 获取最近用户失败: {e}")
            return []

    def get_active_users(self, days: int = 7, limit: int = 50) -> List[Tuple]:
        """获取活跃用户"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            cutoff_date = (datetime.now(BEIJING_TZ) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
            c.execute("""
                SELECT user_id, username, first_name, register_time, last_active, status
                FROM users 
                WHERE last_active >= ?
                ORDER BY last_active DESC 
                LIMIT ?
            """, (cutoff_date, limit))
            result = c.fetchall()
            conn.close()
            return result
        except Exception as e:
            print(f"❌ 获取活跃用户失败: {e}")
            return []

    def search_user(self, query: str) -> List[Tuple]:
        """搜索用户（按ID、用户名、昵称）"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            
            # 尝试按用户ID搜索
            if query.isdigit():
                c.execute("""
                    SELECT user_id, username, first_name, register_time, last_active, status
                    FROM users 
                    WHERE user_id = ?
                """, (int(query),))
                result = c.fetchall()
                if result:
                    conn.close()
                    return result
            
            # 按用户名和昵称模糊搜索
            like_query = f"%{query}%"
            c.execute("""
                SELECT user_id, username, first_name, register_time, last_active, status
                FROM users 
                WHERE username LIKE ? OR first_name LIKE ?
                ORDER BY last_active DESC
                LIMIT 20
            """, (like_query, like_query))
            result = c.fetchall()
            conn.close()
            return result
        except Exception as e:
            print(f"❌ 搜索用户失败: {e}")
            return []

    def get_user_membership_info(self, user_id: int) -> Dict[str, Any]:
        """获取用户的详细会员信息"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            
            # 获取用户基本信息
            c.execute("SELECT username, first_name, register_time, last_active, status FROM users WHERE user_id = ?", (user_id,))
            user_info = c.fetchone()
            
            if not user_info:
                conn.close()
                return {}
            
            # 获取会员信息
            c.execute("SELECT level, trial_expiry_time, created_at FROM memberships WHERE user_id = ?", (user_id,))
            membership_info = c.fetchone()
            
            conn.close()
            
            result = {
                'user_id': user_id,
                'username': user_info[0] or '',
                'first_name': user_info[1] or '',
                'register_time': user_info[2] or '',
                'last_active': user_info[3] or '',
                'status': user_info[4] or '',
                'is_admin': self.is_admin(user_id)
            }
            
            if membership_info:
                result.update({
                    'membership_level': membership_info[0],
                    'expiry_time': membership_info[1],
                    'membership_created': membership_info[2]
                })
            else:
                result.update({
                    'membership_level': '无会员',
                    'expiry_time': '',
                    'membership_created': ''
                })
            
            return result
        except Exception as e:
            print(f"❌ 获取用户会员信息失败: {e}")
            return {}    
    def get_proxy_setting_info(self) -> Tuple[bool, str, Optional[int]]:
        """获取代理设置详细信息"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            c.execute("SELECT proxy_enabled, updated_time, updated_by FROM proxy_settings WHERE id = 1")
            row = c.fetchone()
            conn.close()
            
            if row:
                return bool(row[0]), row[1] or "未知", row[2]
            else:
                return True, "系统默认", None
        except:
            return True, "系统默认", None
    
    # ================================
    # 广播消息相关方法
    # ================================
    
    def get_target_users(self, target: str) -> List[int]:
        """获取目标用户列表"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            
            if target == "all":
                # 所有用户
                c.execute("SELECT user_id FROM users")
            elif target == "members":
                # 仅会员（有效会员）
                now = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
                c.execute("""
                    SELECT user_id FROM memberships 
                    WHERE trial_expiry_time > ?
                """, (now,))
            elif target == "active_7d":
                # 活跃用户（7天内）
                cutoff = (datetime.now(BEIJING_TZ) - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
                c.execute("""
                    SELECT user_id FROM users 
                    WHERE last_active >= ?
                """, (cutoff,))
            elif target == "new_7d":
                # 新用户（7天内）
                cutoff = (datetime.now(BEIJING_TZ) - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
                c.execute("""
                    SELECT user_id FROM users 
                    WHERE register_time >= ?
                """, (cutoff,))
            else:
                conn.close()
                return []
            
            result = [row[0] for row in c.fetchall()]
            conn.close()
            return result
        except Exception as e:
            print(f"❌ 获取目标用户失败: {e}")
            return []
    
    def insert_broadcast_record(self, title: str, content: str, buttons_json: str, 
                               target: str, created_by: int) -> Optional[int]:
        """插入广播记录并返回ID"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
            
            c.execute("""
                INSERT INTO broadcasts 
                (title, content, buttons_json, target, created_by, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """, (title, content, buttons_json, target, created_by, now))
            
            broadcast_id = c.lastrowid
            conn.commit()
            conn.close()
            return broadcast_id
        except Exception as e:
            print(f"❌ 插入广播记录失败: {e}")
            return None
    
    def update_broadcast_progress(self, broadcast_id: int, success: int, 
                                 failed: int, status: str, duration: float):
        """更新广播进度"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            
            c.execute("""
                UPDATE broadcasts 
                SET success = ?, failed = ?, status = ?, duration_sec = ?, total = ?
                WHERE id = ?
            """, (success, failed, status, duration, success + failed, broadcast_id))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ 更新广播进度失败: {e}")
            return False
    
    def add_broadcast_log(self, broadcast_id: int, user_id: int, 
                         status: str, error: Optional[str] = None):
        """添加广播日志"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
            
            c.execute("""
                INSERT INTO broadcast_logs 
                (broadcast_id, user_id, status, error, sent_at)
                VALUES (?, ?, ?, ?, ?)
            """, (broadcast_id, user_id, status, error, now))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ 添加广播日志失败: {e}")
            return False
    
    def get_broadcast_history(self, limit: int = 10) -> List[Tuple]:
        """获取广播历史记录"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            
            c.execute("""
                SELECT id, title, target, created_at, status, total, success, failed
                FROM broadcasts 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (limit,))
            
            result = c.fetchall()
            conn.close()
            return result
        except Exception as e:
            print(f"❌ 获取广播历史失败: {e}")
            return []
    
    def get_broadcast_detail(self, broadcast_id: int) -> Optional[Dict[str, Any]]:
        """获取广播详情"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            
            c.execute("""
                SELECT id, title, content, buttons_json, target, created_by, 
                       created_at, status, total, success, failed, duration_sec
                FROM broadcasts 
                WHERE id = ?
            """, (broadcast_id,))
            
            row = c.fetchone()
            if not row:
                conn.close()
                return None
            
            result = {
                'id': row[0],
                'title': row[1],
                'content': row[2],
                'buttons_json': row[3],
                'target': row[4],
                'created_by': row[5],
                'created_at': row[6],
                'status': row[7],
                'total': row[8],
                'success': row[9],
                'failed': row[10],
                'duration_sec': row[11]
            }
            
            conn.close()
            return result
        except Exception as e:
            print(f"❌ 获取广播详情失败: {e}")
            return None
    
    
    def insert_forget_2fa_log(self, batch_id: str, account_name: str, phone: str,
                              file_type: str, proxy_used: str, status: str,
                              error: str = "", cooling_until: str = "", elapsed: float = 0.0):
        """插入忘记2FA日志"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
            
            c.execute("""
                INSERT INTO forget_2fa_logs 
                (batch_id, account_name, phone, file_type, proxy_used, status, error, cooling_until, elapsed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                batch_id,
                account_name,
                phone,
                file_type,
                proxy_used,
                status,
                error,
                cooling_until,
                elapsed,
                now
            ))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ 插入忘记2FA日志失败: {e}")
            return False
    
    def get_daily_creation_count(self, phone: str) -> int:
        """获取今日创建数量"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            today = datetime.now(BEIJING_TZ).date()
            c.execute("""
                SELECT COUNT(*) FROM batch_creations 
                WHERE phone = ? AND date = ?
            """, (phone, today.strftime("%Y-%m-%d")))
            count = c.fetchone()[0]
            conn.close()
            return count
        except Exception as e:
            print(f"❌ 查询今日创建数量失败: {e}")
            return 0
    
    def record_creation(self, phone: str, creation_type: str, name: str, invite_link: str = None, 
                       username: str = None, creator_id: int = None):
        """记录创建记录"""
        try:
            conn = sqlite3.connect(self.db_name)
            c = conn.cursor()
            now = datetime.now(BEIJING_TZ)
            c.execute("""
                INSERT INTO batch_creations 
                (phone, creation_type, name, username, invite_link, creator_id, created_at, date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                phone,
                creation_type,
                name,
                username,
                invite_link,
                creator_id,
                now.strftime("%Y-%m-%d %H:%M:%S"),
                now.strftime("%Y-%m-%d")
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ 记录创建失败: {e}")
            return False
