"""
tdata_legacy - Backward compatibility re-exports.

This module re-exports all public symbols from the new modular structure so
that any code that previously did ``import tdata`` or ``from tdata import …``
continues to work without modification.

.. deprecated::
    All symbols in this module are available from their canonical locations
    in the new modular packages.  Direct imports from those packages are
    preferred and this shim will be removed in a future major release.
"""
import warnings

warnings.warn(
    "Importing from tdata_legacy (formerly tdata.py) is deprecated. "
    "Please update your imports to use the new modular structure. "
    "See ARCHITECTURE.md for the mapping.",
    DeprecationWarning,
    stacklevel=2,
)

# ---------------------------------------------------------------------------
# core
# ---------------------------------------------------------------------------
from core.constants import (  # noqa: F401, E402
    BEIJING_TZ,
    COOLDOWN_THRESHOLD_SECONDS,
    TEST_CONTACT_PHONES,
    CONTACT_CHECK_MAX_CONCURRENT,
    CONTACT_CHECK_DELAY_BETWEEN,
    SINGLE_ACCOUNT_TIMEOUT,
    BATCH_TIMEOUT,
    UPDATE_INTERVAL,
    CLEANUP_UPDATE_INTERVAL,
    TDATA_CONVERT_TIMEOUT,
    CLEANUP_SINGLE_ACCOUNT_TIMEOUT,
    CLEANUP_OPERATION_TIMEOUT,
    TDATA_PIPELINE_CONVERT_CONCURRENT,
    TDATA_PIPELINE_CHECK_CONCURRENT,
    TDATA_PIPELINE_CONVERT_TIMEOUT,
    PROGRESS_UPDATE_INTERVAL,
    PROGRESS_UPDATE_MIN_PERCENT,
    PROGRESS_UPDATE_MIN_PERCENT_LARGE,
    PROGRESS_LARGE_BATCH_THRESHOLD,
    CONTACT_STATUS_NORMAL,
    CONTACT_STATUS_LIMITED,
    CONTACT_STATUS_BANNED,
    CONTACT_STATUS_ERROR,
    CONTACT_STATUS_UNAUTHORIZED,
)
from core.config import Config  # noqa: F401, E402
from core.database import Database  # noqa: F401, E402

# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------
from models.dataclasses import (  # noqa: F401, E402
    CleanupAction,
    ProfileUpdateConfig,
    ProxyUsageRecord,
    BatchCreationConfig,
    BatchCreationResult,
    BatchAccountInfo,
)

# ---------------------------------------------------------------------------
# managers
# ---------------------------------------------------------------------------
from managers.proxy_manager import ProxyManager  # noqa: F401, E402
from managers.device_params import DeviceParamsManager, DeviceParamsLoader  # noqa: F401, E402
from managers.profile_manager import ProfileManager  # noqa: F401, E402
from managers.file_processor import FileProcessor  # noqa: F401, E402

# ---------------------------------------------------------------------------
# testers / detectors
# ---------------------------------------------------------------------------
from testers.proxy_tester import ProxyTester, ProxyRotator  # noqa: F401, E402
from detectors.password_detector import PasswordDetector  # noqa: F401, E402

# ---------------------------------------------------------------------------
# services
# ---------------------------------------------------------------------------
from services.spambot_checker import SpamBotChecker  # noqa: F401, E402
from services.format_converter import FormatConverter  # noqa: F401, E402
from services.two_factor_manager import TwoFactorManager  # noqa: F401, E402
from services.api_converter import APIFormatConverter  # noqa: F401, E402
from services.forget_2fa_manager import Forget2FAManager  # noqa: F401, E402
from services.batch_creator import BatchCreatorService  # noqa: F401, E402

# ---------------------------------------------------------------------------
# utils
# ---------------------------------------------------------------------------
from utils.helpers import (  # noqa: F401, E402
    generate_progress_bar,
    format_time,
    get_back_to_menu_keyboard,
    extract_phone_from_path,
    extract_phone_from_tdata_path,
    scan_tdata_accounts,
    copy_session_to_temp,
    cleanup_temp_session,
    process_accounts_with_dedup,
    deduplicate_accounts_by_phone,
    create_zip_with_unique_paths,
    normalize_phone,
    utc_to_beijing,
    _find_available_port,
)
from utils.validators import detect_tdata_structure, is_valid_tdata  # noqa: F401, E402
from utils.async_helpers import (  # noqa: F401, E402
    safe_process_with_retry,
    safe_process_session,
    batch_convert_tdata_to_session,
    batch_update_profiles_concurrent,
)
try:
    from utils.i18n import get_profile_error_message  # noqa: F401, E402
    from i18n import get_text as t, set_user_language, get_user_language  # noqa: F401, E402
except ImportError:
    pass

# ---------------------------------------------------------------------------
# bot
# ---------------------------------------------------------------------------
from bot.main import EnhancedBot, setup_session_directory, create_sample_proxy_file  # noqa: F401, E402
