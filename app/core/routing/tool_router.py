"""
ToolRouter — 双层工具子集选择器 (v2)

作用：根据用户意图，从全量工具列表中筛选出最相关的工具子集，
      传给 LLM 以减少 tokens 消耗，同时降低 LLM 误选工具的概率。

策略：
  Tier 1 (快速路径): 关键词正则规则 → 工具名集合（O(1)，无延迟，无依赖）
  Tier 2 (语义匹配): 字符 n-gram + 词汇重叠评分 → 按相似度排序 Top-K
                     纯 stdlib 实现，延迟 < 1ms，无外部依赖，自动缓存索引

  最终结果：两层合并，关键词命中工具排在前面（LLM 优先可见），截断至 max_tools。

  优点：
  - Tier 1 维持原有精度（已知分类）
  - Tier 2 覆盖模糊意图 / 新加入工具 / 未命中分类的情况
  - 工具描述索引懒加载，工具集变化时自动重建（hash 对比）

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
# Tier 2 Helpers: 纯 stdlib 语义匹配
# ─────────────────────────────────────────────────────────────────────────────

def _build_tokens(text: str) -> frozenset:
    """
    混合分词器：提取中文单字 + 英文词（含下划线分割）+ 字符 bigram。
    对中英混合的工具名/描述和中文用户查询均有良好覆盖。

    示例:
      "分析这个CSV里的趋势" → {"分","析","这","个","C","S","V","里","的","趋","势",
                               "分析","析这","这个","csv","趋势", ...}
      "analyze_excel_data"  → {"analyze","excel","data","an","na","al","ly",...}
    """
    tokens: set = set()
    text_lower = text.lower()

    # 英文词 + 下划线碎片（工具名如 analyze_excel_data → analyze, excel, data）
    for w in re.findall(r'[a-z_]+', text_lower):
        tokens.add(w)
        for part in w.split('_'):
            if len(part) > 1:
                tokens.add(part)

    # 中文单字
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            tokens.add(ch)

    # 字符 bigram（捕获「分析」「搜索」「查询」等2字中文词，以及英文2-gram）
    clean = re.sub(r'\s+', '', text_lower)
    for i in range(len(clean) - 1):
        bg = clean[i:i + 2]
        if bg.strip():
            tokens.add(bg)

    return frozenset(tokens)


def _overlap_score(query_toks: frozenset, desc_toks: frozenset) -> float:
    """
    召回偏置重叠系数: |intersection| / |query|。
    衡量"工具描述覆盖了用户查询多少语义"，避免长描述被 Jaccard 惩罚。
    """
    if not query_toks:
        return 0.0
    return len(query_toks & desc_toks) / len(query_toks)

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
    (r"搜索|查询|查一下|查找|search|google|网上找|百度|信息|新闻|价格|天气|汇率|股价|12306|车票|火车"
     r"|金价|油价|银价|黄金|白银|铜价|行情|期货|现货|实时|最新价|今日价|当前价|涨跌|走势|比特币|以太坊", "search"),

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
    双层工具子集选择器。

    Tier 1 — 关键词正则（快速路径，O(1)，无额外延迟）
    Tier 2 — 描述语义匹配（n-gram overlap，纯 stdlib，< 1ms，自动缓存）

    两层结果合并：关键词命中的工具排在前面（LLM 优先读取），
    语义匹配的工具补充尾部，共同截断至 max_tools。

    用法::
        router = ToolRouter()
        selected = router.select(all_tool_defs, user_message)
    """

    def __init__(self, max_tools: int = 20, semantic_topk: int = 12):
        """
        Args:
            max_tools:     单次向 LLM 暴露的工具上限（防止 token 爆炸）
            semantic_topk: Tier 2 语义匹配保留的 Top-K 候选数
        """
        self.max_tools = max_tools
        self._semantic_topk = semantic_topk
        # 懒加载的描述索引 {tool_name: frozenset(tokens)}
        self._desc_index: Optional[Dict[str, frozenset]] = None
        self._index_cache_key: Optional[int] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def select(
        self,
        all_definitions: List[Dict],
        user_message: str,
        force_all: bool = False,
    ) -> List[Dict]:
        """
        两层筛选：Tier1 关键词 → Tier2 语义，合并后截断至 max_tools。

        Args:
            all_definitions: ToolRegistry.get_definitions() 返回的完整列表
            user_message:    用户输入文本
            force_all:       True 则跳过过滤，返回全量（截断至 max_tools）

        Returns:
            过滤并截断后的工具定义列表（关键词命中的排在前面）
        """
        if force_all:
            result = all_definitions[: self.max_tools]
            logger.debug(f"[ToolRouter] force_all → {len(result)} 工具")
            return result

        # ── Tier 1: 关键词规则 ────────────────────────────────────────────────
        matched_categories = self._match_categories(user_message)
        keyword_names: Set[str] = set()
        for cat in matched_categories:
            keyword_names |= TOOL_CATEGORIES.get(cat, set())
        # 永远包含锚点工具（时间/通知几乎所有场景都需要）
        keyword_names |= {"get_current_time", "get_current_datetime", "notify_user"}

        keyword_defs = [d for d in all_definitions if d.get("name") in keyword_names]
        seen_names: Set[str] = {d["name"] for d in keyword_defs}

        # ── Tier 2: 语义 n-gram 匹配 ─────────────────────────────────────────
        semantic_ranked = self._semantic_select(
            user_message, all_definitions, topk=self._semantic_topk
        )
        semantic_defs = [
            d for d in all_definitions
            if d.get("name") in set(semantic_ranked) and d.get("name") not in seen_names
        ]
        rank_order = {name: i for i, name in enumerate(semantic_ranked)}
        semantic_defs.sort(key=lambda d: rank_order.get(d.get("name", ""), 999))

        # ── 合并与截断 ────────────────────────────────────────────────────────
        merged = (keyword_defs + semantic_defs)[: self.max_tools]

        # 兜底：两层都没命中任何工具时（通常是新添加的极少见工具集），使用核心集
        if len(merged) == 0:
            core_defs = [d for d in all_definitions if d.get("name") in _CORE_TOOLS]
            merged = core_defs[: self.max_tools]

        label = "+".join(sorted(matched_categories)) if matched_categories else "semantic-only"
        logger.debug(
            f"[ToolRouter] intent={label!r} "
            f"keyword={len(keyword_defs)} semantic={len(semantic_defs)} "
            f"→ {len(merged)}/{len(all_definitions)} 工具暴露给 LLM"
        )
        return merged

    # ── Tier 1: 关键词规则 ────────────────────────────────────────────────────

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

    # ── Tier 2: 语义 n-gram 匹配 ─────────────────────────────────────────────

    def _get_desc_index(self, definitions: List[Dict]) -> Dict[str, frozenset]:
        """
        返回 {tool_name: token_set} 索引。
        当工具集发生变化（hash 不同）时自动重建，否则返回缓存。
        """
        cache_key = hash(tuple(d.get("name", "") for d in definitions))
        if cache_key != self._index_cache_key or self._desc_index is None:
            self._desc_index = {
                d["name"]: _build_tokens(
                    (d.get("description") or "")
                    + " "
                    + d.get("name", "").replace("_", " ")
                )
                for d in definitions if d.get("name")
            }
            self._index_cache_key = cache_key
            logger.debug(f"[ToolRouter] 语义索引重建: {len(self._desc_index)} 个工具")
        return self._desc_index

    def _semantic_select(
        self,
        query: str,
        definitions: List[Dict],
        topk: int,
    ) -> List[str]:
        """
        返回按语义相关度排序的 Top-K 工具名列表。
        使用召回偏置重叠系数：|query_tokens ∩ desc_tokens| / |query_tokens|
        """
        query_toks = _build_tokens(query)
        if not query_toks:
            return []
        index = self._get_desc_index(definitions)
        scores = [
            (name, _overlap_score(query_toks, desc_toks))
            for name, desc_toks in index.items()
        ]
        scores.sort(key=lambda x: -x[1])
        return [name for name, score in scores[:topk] if score > 0.0]


# ── 模块级单例（工厂层直接 import 使用）────────────────────────────────────
_default_router: Optional[ToolRouter] = None


def get_tool_router(max_tools: int = 20) -> ToolRouter:
    global _default_router
    if _default_router is None:
        _default_router = ToolRouter(max_tools=max_tools)
    return _default_router
