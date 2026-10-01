"""Path constants — single source of truth for every file location.

All paths are anchored to the project root (the parent of this package),
never to the process CWD, so the bot works no matter where it is launched.
"""

import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.path.join(ROOT_DIR, "ponisha.db")
LOG_PATH = os.path.join(ROOT_DIR, "ponishabot.log")
CONFIG_PATH = os.path.join(ROOT_DIR, "config.yaml")
DEBUG_DIR = os.path.join(ROOT_DIR, "debug")
BID_LOG_PATH = os.path.join(ROOT_DIR, "bid_log.json")
DATA_DIR = os.path.join(ROOT_DIR, "data")
SKILLS_CACHE_PATH = os.path.join(DATA_DIR, "skills_cache.json")
FONTS_DIR = os.path.join(ROOT_DIR, "assets", "fonts")


def root_path(name: str) -> str:
    """Resolve a filename (e.g. descriptions.txt) against the project root."""
    return os.path.join(ROOT_DIR, name)
