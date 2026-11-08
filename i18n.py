"""配置工具的 gettext 消息入口。"""
from __future__ import annotations

import gettext
from pathlib import Path

_ = gettext.translation("provision", localedir=Path(__file__).with_name("locales"),
                        fallback=True).gettext
