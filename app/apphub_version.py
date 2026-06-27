# AppHub Version Configuration
# Update this file when you release a new version of AppHub

VERSION = "12.0.0"
BUILD_NUMBER = 12
# Force all builds older than the current release to update.
MIN_SUPPORTED_BUILD = BUILD_NUMBER
RELEASE_DATE = "2026-06-28"

# File Information
DOWNLOAD_URLS = {
    "arm64-v8a": "http://apphubx.store/apphub/app/app-arm64-v8a-release.apk",
    "armeabi-v7a": "http://apphubx.store/apphub/app/app-armeabi-v7a-release.apk",
    "x86": "",
    "x86_64": "http://apphubx.store/apphub/app/app-x86_64-release.apk",
    "universal": ""
}
DOWNLOAD_SIZES = {
    "arm64-v8a": 32000000,
    "armeabi-v7a": 37000000,
    "x86": 23000000,
    "x86_64": 23000000,
    "universal": 40000000
}
DOWNLOAD_URL = DOWNLOAD_URLS["universal"]
APK_HASH = ""  # Example SHA-256 Hash for download integrity verification
SIZE_BYTES = DOWNLOAD_SIZES["universal"]

# Update Enforcement
IS_MANDATORY = False  # If True, prompts an update regardless of MIN_SUPPORTED_BUILD

# Telegram Support
TELEGRAM_CHANNEL = "https://t.me/+IDEuHZyD9lc5Y2Jl"

# Changelog Details
CHANGELOG_TITLE = "🎉 What's New in v12.0.0"
CHANGELOG = """
✨ Major Features
• Reduced app size.
• Added 11+ adult website support.
• Cache set to 1 hour.
• Removed non-working playlists.
• Removed storage permission for Android 13+.

🛠️ Bug Fixes & Enhancements
• Fixed m3u8 downloader.
• Fixed previous bugs.
• Many more not on the list
"""
