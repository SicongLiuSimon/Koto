"""
ToolRouter — 动态工具子集选择器

作用：根据用户意图，从全量工具列表中筛选出最相关的工具子集，
      传给 LLM 以减少 tokens 消耗，同时降低 LLM 误选工具的概率。

策略：
  1. 关键词匹配（O(1) 分类，无需 LLM）
  2. 未能命中任何分类时，返回核心工具集（而非全量，避免超出 context window）
  3. 支持强制返回全量（force_all=True）

分类参考（可持续扩充）：
  • communication  → 微信、邮件
  • calendar       → 日程、提醒
  • search         → 网络搜索、本地搜索、文件查找
  • browser        → 浏览器自动化
  • files          → 文件读写、文档、Excel、格式转换、压缩
  • system         → 系统信息、应用打开、截图、剪贴板、shell 命令
  • code           → Python 执行、脚本生成
  • data           → 数据处理、分析
"""

import re
import logging
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 工具分类表：category → 工具名称集合
# 调整这里可控制哪些工具在什么场景下对 LLM 可见
# ─────────────────────────────────────────────────────────────────────────────
TOOL_CATEGORIES: Dict[str, Set[str]] = {
    "communication": {
        "send_wechat_message", "read_wechat_message",
        "send_email",
        "notify_user",
    },
    "calendar": {
        "add_calendar_event", "list_calendar_events", "delete_calendar_event",
        "add_reminder", "list_reminders", "cancel_reminder",
        "get_current_datetime", "get_current_time",
    },
    "search": {
        "web_search", "search_local_files",
        "get_12306_ticket_url",
    },
    "browser": {
        "open_url", "browser_get_page_info", "browser_click",
        "browser_input_text", "browser_screenshot", "browser_get_text",
    },
    "files": {
        "read_file", "write_file", "read_document", "generate_document",
        "analyze_excel_data", "convert_file",
        "list_directory", "open_file_or_folder",
        "move_file", "delete_file", "zip_files", "unzip_file",
        # 精准编辑工具（优先于 write_file 全量覆盖）
        "replace_text", "patch_file", "insert_line", "delete_lines", "append_text",
        "list_backups", "restore_backup",
    },
    "system": {
        "open_application", "open_file_or_folder",
        "take_screenshot", "notify_user",
        "get_clipboard_text", "set_clipboard_text",
        "read_clipboard", "search_clipboard",
        "shell_command",
        "query_cpu_status", "query_memory_status",
        "query_disk_usage", "query_network_status",
        "query_python_env", "list_running_apps",
    },
    "code": {
        "run_python_code", "shell_command",
        "generate_script", "run_script",
        # 代码文件精准编辑
        "replace_text", "patch_file", "insert_line", "delete_lines",
        "read_file", "write_file",
    },
    "data": {
        "analyze_excel_data", "calculate",
        "process_csv", "process_json",
        "run_python_code",
    },
}

# 意图关键词 → 分类映射（支持正则 / 简单子串）
_INTENT_RULES: List[tuple] = [
    # communication
    (r"微信|wechat|发消息|发送消息|聊天记录", "communication"),
    (r"邮件|email|mail|发邮件", "communication"),
    (r"通知|提醒我|remind me", "communication"),

    # calendar
    (r"日程|calendar|会议|约会|安排|提醒|schedule|reminder|几点|明天|后天|下周", "calendar"),
    (r"今天|现在几点|当前时间|几号|日期", "calendar"),

    # search
    (r"搜索|查询|查一下|查找|search|google|网上找|百度|信息|新闻|价格|天气|汇率|股价|12306|车票|火车", "search"),

    # browser
    (r"浏览器|打开网页|网址|url|点击|浏览|网站|webpage|browser", "browser"),

    # files
    (r"文件|文档|word|excel|pdf|ppt|表格|读取|写入|保存|创建文件|下载|上传|解压|压缩|zip", "files"),
    (r"目录|文件夹|folder|directory|列出文件|ls |dir ", "files"),
    (r"修改文件|编辑文件|替换|批注|注释|改一下|改这里|更新文件|patch|edit file|replace in", "files"),
    (r"插入行|删除行|追加内容|append to|insert line|delete line", "files"),

    # system
    (r"截图|screenshot|剪贴板|clipboard|打开应用|open app|命令行|cmd|powershell|shell", "system"),
    (r"cpu|内存|memory|磁盘|disk|进程|process|系统状态|系统信息|system info", "system"),

    # code
    (r"运行代码|执行代码|python|脚本|run code|代码|编程|计算|eval", "code"),

    # data
    (r"分析数据|统计|数据处理|数据分析|aggregat|sum|count|average|均值|汇总", "data"),
]

# 核心工具集：无法命中分类时的兜底（比全量小很多）
_CORE_TOOLS: Set[str] = {
    "get_current_time",
    "get_current_datetime",
    "calculate",
    "web_search",
    "run_python_code",
    "search_local_files",
    "notify_user",
    "read_file",
    "write_file",
    "replace_text",
    "patch_file",
    "list_directory",
    "shell_command",
    "take_screenshot",
}


class ToolRouter:
    """
    使用关键词规则将用户意图映射到工具子集。

    用法::

        router = ToolRouter()
        selected = router.select(all_tool_defs, user_message)
        # selected 是过滤后的工具 schema 列表，传给 LLM
    """

    def __init__(self, max_tools: int = 20):
        """
        Args:
            max_tools: 单次能向 LLM 暴露的工具上限（防止 token 爆炸）
        """
        self.max_tools = max_tools

    def select(
        self,
        all_definitions: List[Dict],
        user_message: str,
        force_all: bool = False,
    ) -> List[Dict]:
        """
        根据 user_message 从 all_definitions 中筛选最相关的工具。

        Args:
            all_definitions: ToolRegistry.get_definitions() 返回的完整列表
            user_message:    用户输入文本
            force_all:       True 则跳过过滤，返回全量（截断至 max_tools）

        Returns:
            过滤并截断后的工具定义列表
        """
        if force_all:
            result = all_definitions[: self.max_tools]
            logger.debug(f"[ToolRouter] force_all → {len(result)} 工具")
            return result

        # 1. 识别意图分类
        matched_categories = self._match_categories(user_message)

        # 2. 合并对应工具名称集合
        if matched_categories:
            allowed_names: Set[str] = set()
            for cat in matched_categories:
                allowed_names |= TOOL_CATEGORIES.get(cat, set())
            label = "+".join(sorted(matched_categories))
        else:
            allowed_names = _CORE_TOOLS
            label = "core"

        # 3. 始终包含核心工具（calendar/datetime 几乎所有任务都可能需要）
        allowed_names |= {"get_current_time", "get_current_datetime", "notify_user"}

        # 4. 过滤
        filtered = [d for d in all_definitions if d.get("name") in allowed_names]

        # 5. 若过滤后工具数 < 3（可能是新工具未入分类表），追加全量兜底
        if len(filtered) < 3:
            filtered = all_definitions

        # 6. 截断
        result = filtered[: self.max_tools]
        logger.debug(
            f"[ToolRouter] intent={label!r} → "
            f"{len(result)}/{len(all_definitions)} 工具暴露给 LLM"
        )
        return result

    def _match_categories(self, text: str) -> List[str]:
        """返回命中的分类名列表（可多命中）"""
        text_lower = text.lower()
        matched = []
        seen: Set[str] = set()
        for pattern, category in _INTENT_RULES:
            if category in seen:
                continue
            if re.search(pattern, text_lower):
                matched.append(category)
                seen.add(category)
        return matched


# ── 模块级单例（工厂层直接 import 使用）────────────────────────────────────
_default_router: Optional[ToolRouter] = None


def get_tool_router(max_tools: int = 20) -> ToolRouter:
    global _default_router
    if _default_router is None:
        _default_router = ToolRouter(max_tools=max_tools)
    return _default_router
