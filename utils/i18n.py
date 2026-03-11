"""
utils.i18n - Internationalization helper re-exports
"""
from i18n import get_text as t, set_user_language, get_user_language

__all__ = ['t', 'set_user_language', 'get_user_language', 'get_profile_error_message']

# 错误类型映射（用于资料修改）- 映射到翻译键
ERROR_TYPE_TO_TRANSLATION_KEY = {
    'UserDeactivatedBanError': 'profile_error_banned',
    'UserDeactivatedError': 'profile_error_deactivated',
    'AuthKeyUnregisteredError': 'profile_error_auth_expired',
    'UsernameOccupiedError': 'profile_error_username_taken',
    'UsernameInvalidError': 'profile_error_username_invalid',
    'FloodWaitError': 'profile_error_flood',
    'TimeoutError': 'profile_error_timeout',
    'ConnectionError': 'profile_error_network',
    'RPCError': 'profile_error_rpc_error',
    'SessionPasswordNeededError': 'profile_error_password_needed',
    'PhoneNumberBannedError': 'profile_error_phone_banned',
}


def get_profile_error_message(user_id, error_type, fallback=None):
    """根据用户语言获取错误消息"""
    if error_type in ERROR_TYPE_TO_TRANSLATION_KEY:
        return t(user_id, ERROR_TYPE_TO_TRANSLATION_KEY[error_type])
    return fallback if fallback else t(user_id, 'profile_error_unknown')
