"""
core.config - Configuration class for the Telegram account detection bot.
"""

import os


class Config:
    def __init__(self):
        self.TOKEN = os.getenv("TOKEN") or os.getenv("BOT_TOKEN")
        self.API_ID = int(os.getenv("API_ID", "0"))
        # Ensure API_HASH is always a string to prevent TypeError in Telethon
        self.API_HASH = str(os.getenv("API_HASH", ""))

        admin_ids = os.getenv("ADMIN_IDS", "")
        self.ADMIN_IDS = []
        if admin_ids:
            try:
                self.ADMIN_IDS = [int(x.strip()) for x in admin_ids.split(",") if x.strip()]
            except:
                pass

        self.TRIAL_DURATION = int(os.getenv("TRIAL_DURATION", "30"))
        self.TRIAL_DURATION_UNIT = os.getenv("TRIAL_DURATION_UNIT", "minutes")

        if self.TRIAL_DURATION_UNIT == "minutes":
            self.TRIAL_DURATION_SECONDS = self.TRIAL_DURATION * 60
        else:
            self.TRIAL_DURATION_SECONDS = self.TRIAL_DURATION

        self.DB_NAME = "bot_data.db"
        self.MAX_CONCURRENT_CHECKS = int(os.getenv("MAX_CONCURRENT_CHECKS", "20"))
        self.CHECK_TIMEOUT = int(os.getenv("CHECK_TIMEOUT", "15"))
        self.SPAMBOT_WAIT_TIME = float(os.getenv("SPAMBOT_WAIT_TIME", "2.0"))

        # 账号处理速度优化配置（带验证）
        self.MAX_CONCURRENT = max(1, min(50, int(os.getenv("MAX_CONCURRENT", "15"))))  # 限制在1-50之间
        self.DELAY_BETWEEN_ACCOUNTS = max(0.1, min(10.0, float(os.getenv("DELAY_BETWEEN_ACCOUNTS", "0.3"))))  # 限制在0.1-10秒之间
        self.CONNECTION_TIMEOUT = max(5, min(60, int(os.getenv("CONNECTION_TIMEOUT", "10"))))  # 限制在5-60秒之间

        # 代理配置
        self.USE_PROXY = os.getenv("USE_PROXY", "true").lower() == "true"
        self.PROXY_TIMEOUT = int(os.getenv("PROXY_TIMEOUT", "10"))
        self.PROXY_FILE = os.getenv("PROXY_FILE", "proxy.txt")

        # 住宅代理配置
        self.RESIDENTIAL_PROXY_TIMEOUT = int(os.getenv("RESIDENTIAL_PROXY_TIMEOUT", "30"))
        self.RESIDENTIAL_PROXY_PATTERNS = os.getenv(
            "RESIDENTIAL_PROXY_PATTERNS",
            "abcproxy,residential,resi,mobile"
        ).split(",")
        # 新增：对外访问的基础地址，用于生成验证码网页链接
        # 例如: http://45.147.196.113:5000 或 https://your.domain
        self.BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000")
        print(f"🌐 验证码网页 BASE_URL: {self.BASE_URL}")
        # 新增速度优化配置
        self.PROXY_CHECK_CONCURRENT = int(os.getenv("PROXY_CHECK_CONCURRENT", "100"))
        self.PROXY_CHECK_TIMEOUT = int(os.getenv("PROXY_CHECK_TIMEOUT", "3"))
        self.PROXY_AUTO_CLEANUP = os.getenv("PROXY_AUTO_CLEANUP", "true").lower() == "true"
        self.PROXY_FAST_MODE = os.getenv("PROXY_FAST_MODE", "true").lower() == "true"
        self.PROXY_RETRY_COUNT = int(os.getenv("PROXY_RETRY_COUNT", "2"))
        self.PROXY_BATCH_SIZE = int(os.getenv("PROXY_BATCH_SIZE", "100"))
        self.PROXY_USAGE_LOG_LIMIT = int(os.getenv("PROXY_USAGE_LOG_LIMIT", "500"))
        self.PROXY_ROTATE_RETRIES = int(os.getenv("PROXY_ROTATE_RETRIES", "2"))
        self.PROXY_SHOW_FAILURE_REASON = os.getenv("PROXY_SHOW_FAILURE_REASON", "true").lower() == "true"
        self.PROXY_DEBUG_VERBOSE = os.getenv("PROXY_DEBUG_VERBOSE", "false").lower() == "true"

        # 忘记2FA批量处理速度优化配置
        self.FORGET2FA_CONCURRENT = int(os.getenv("FORGET2FA_CONCURRENT", "50"))  # 并发数50（高速处理）
        self.FORGET2FA_MIN_DELAY = float(os.getenv("FORGET2FA_MIN_DELAY", "3.0"))  # 批次间最小延迟3秒
        self.FORGET2FA_MAX_DELAY = float(os.getenv("FORGET2FA_MAX_DELAY", "6.0"))  # 批次间最大延迟6秒
        self.FORGET2FA_NOTIFY_WAIT = float(os.getenv("FORGET2FA_NOTIFY_WAIT", "0.5"))  # 等待通知到达的时间（秒）
        self.FORGET2FA_MAX_PROXY_RETRIES = int(os.getenv("FORGET2FA_MAX_PROXY_RETRIES", "3"))  # 代理重试次数3次
        self.FORGET2FA_PROXY_TIMEOUT = int(os.getenv("FORGET2FA_PROXY_TIMEOUT", "10"))  # 代理超时时间10秒
        self.FORGET2FA_DEFAULT_COUNTRY_PREFIX = os.getenv("FORGET2FA_DEFAULT_COUNTRY_PREFIX", "+62")  # 默认国家前缀

        # API格式转换器和验证码服务器配置
        self.WEB_SERVER_PORT = int(os.getenv("WEB_SERVER_PORT", "8080"))
        self.ALLOW_PORT_SHIFT = os.getenv("ALLOW_PORT_SHIFT", "true").lower() == "true"

        # 一键清理功能配置
        self.ENABLE_ONE_CLICK_CLEANUP = os.getenv("ENABLE_ONE_CLICK_CLEANUP", "true").lower() == "true"
        self.CLEANUP_ACCOUNT_CONCURRENCY = int(os.getenv("CLEANUP_ACCOUNT_CONCURRENCY", "30"))  # 同时处理的账户数（改为30）
        self.CLEANUP_LEAVE_CONCURRENCY = int(os.getenv("CLEANUP_LEAVE_CONCURRENCY", "3"))
        self.CLEANUP_DELETE_HISTORY_CONCURRENCY = int(os.getenv("CLEANUP_DELETE_HISTORY_CONCURRENCY", "2"))
        self.CLEANUP_DELETE_CONTACTS_CONCURRENCY = int(os.getenv("CLEANUP_DELETE_CONTACTS_CONCURRENCY", "3"))
        self.CLEANUP_ACTION_SLEEP = float(os.getenv("CLEANUP_ACTION_SLEEP", "0.3"))
        self.CLEANUP_MIN_PEER_INTERVAL = float(os.getenv("CLEANUP_MIN_PEER_INTERVAL", "1.5"))
        self.CLEANUP_REVOKE_DEFAULT = os.getenv("CLEANUP_REVOKE_DEFAULT", "true").lower() == "true"

        # 批量创建功能配置
        self.ENABLE_BATCH_CREATE = os.getenv("ENABLE_BATCH_CREATE", "true").lower() == "true"
        self.BATCH_CREATE_DAILY_LIMIT = int(os.getenv("BATCH_CREATE_DAILY_LIMIT", "10"))  # 每个账号每日创建上限
        self.BATCH_CREATE_CONCURRENT = int(os.getenv("BATCH_CREATE_CONCURRENT", "10"))  # 同时处理的账户数

        # 重新授权功能配置
        self.ENABLE_REAUTHORIZE = os.getenv("ENABLE_REAUTHORIZE", "true").lower() == "true"
        self.REAUTH_CONCURRENT = int(os.getenv("REAUTH_CONCURRENT", "30"))  # 同时处理的账户数（默认30）
        self.REAUTH_USE_RANDOM_DEVICE = os.getenv("REAUTH_USE_RANDOM_DEVICE", "true").lower() == "true"  # 使用随机设备参数
        self.REAUTH_FORCE_PROXY = os.getenv("REAUTH_FORCE_PROXY", "true").lower() == "true"  # 强制使用代理
        self.BATCH_CREATE_MIN_INTERVAL = int(os.getenv("BATCH_CREATE_MIN_INTERVAL", "60"))  # 创建间隔最小秒数
        self.BATCH_CREATE_MAX_INTERVAL = int(os.getenv("BATCH_CREATE_MAX_INTERVAL", "120"))  # 创建间隔最大秒数
        self.BATCH_CREATE_MAX_FLOOD_WAIT = int(os.getenv("BATCH_CREATE_MAX_FLOOD_WAIT", "60"))  # 最大可接受的flood等待时间（秒）

        # 获取项目根目录（core/ 的父目录，等同于原 tdata.py 所在目录）
        self.SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 文件管理配置
        self.RESULTS_DIR = os.getenv("RESULTS_DIR") or os.path.join(self.SCRIPT_DIR, "results")
        self.UPLOADS_DIR = os.getenv("UPLOAD_DIR") or os.path.join(self.SCRIPT_DIR, "uploads")
        self.CLEANUP_REPORTS_DIR = os.path.join(self.RESULTS_DIR, "cleanup_reports")

        # Session文件目录结构
        # sessions:  存放用户上传的session文件
        # sessions/sessions_bak: 存放临时处理文件
        self.SESSIONS_DIR = os.getenv("SESSION_DIR") or os.path.join(self.SCRIPT_DIR, "sessions")
        self.SESSIONS_BAK_DIR = os.path.join(self.SESSIONS_DIR, "sessions_bak")
        # 创建目录
        os.makedirs(self.RESULTS_DIR, exist_ok=True)
        os.makedirs(self.UPLOADS_DIR, exist_ok=True)
        os.makedirs(self.CLEANUP_REPORTS_DIR, exist_ok=True)
        os.makedirs(self.SESSIONS_DIR, exist_ok=True)
        os.makedirs(self.SESSIONS_BAK_DIR, exist_ok=True)

        print(f"📁 上传目录: {self.UPLOADS_DIR}")
        print(f"📁 结果目录: {self.RESULTS_DIR}")
        print(f"📁 清理报告目录: {self.CLEANUP_REPORTS_DIR}")
        print(f"📁 Session目录: {self.SESSIONS_DIR}")
        print(f"📁 临时文件目录: {self.SESSIONS_BAK_DIR}")
        print(f"📡 系统配置: USE_PROXY={'true' if self.USE_PROXY else 'false'}")
        print(f"🧹 一键清理: {'启用' if self.ENABLE_ONE_CLICK_CLEANUP else '禁用'}")
        print(f"📦 批量创建: {'启用' if self.ENABLE_BATCH_CREATE else '禁用'}，每日限制: {self.BATCH_CREATE_DAILY_LIMIT}")
        print(f"⏱️ 创建间隔: {self.BATCH_CREATE_MIN_INTERVAL}-{self.BATCH_CREATE_MAX_INTERVAL}秒（避免频率限制）")
        print(f"🔄 重新授权: {'启用' if self.ENABLE_REAUTHORIZE else '禁用'}，并发数: {self.REAUTH_CONCURRENT}，随机设备: {'开启' if self.REAUTH_USE_RANDOM_DEVICE else '关闭'}，强制代理: {'开启' if self.REAUTH_FORCE_PROXY else '关闭'}")
        print(f"💡 注意: 实际代理模式需要配置文件+数据库开关+有效代理文件同时满足")

    def validate(self):
        if not self.TOKEN or not self.API_ID or not self.API_HASH:
            self.create_env_file()
            return False
        return True

    def create_env_file(self):
        if not os.path.exists(".env"):
            env_content = """TOKEN=YOUR_BOT_TOKEN_HERE
API_ID=YOUR_API_ID_HERE
API_HASH=YOUR_API_HASH_HERE
ADMIN_IDS=123456789
TRIAL_DURATION=30
TRIAL_DURATION_UNIT=minutes
MAX_CONCURRENT_CHECKS=20
CHECK_TIMEOUT=15
SPAMBOT_WAIT_TIME=2.0
# 账号处理速度优化配置
MAX_CONCURRENT=15  # 并发账号处理数：从3提高到15
DELAY_BETWEEN_ACCOUNTS=0.3  # 账号间隔：从2秒减少到0.3秒
CONNECTION_TIMEOUT=10  # 连接超时：从30秒减少到10秒
USE_PROXY=true
PROXY_TIMEOUT=10
PROXY_FILE=proxy.txt
RESIDENTIAL_PROXY_TIMEOUT=30
RESIDENTIAL_PROXY_PATTERNS=abcproxy,residential,resi,mobile
PROXY_CHECK_CONCURRENT=100
PROXY_CHECK_TIMEOUT=3
PROXY_AUTO_CLEANUP=true
PROXY_FAST_MODE=true
PROXY_RETRY_COUNT=2
PROXY_BATCH_SIZE=100
PROXY_ROTATE_RETRIES=2
PROXY_SHOW_FAILURE_REASON=true
PROXY_USAGE_LOG_LIMIT=500
PROXY_DEBUG_VERBOSE=false
BASE_URL=http://127.0.0.1:5000
# 忘记2FA批量处理速度优化配置
FORGET2FA_CONCURRENT=50
FORGET2FA_MIN_DELAY=3.0
FORGET2FA_MAX_DELAY=6.0
FORGET2FA_NOTIFY_WAIT=0.5
FORGET2FA_MAX_PROXY_RETRIES=3
FORGET2FA_PROXY_TIMEOUT=10
FORGET2FA_DEFAULT_COUNTRY_PREFIX=+62
# API格式转换器和验证码服务器配置
WEB_SERVER_PORT=8080
ALLOW_PORT_SHIFT=true
# 一键清理功能配置
ENABLE_ONE_CLICK_CLEANUP=true
CLEANUP_ACCOUNT_CONCURRENCY=3  # 同时处理的账户数量（提升清理速度）
CLEANUP_LEAVE_CONCURRENCY=3
CLEANUP_DELETE_HISTORY_CONCURRENCY=2
CLEANUP_DELETE_CONTACTS_CONCURRENCY=3
CLEANUP_ACTION_SLEEP=0.3
CLEANUP_MIN_PEER_INTERVAL=1.5
CLEANUP_REVOKE_DEFAULT=true
# 批量创建功能配置
ENABLE_BATCH_CREATE=true
BATCH_CREATE_DAILY_LIMIT=10  # 每个账号每日创建上限
BATCH_CREATE_CONCURRENT=10  # 同时处理的账户数
BATCH_CREATE_MIN_INTERVAL=60  # 创建间隔最小秒数（每个账号内）
BATCH_CREATE_MAX_INTERVAL=120  # 创建间隔最大秒数（每个账号内）
BATCH_CREATE_MAX_FLOOD_WAIT=60  # 最大可接受的flood等待时间（秒）
# 重新授权功能配置
ENABLE_REAUTHORIZE=true
REAUTH_CONCURRENT=30  # 同时处理的账户数（默认30）
REAUTH_USE_RANDOM_DEVICE=true  # 使用随机设备参数
REAUTH_FORCE_PROXY=true  # 强制使用代理
"""
            with open(".env", "w", encoding="utf-8") as f:
                f.write(env_content)
            print("✅ 已创建.env配置文件，请填入正确的配置信息")
