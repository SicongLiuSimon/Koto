#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Windows 本地通知封装
使用 win10toast 显示右下角系统提醒
"""

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_toaster = None


def _get_toaster():
    global _toaster
    if _toaster is not None:
        return _toaster
    try:
        from win10toast import ToastNotifier

        _toaster = ToastNotifier()
    except Exception as e:
        logger.warning(f"⚠️ 无法初始化系统通知: {e}")
        _toaster = None
    return _toaster


def show_toast(
    title: str, msg: str, duration: int = 5, icon_path: Optional[str] = None
):
    """显示 Windows 系统通知 (非阻塞)。
    如果当前环境不支持 (非 Windows 或缺少依赖)，静默失败并打印提示。
    """
    toaster = _get_toaster()
    if toaster is None:
        return False

    def _run():
        try:
            toaster.show_toast(
                title=title,
                msg=msg,
                icon_path=(
                    icon_path if icon_path and os.path.exists(icon_path) else None
                ),
                duration=max(3, duration),
                threaded=True,
            )
        except Exception as e:
            logger.warning(f"⚠️ 系统通知发送失败: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return True
