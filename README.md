# Tadtan — Telegram Account Management Bot

A powerful Telegram bot for bulk management of Telegram accounts, supporting
format conversion, 2FA management, proxy rotation, account classification,
profile updates, spam-bot checking, and more.

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env   # edit TOKEN, API_ID, API_HASH, ADMIN_IDS

# 3. Start the bot
python main.py
```

---

## 📁 Project Structure

```
tadtan/
├── main.py                    # Application entry point
├── tdata.py                   # Original monolith (kept as reference)
├── tdata_legacy.py            # Backward-compatibility re-export shim
│
├── core/                      # Core infrastructure
│   ├── constants.py           # All constants, thresholds, status codes
│   ├── config.py              # Config class (reads .env)
│   └── database.py            # Database class (SQLite)
│
├── models/                    # Pure data structures
│   └── dataclasses.py         # CleanupAction, ProfileUpdateConfig,
│                              # ProxyUsageRecord, BatchCreation* dataclasses
│
├── managers/                  # Resource managers
│   ├── proxy_manager.py       # ProxyManager
│   ├── device_params.py       # DeviceParamsManager, DeviceParamsLoader
│   ├── profile_manager.py     # ProfileManager
│   └── file_processor.py      # FileProcessor
│
├── services/                  # Business-logic services
│   ├── spambot_checker.py     # SpamBotChecker
│   ├── format_converter.py    # FormatConverter (TData ↔ Session)
│   ├── two_factor_manager.py  # TwoFactorManager
│   ├── api_converter.py       # APIFormatConverter (Flask verification server)
│   ├── forget_2fa_manager.py  # Forget2FAManager
│   └── batch_creator.py       # BatchCreatorService
│
├── testers/
│   └── proxy_tester.py        # ProxyTester, ProxyRotator
│
├── detectors/
│   └── password_detector.py   # PasswordDetector
│
├── utils/                     # Stateless utility functions
│   ├── helpers.py             # Progress bar, time formatting, file helpers
│   ├── validators.py          # TData structure validation
│   ├── async_helpers.py       # Async retry / batch conversion helpers
│   └── i18n.py               # Internationalization helpers
│
├── bot/                       # Telegram bot
│   ├── main.py                # EnhancedBot class + run_bot()
│   └── handlers/              # (future) per-feature handler split
│
└── i18n/                      # Translation files
    ├── zh.py                  # Chinese
    ├── en.py                  # English
    └── ru.py                  # Russian
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full module dependency graph and
design rationale.

---

## ⚙️ Configuration

All configuration is via environment variables (`.env` file):

| Variable | Default | Description |
|---|---|---|
| `TOKEN` | — | Bot token from @BotFather |
| `API_ID` | — | Telegram API ID |
| `API_HASH` | — | Telegram API Hash |
| `ADMIN_IDS` | — | Comma-separated admin user IDs |
| `USE_PROXY` | `true` | Enable proxy usage |
| `PROXY_FILE` | `proxy.txt` | Proxy list file |
| `MAX_CONCURRENT` | `15` | Concurrent account processing limit |
| `BASE_URL` | `http://127.0.0.1:5000` | Public URL for verification pages |

---

## 🔑 Features

- **SpamBot checking** — bulk check accounts against @SpamBot
- **Format conversion** — TData ↔ Session conversion
- **2FA management** — add/change/remove/forget 2FA passwords
- **Profile updates** — bulk name, bio, avatar, username changes
- **Proxy management** — load, test, rotate, and clean proxies
- **Account classification** — sort by country or split by count
- **Batch group/channel creation** — create Telegram groups or channels in bulk
- **Account cleanup** — delete chats, contacts, and leave groups
- **VIP/membership system** — subscription management with redeem codes
- **Admin panel** — user management, broadcast system, payment stats
- **Multi-language** — Chinese, English, Russian
