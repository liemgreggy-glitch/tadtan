# Architecture — Tadtan Modular Structure

## Overview

`tdata.py` was a single 29,208-line monolith containing 30+ classes and all
bot logic.  It has been refactored into a layered module hierarchy following
SOLID principles.

---

## Module Dependency Graph

```
main.py
  └── bot/main.py (EnhancedBot)
        ├── core/config.py         (Config)
        ├── core/database.py       (Database)
        │     └── core/constants.py
        ├── managers/proxy_manager.py   (ProxyManager)
        ├── managers/device_params.py   (DeviceParamsManager, DeviceParamsLoader)
        ├── managers/profile_manager.py (ProfileManager)
        ├── managers/file_processor.py  (FileProcessor)
        │     └── services/spambot_checker.py
        ├── testers/proxy_tester.py     (ProxyTester, ProxyRotator)
        ├── detectors/password_detector.py (PasswordDetector)
        ├── services/spambot_checker.py  (SpamBotChecker)
        │     ├── core/constants.py
        │     ├── managers/proxy_manager.py
        │     └── models/dataclasses.py  (ProxyUsageRecord)
        ├── services/format_converter.py (FormatConverter)
        │     └── services/forget_2fa_manager.py
        ├── services/two_factor_manager.py (TwoFactorManager)
        │     ├── core/constants.py
        │     └── services/format_converter.py
        ├── services/api_converter.py  (APIFormatConverter)
        │     └── detectors/password_detector.py
        ├── services/forget_2fa_manager.py (Forget2FAManager)
        │     ├── managers/proxy_manager.py
        │     └── testers/proxy_tester.py
        ├── services/batch_creator.py  (BatchCreatorService)
        │     └── models/dataclasses.py
        └── utils/
              ├── helpers.py
              ├── validators.py
              ├── async_helpers.py
              └── i18n.py
```

---

## Layer Descriptions

### `core/`
Pure infrastructure with no dependencies on other project modules.

| File | Purpose |
|---|---|
| `constants.py` | All magic numbers, timeouts, status code strings |
| `config.py` | `Config` — reads `.env` via `os.getenv`, creates runtime dirs |
| `database.py` | `Database` — all SQLite operations (users, memberships, broadcasts, logs) |

### `models/`
Pure data containers — no I/O, no logic, no external dependencies.

| Class | Description |
|---|---|
| `CleanupAction` | Record of a single cleanup operation on a chat |
| `ProfileUpdateConfig` | Settings for a bulk profile-update run |
| `ProxyUsageRecord` | Result of one proxy connection attempt |
| `BatchCreationConfig` | Parameters for a batch group/channel creation job |
| `BatchCreationResult` | Outcome of one item in a batch creation job |
| `BatchAccountInfo` | Per-account info collected during batch creation |

### `managers/`
Stateful resource managers; depend only on `core` and `models`.

| File | Class(es) | Responsibility |
|---|---|---|
| `proxy_manager.py` | `ProxyManager` | Load, parse, rotate, track proxies |
| `device_params.py` | `DeviceParamsManager`, `DeviceParamsLoader` | Random device fingerprints |
| `profile_manager.py` | `ProfileManager` | Bulk profile updates (name/bio/photo/username) |
| `file_processor.py` | `FileProcessor` | Scan ZIPs, dedup accounts, run bulk checks |

### `testers/` and `detectors/`
Single-purpose utility classes.

| File | Class | Responsibility |
|---|---|---|
| `testers/proxy_tester.py` | `ProxyTester`, `ProxyRotator` | Test proxy connectivity and speed |
| `detectors/password_detector.py` | `PasswordDetector` | Auto-detect 2FA passwords in session/ZIP files |

### `services/`
Business-logic services; may depend on `core`, `models`, `managers`, `testers`, and `detectors`.

| File | Class | Responsibility |
|---|---|---|
| `spambot_checker.py` | `SpamBotChecker` | Bulk SpamBot status detection |
| `format_converter.py` | `FormatConverter` | TData ↔ Session conversion |
| `two_factor_manager.py` | `TwoFactorManager` | Add / change / remove 2FA passwords |
| `api_converter.py` | `APIFormatConverter` | API-mode conversion + Flask verification server |
| `forget_2fa_manager.py` | `Forget2FAManager` | Trigger official Telegram password-reset flow |
| `batch_creator.py` | `BatchCreatorService` | Bulk group/channel creation |

### `utils/`
Stateless helper functions grouped by concern.

| File | Key symbols |
|---|---|
| `helpers.py` | `generate_progress_bar`, `format_time`, `extract_phone_from_path`, `scan_tdata_accounts`, `copy_session_to_temp`, `normalize_phone`, `utc_to_beijing` |
| `validators.py` | `is_valid_tdata`, `detect_tdata_structure` |
| `async_helpers.py` | `safe_process_with_retry`, `batch_convert_tdata_to_session`, `batch_update_profiles_concurrent` |
| `i18n.py` | `t`, `get_user_language`, `set_user_language`, `get_profile_error_message` |

### `bot/`
Telegram bot layer; depends on all layers above.

| File | Purpose |
|---|---|
| `bot/main.py` | `EnhancedBot` class — all command/callback/file/admin handlers, plus `run_bot()` |
| `bot/handlers/` | Reserved for future handler split-out by feature group |

---

## Backward Compatibility

`tdata_legacy.py` re-exports every public symbol from its canonical new
location.  Code that previously did:

```python
from tdata import ProxyManager, Database, Config
```

can be updated gradually to:

```python
from managers.proxy_manager import ProxyManager
from core.database import Database
from core.config import Config
```

The legacy shim emits a `DeprecationWarning` at import time and will be
removed in a future major release.

---

## Design Principles

1. **No circular imports** — dependency arrows flow in one direction:
   `bot → services → managers → core/models`
2. **Optional third-party libraries** — every `import telethon / opentele /
   faker / flask / socks` is wrapped in `try/except ImportError` so modules
   can be imported for inspection without all dependencies installed.
3. **No business logic changes** — all code was moved verbatim; only the file
   layout changed.
4. **Type hints & docstrings** — added to newly created wrapper files;
   preserved as-is in extracted code.
