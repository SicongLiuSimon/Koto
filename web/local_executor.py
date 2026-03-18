"""
local_executor.py — 本地系统操作执行器

让 Koto 能够：打开/关闭应用、截图、搜索、获取系统时间/状态、模拟按键等。
所有方法均为 classmethod，无需实例化。
不依赖 web/app.py 的任何模块级变量，可以独立导入。

从 web/app.py 的内联 LocalExecutor 类提取 (2026-03-09)。
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)


class LocalExecutor:
    """
    本地系统操作执行器 - 让 Koto 成为真正的 AI OS
    支持：打开应用、文件操作、系统命令等
    """

    # Windows 常用应用路径映射 (包含更多路径)
    APP_ALIASES = {
        # 社交通讯
        "微信": [
            "WeChat",
            r"C:\Program Files\Tencent\WeChat\WeChat.exe",
            r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe",
        ],
        "wechat": [
            "WeChat",
            r"C:\Program Files\Tencent\WeChat\WeChat.exe",
            r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe",
        ],
        "qq": [
            "QQ",
            r"C:\Program Files\Tencent\QQ\Bin\QQ.exe",
            r"C:\Program Files (x86)\Tencent\QQ\Bin\QQ.exe",
            r"C:\Program Files\Tencent\QQNT\QQ.exe",
        ],
        "钉钉": ["DingTalk", "dingtalk"],
        "飞书": ["Feishu", "Lark"],
        "telegram": ["Telegram"],
        "discord": ["Discord", "Update --processStart Discord.exe"],
        # 游戏平台
        "steam": [
            "steam",
            r"C:\Program Files (x86)\Steam\steam.exe",
            r"C:\Program Files\Steam\steam.exe",
            r"D:\Steam\steam.exe",
        ],
        "epic": [
            "EpicGamesLauncher",
            r"C:\Program Files (x86)\Epic Games\Launcher\Portal\Binaries\Win32\EpicGamesLauncher.exe",
        ],
        "战网": ["Battle.net"],
        "wallpaper engine": [
            "wallpaper32",
            "wallpaper64",
            r"C:\Program Files (x86)\Steam\steamapps\common\wallpaper_engine\wallpaper32.exe",
        ],
        "wallpaper": ["wallpaper32", "wallpaper64"],
        # 网络加速器（按开始菜单名模糊匹配）
        "西瓜加速": ["西瓜加速器", "XiguaVPN"],
        "西瓜加速器": ["西瓜加速器", "XiguaVPN"],
        "xigua": ["西瓜加速器", "XiguaVPN"],
        "uu加速器": ["UUBooster", "UU加速器"],
        "uu": ["UUBooster"],
        "迅游加速器": ["SkySun", "迅游"],
        "迅游": ["SkySun", "迅游"],
        "雷神加速器": ["Thor", "雷神"],
        "雷神": ["Thor"],
        "网易uu": ["UUBooster"],
        "加速器": [],  # 通用：find_app_smart 会走 start menu 搜索
        # 浏览器
        "chrome": [
            "chrome",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        "谷歌浏览器": ["chrome"],
        "edge": [
            "msedge",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ],
        "firefox": ["firefox", r"C:\Program Files\Mozilla Firefox\firefox.exe"],
        "浏览器": ["chrome", "msedge", "firefox"],
        # 开发工具
        "vscode": ["code"],
        "vs code": ["code"],
        "code": ["code"],
        "pycharm": ["pycharm64", "pycharm"],
        "idea": ["idea64", "idea"],
        "terminal": ["wt", "cmd", "powershell"],
        "终端": ["wt", "cmd", "powershell"],
        "命令行": ["cmd", "powershell"],
        "git": ["git-bash", r"C:\Program Files\Git\git-bash.exe"],
        # 办公软件
        "word": ["winword", "WINWORD"],
        "excel": ["excel", "EXCEL"],
        "ppt": ["powerpnt", "POWERPNT"],
        "powerpoint": ["powerpnt"],
        "outlook": ["outlook", "OUTLOOK"],
        "记事本": ["notepad"],
        "notepad": ["notepad"],
        "wps": [
            "wps",
            "wpsoffice",
            r"C:\Users\12524\AppData\Local\Kingsoft\WPS Office\ksolaunch.exe",
        ],
        "wps office": ["wps", "wpsoffice", "ksolaunch"],
        # 媒体
        "spotify": ["Spotify"],
        "网易云": [
            "cloudmusic",
            r"C:\Program Files (x86)\Netease\CloudMusic\cloudmusic.exe",
            r"C:\Program Files\Netease\CloudMusic\cloudmusic.exe",
        ],
        "网易云音乐": [
            "cloudmusic",
            r"C:\Program Files (x86)\Netease\CloudMusic\cloudmusic.exe",
        ],
        "cloudmusic": [
            "cloudmusic",
            r"C:\Program Files (x86)\Netease\CloudMusic\cloudmusic.exe",
        ],
        "qq音乐": ["QQMusic", r"C:\Program Files (x86)\Tencent\QQMusic\QQMusic.exe"],
        "酷狗": ["KuGou", r"C:\Program Files\KuGou\KuGou.exe"],
        "酷我": ["KuWo"],
        "网易音乐": ["cloudmusic"],
        "potplayer": [
            "PotPlayerMini64",
            "PotPlayerMini",
            r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe",
        ],
        "vlc": ["vlc", r"C:\Program Files\VideoLAN\VLC\vlc.exe"],
        # 系统
        "设置": ["ms-settings:"],
        "控制面板": ["control"],
        "任务管理器": ["taskmgr"],
        "计算器": ["calc"],
        "文件管理器": ["explorer"],
        "资源管理器": ["explorer"],
        "画图": ["mspaint"],
        "截图": ["snippingtool", "SnippingTool"],
    }

    # 系统操作关键词
    SYSTEM_KEYWORDS = [
        "打开",
        "启动",
        "运行",
        "开启",
        "关闭",
        "退出",
        "杀死",
        "open",
        "start",
        "launch",
        "run",
        "close",
        "kill",
        "exit",
        "搜索",
        "查找",
        "search",
        "find",
        "截图",
        "screenshot",
        "音量",
        "亮度",
        "volume",
        "brightness",
        "关机",
        "重启",
        "休眠",
        "睡眠",
        "shutdown",
        "restart",
        "sleep",
    ]

    # 知识提问模式 —— 如果匹配到这些，说明用户是在**问问题**，不是在下命令
    QUESTION_PATTERNS = [
        "怎么",
        "如何",
        "什么办法",
        "什么方法",
        "什么意思",
        "什么是",
        "是什么",
        "为什么",
        "为啥",
        "能不能",
        "可以吗",
        "可不可以",
        "怎样",
        "咋",
        "一般用",
        "通常",
        "有没有",
        "有什么",
        "哪些",
        "哪个",
        "哪种",
        "区别",
        "对比",
        "比较",
        "最好的",
        "推荐",
        "建议",
        "教程",
        "步骤",
        "流程",
        "原理",
        "概念",
        "用什么",
        "是啥",
        "啥意思",
        "讲讲",
        "说说",
        "介绍",
        "how to",
        "what is",
        "why",
        "which",
        "recommend",
        "difference between",
        "best way",
        "tutorial",
    ]

    @classmethod
    def is_system_command(cls, text):
        """检测是否是系统操作请求（祈使句/命令句，非知识提问）"""
        text_lower = text.lower().strip()

        if any(qp in text_lower for qp in cls.QUESTION_PATTERNS):
            return False

        if len(text_lower) > 30:
            return False

        action_keywords = [
            "打开",
            "启动",
            "运行",
            "开启",
            "关闭",
            "退出",
            "杀死",
            "open",
            "start",
            "launch",
            "close",
            "kill",
            "exit",
            "截图",
            "screenshot",
            "关机",
            "重启",
            "休眠",
            "睡眠",
            "shutdown",
            "restart",
            "sleep",
            "时间",
            "几点",
            "日期",
            "几号",
            "星期几",
            "time",
            "date",
            "状态",
            "信息",
            "配置",
            "内存",
            "cpu",
            "硬盘",
        ]
        has_action = any(kw in text_lower for kw in action_keywords)
        if not has_action:
            return False

        has_app = any(app in text_lower for app in cls.APP_ALIASES.keys())

        standalone_commands = [
            "截图",
            "screenshot",
            "关机",
            "重启",
            "休眠",
            "睡眠",
            "shutdown",
            "restart",
            "sleep",
            "时间",
            "几点",
            "日期",
            "几号",
            "星期几",
            "time",
            "date",
            "系统状态",
            "电脑状态",
            "系统信息",
            "电脑信息",
            "配置",
            "内存",
            "cpu",
            "硬盘",
        ]
        is_standalone = any(cmd in text_lower for cmd in standalone_commands)

        # 宽松兜底：命令动词开头 + 短输入 → 视为系统操作（即使应用名不在 APP_ALIASES）
        _cmd_starters = ("打开", "启动", "运行", "开启", "关闭", "退出", "关掉", "杀掉")
        _exclude_metaphors = (
            "文件",
            "网页",
            "网站",
            "url",
            "思路",
            "方式",
            "方法",
            "问题",
            "功能",
        )
        is_action_command = (
            len(text_lower) <= 18
            and any(text_lower.startswith(s) for s in _cmd_starters)
            and not any(k in text_lower for k in _exclude_metaphors)
        )

        return has_app or is_standalone or is_action_command

    @classmethod
    def extract_app_name(cls, text):
        """从文本中提取应用名"""
        text_lower = text.lower()

        category_mapping = {
            "音乐软件": ["网易云", "qq音乐", "spotify", "酷狗"],
            "听歌软件": ["网易云", "qq音乐", "spotify", "酷狗"],
            "浏览器": ["edge", "chrome", "firefox"],
            "文本编辑器": ["记事本", "vscode", "notepad"],
            "代码编辑器": ["vscode", "pycharm", "idea"],
            "视频播放器": ["potplayer", "vlc"],
            "聊天软件": ["微信", "qq", "钉钉"],
            "办公软件": ["word", "excel", "ppt", "wps"],
        }

        for app_name in sorted(cls.APP_ALIASES.keys(), key=len, reverse=True):
            if app_name in text_lower:
                return app_name

        for category, apps in category_mapping.items():
            if category in text_lower:
                import shutil

                for app in apps:
                    aliases = cls.APP_ALIASES.get(app, [app])
                    for alias in aliases:
                        if os.path.exists(alias) or shutil.which(alias):
                            return app
                return apps[0]

        import re

        patterns = [
            r"(?:打开|启动|运行|开启)\s*(?:一个|一款)?\s*(.+?)(?:\s|$|吧|呗)",
            r"(?:open|start|launch)\s+(?:a\s+)?(.+?)(?:\s|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                return match.group(1).strip()

        return None

    @classmethod
    def find_app_in_start_menu(cls, app_name):
        """从开始菜单查找应用"""
        import glob

        start_menu_paths = [
            os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
            os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs"),
        ]

        app_name_lower = app_name.lower()

        for start_path in start_menu_paths:
            if not os.path.exists(start_path):
                continue
            for lnk_file in glob.glob(
                os.path.join(start_path, "**", "*.lnk"), recursive=True
            ):
                lnk_name = os.path.basename(lnk_file).lower().replace(".lnk", "")
                if app_name_lower in lnk_name or lnk_name in app_name_lower:
                    return lnk_file

        return None

    @classmethod
    def find_app_smart(cls, app_name):
        """智能查找应用 - 多种方式"""
        import shutil
        import subprocess

        if app_name.lower() in cls.APP_ALIASES:
            aliases = cls.APP_ALIASES[app_name.lower()]
            for alias in aliases:
                if os.path.exists(alias):
                    return alias
                if shutil.which(alias):
                    return alias

        if shutil.which(app_name):
            return app_name

        lnk_path = cls.find_app_in_start_menu(app_name)
        if lnk_path:
            return lnk_path

        try:
            ps_cmd = f'Get-StartApps | Where-Object {{$_.Name -like "*{app_name}*"}} | Select-Object -First 1 -ExpandProperty AppID'
            result = subprocess.run(
                ["powershell", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                ),
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

        return None

    @classmethod
    def execute(cls, user_input):
        """执行系统操作"""
        import subprocess

        text_lower = user_input.lower()
        result = {"success": False, "action": "", "message": "", "details": ""}

        # === 打开应用 ===
        if any(
            kw in text_lower
            for kw in ["打开", "启动", "运行", "开启", "open", "start", "launch"]
        ):
            app_name = cls.extract_app_name(text_lower)

            if app_name:
                app_path = cls.find_app_smart(app_name)

                if app_path:
                    try:
                        if app_path.startswith("ms-"):
                            subprocess.Popen(
                                f"start {app_path}",
                                shell=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                        elif app_path.endswith(".lnk"):
                            subprocess.Popen(
                                f'start "" "{app_path}"',
                                shell=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                        elif "!" in app_path:
                            subprocess.Popen(
                                f"start shell:AppsFolder\\{app_path}",
                                shell=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                        elif os.path.exists(app_path):
                            subprocess.Popen(
                                [app_path],
                                shell=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=(
                                    subprocess.CREATE_NO_WINDOW
                                    if sys.platform == "win32"
                                    else 0
                                ),
                            )
                        else:
                            subprocess.Popen(
                                f'start "" "{app_path}"',
                                shell=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )

                        result["success"] = True
                        result["action"] = "open_app"
                        result["message"] = f"✅ 已打开 {app_name}"
                        logger.info(
                            f"[LocalExecutor] ✅ 成功启动应用: {app_name} - 路径: {app_path}"
                        )
                        return result
                    except Exception as e:
                        result["message"] = f"❌ 打开 {app_name} 失败: {str(e)}"
                        return result

                try:
                    subprocess.Popen(
                        f'start "" "{app_name}"',
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    result["success"] = True
                    result["action"] = "open_app"
                    result["message"] = f"✅ 正在尝试打开 {app_name}"
                    return result
                except Exception:
                    pass

                result["message"] = f"❌ 无法打开 {app_name}，请确认已安装"
                return result

        # === 关闭应用 ===
        if any(
            kw in text_lower for kw in ["关闭", "退出", "杀死", "close", "kill", "exit"]
        ):
            app_name = cls.extract_app_name(text_lower)
            if app_name:
                import subprocess

                aliases = cls.APP_ALIASES.get(app_name, [app_name])
                for alias in aliases:
                    try:
                        if sys.platform == "win32":
                            proc_name = (
                                alias.split("\\")[-1] if "\\" in alias else alias
                            )
                            if not proc_name.endswith(".exe"):
                                proc_name += ".exe"
                            ret = subprocess.run(
                                f'taskkill /IM "{proc_name}" /F',
                                shell=True,
                                capture_output=True,
                                timeout=5,
                                creationflags=subprocess.CREATE_NO_WINDOW,
                            )
                            if ret.returncode == 0:
                                result["success"] = True
                                result["action"] = "close_app"
                                result["message"] = f"✅ 已关闭 {app_name}"
                                return result
                    except Exception:
                        continue
                result["message"] = f"❌ 无法关闭 {app_name}"
                return result

        # === 截图 ===
        if "截图" in text_lower or "screenshot" in text_lower:
            if sys.platform == "win32":
                import subprocess

                subprocess.Popen(
                    "snippingtool",
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                result["success"] = True
                result["action"] = "screenshot"
                result["message"] = "✅ 已打开截图工具"
                return result

        # === 搜索 ===
        if any(kw in text_lower for kw in ["搜索", "查找", "search"]):
            search_terms = (
                text_lower.replace("搜索", "")
                .replace("查找", "")
                .replace("search", "")
                .strip()
            )
            if search_terms:
                import webbrowser

                webbrowser.open(f"https://www.google.com/search?q={search_terms}")
                result["success"] = True
                result["action"] = "search"
                result["message"] = f"✅ 正在搜索: {search_terms}"
                return result

        # === 系统时间/日期 ===
        if any(
            kw in text_lower
            for kw in ["时间", "几点", "日期", "几号", "星期几", "time", "date"]
        ):
            import datetime

            now = datetime.datetime.now()
            weekdays = [
                "星期一",
                "星期二",
                "星期三",
                "星期四",
                "星期五",
                "星期六",
                "星期日",
            ]
            weekday_str = weekdays[now.weekday()]

            if any(kw in text_lower for kw in ["日期", "几号", "星期几", "date"]):
                time_str = now.strftime(f"%Y年%m月%d日 {weekday_str}")
                msg = f"📅 当前日期是：{time_str}"
            else:
                time_str = now.strftime(f"%Y-%m-%d %H:%M:%S {weekday_str}")
                msg = f"🕒 当前系统时间是：{time_str}"

            result["success"] = True
            result["action"] = "get_time"
            result["message"] = msg
            return result

        # === 电源操作 ===
        if any(
            kw in text_lower
            for kw in ["关机", "重启", "休眠", "睡眠", "shutdown", "restart", "sleep"]
        ):
            if sys.platform == "win32":
                import subprocess

                if "关机" in text_lower or "shutdown" in text_lower:
                    subprocess.Popen("shutdown /s /t 0", shell=True)
                    msg = "✅ 正在关机..."
                elif "重启" in text_lower or "restart" in text_lower:
                    subprocess.Popen("shutdown /r /t 0", shell=True)
                    msg = "✅ 正在重启..."
                else:
                    subprocess.Popen(
                        "rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True
                    )
                    msg = "✅ 正在进入休眠/睡眠状态..."
                result["success"] = True
                result["action"] = "power_op"
                result["message"] = msg
                return result

        # === 系统状态 ===
        if any(
            kw in text_lower
            for kw in [
                "系统状态",
                "电脑状态",
                "系统信息",
                "电脑信息",
                "配置",
                "内存",
                "cpu",
                "硬盘",
            ]
        ):
            info = cls.get_system_info()
            if info.get("success"):
                mem = info.get("memory", {})
                disk = info.get("disk", {})
                msg = (
                    f"💻 **系统状态报告**\n\n"
                    f"- **操作系统**: {info.get('system')} ({info.get('platform')})\n"
                    f"- **处理器**: {info.get('processor')}\n"
                    f"- **CPU 使用率**: {info.get('cpu_percent')}%\n"
                    f"- **内存**: 已用 {mem.get('percent')}% (剩余 {mem.get('available')} / 总共 {mem.get('total')})\n"
                    f"- **C盘**: 已用 {disk.get('percent')}% (剩余 {disk.get('free')} / 总共 {disk.get('total')})\n"
                )
                result["success"] = True
                result["action"] = "get_system_info"
                result["message"] = msg
                return result

        result["message"] = "❓ 无法识别该系统操作"
        return result

    @classmethod
    def get_clipboard(cls):
        """获取剪贴板内容"""
        try:
            import pyperclip

            content = pyperclip.paste()
            return {
                "success": True,
                "content": content,
                "length": len(content),
                "message": f"✅ 已获取剪贴板内容 ({len(content)} 字符)",
            }
        except Exception as e:
            return {
                "success": False,
                "content": "",
                "message": f"❌ 无法读取剪贴板: {str(e)}",
            }

    @classmethod
    def set_clipboard(cls, text):
        """设置剪贴板内容"""
        try:
            import pyperclip

            pyperclip.copy(text)
            return {"success": True, "message": f"✅ 已复制到剪贴板 ({len(text)} 字符)"}
        except Exception as e:
            return {"success": False, "message": f"❌ 无法写入剪贴板: {str(e)}"}

    @classmethod
    def get_system_info(cls):
        """获取系统信息"""
        try:
            import platform

            import psutil

            return {
                "success": True,
                "system": platform.system(),
                "platform": platform.platform(),
                "processor": platform.processor(),
                "cpu_count": psutil.cpu_count(),
                "cpu_percent": psutil.cpu_percent(interval=1),
                "memory": {
                    "total": f"{psutil.virtual_memory().total / (1024**3):.2f} GB",
                    "available": f"{psutil.virtual_memory().available / (1024**3):.2f} GB",
                    "percent": psutil.virtual_memory().percent,
                },
                "disk": {
                    "total": f"{psutil.disk_usage('/').total / (1024**3):.2f} GB",
                    "free": f"{psutil.disk_usage('/').free / (1024**3):.2f} GB",
                    "percent": psutil.disk_usage("/").percent,
                },
            }
        except Exception as e:
            return {"success": False, "message": f"❌ 无法获取系统信息: {str(e)}"}

    @classmethod
    def list_running_apps(cls):
        """列出正在运行的应用"""
        try:
            import psutil

            apps = []
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    apps.append({"name": proc.info["name"], "pid": proc.info["pid"]})
                except Exception:
                    continue
            return {
                "success": True,
                "apps": apps[:30],
                "count": len(apps),
                "message": f"✅ 找到 {len(apps)} 个运行中的进程",
            }
        except Exception as e:
            return {"success": False, "message": f"❌ 无法列出应用: {str(e)}"}

    @classmethod
    def open_file_or_directory(cls, path):
        """打开文件或目录"""
        try:
            import subprocess

            path = os.path.expanduser(path)
            if not os.path.exists(path):
                return {"success": False, "message": f"❌ 路径不存在: {path}"}
            (
                os.startfile(path)
                if sys.platform == "win32"
                else subprocess.Popen(["open", path])
            )
            kind = "文件" if os.path.isfile(path) else "目录"
            return {
                "success": True,
                "message": f"✅ 已打开{kind}: {os.path.basename(path) or path}",
            }
        except Exception as e:
            return {"success": False, "message": f"❌ 无法打开: {str(e)}"}

    @classmethod
    def send_keystroke(cls, key_combination):
        """模拟键盘快捷键"""
        try:
            import keyboard

            keys = [k.strip().lower() for k in key_combination.split("+")]
            keyboard.hotkey(*keys)
            return {"success": True, "message": f"✅ 已模拟快捷键: {key_combination}"}
        except Exception as e:
            return {"success": False, "message": f"❌ 无法发送快捷键: {str(e)}"}
