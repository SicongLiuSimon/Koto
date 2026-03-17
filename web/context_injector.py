# -*- coding: utf-8 -*-
"""
🎯 Koto 智能上下文注入器

根据用户问题的类型，动态选择需要包含的系统信息。
这使 Koto 能够在不同场景下提供更相关的建议。

Features:
  - 问题意图分类（编程、文件操作、应用推荐、性能诊断等）
  - 智能上下文选择（只包含相关的系统信息）
  - 系统指令动态生成
  - 性能优化（缓存分类结果）
"""

import re
from typing import Dict, List, Set, Optional, Tuple
from enum import Enum
from datetime import datetime
import logging


logger = logging.getLogger(__name__)

class TaskType(Enum):
    """任务类型枚举"""
    CODE_EXECUTION = "code_execution"  # 代码执行、编程
    FILE_OPERATION = "file_operation"  # 文件操作、查询
    APP_RECOMMENDATION = "app_recommendation"  # 应用推荐
    SYSTEM_DIAGNOSIS = "system_diagnosis"  # 系统诊断
    SYSTEM_MANAGEMENT = "system_management"  # 系统管理
    LEARNING = "learning"  # 学习、解释
    GENERAL = "general"  # 通用问题


class ContextType(Enum):
    """上下文信息类型"""
    TIME = "time"  # 时间信息
    CPU_MEMORY = "cpu_memory"  # CPU/内存使用
    DISK = "disk"  # 磁盘信息
    PROCESSES = "processes"  # 进程信息
    PYTHON_ENV = "python_env"  # Python 环境
    INSTALLED_APPS = "installed_apps"  # 已安装应用
    NETWORK = "network"  # 网络信息
    WORKING_DIR = "working_dir"  # 工作目录
    FILESYSTEM = "filesystem"  # 文件系统
    WARNINGS = "warnings"  # 系统警告


class QuestionClassifier:
    """问题分类器 - 识别用户问题的意图"""
    
    def __init__(self):
        """初始化分类器"""
        # 精简的关键词列表（直接匹配，不用正则）
        self.simple_keywords = {
            TaskType.CODE_EXECUTION: ['运行', '执行', '脚本', 'python', '代码', 'run', 'pip', 'import', '虚拟环境', 'venv'],
            TaskType.FILE_OPERATION: ['文件', '目录', '文件夹', '找', '列出', '删除', '复制', '移动', '.csv', '.xlsx', '.pdf'],
            TaskType.APP_RECOMMENDATION: ['推荐', '软件', '工具', '应用', '图片', '编辑', '视频'],
            TaskType.SYSTEM_DIAGNOSIS: ['卡', '慢', 'CPU', '内存', '磁盘', '诊断', '性能'],
            TaskType.SYSTEM_MANAGEMENT: ['开机', '关闭', '重启', '权限', '备份', '恢复', '更新'],
            TaskType.LEARNING: ['怎', '如何', '教', '解释', '学习', '教程'],
        }
        
        # 复杂的正则表达式（高优先级，权重更高）
        self.regex_keywords = {
            TaskType.CODE_EXECUTION: [
                r'(运行|执行|跑).*?(脚本|代码|程序|python|py)',
                r'(需要|要|装|安装).*(包|库|pip)',
                r'报错|错误|bug',
            ],
            TaskType.FILE_OPERATION: [
                r'(找|列出|查).*?(最大|最小)?.*?(文件|文件夹)',
                r'(删除|移动|复制|创建).*?(文件|目录)',
            ],
            TaskType.SYSTEM_DIAGNOSIS: [
                r'(卡|慢|不响应).*?(怎|怎么|为什么)',
                r'(CPU|内存|磁盘).*(高|满|占用)',
            ],
        }
    
    def classify(self, question: str) -> Tuple[TaskType, float]:
        """
        分类问题
        
        Args:
            question: 用户问题
            
        Returns:
            (任务类型, 置信度)
        """
        if not question:
            return TaskType.GENERAL, 0.0
        
        q_lower = question.lower()
        best_score = 0
        best_type = TaskType.GENERAL
        
        # 分别计算每种任务类型的匹配度
        for task_type, simple_kw_list in self.simple_keywords.items():
            score = 0
            
            # 简单关键词匹配（每个匹配 1 分）
            for keyword in simple_kw_list:
                if keyword in q_lower:
                    score += 1
            
            # 正则表达式匹配（每个匹配 2 分，权重更高）
            if task_type in self.regex_keywords:
                for pattern in self.regex_keywords[task_type]:
                    try:
                        if re.search(pattern, q_lower):
                            score += 2
                    except re.error:
                        pass
            
            # 更新最佳匹配
            if score > best_score:
                best_score = score
                best_type = task_type
        
        # 计算置信度（0-1 范围）
        if best_score == 0:
            return TaskType.GENERAL, 0.0
        
        confidence = min(best_score / 5.0, 1.0)  # 5 分为满分
        return best_type, confidence


class ContextSelector:
    """上下文选择器 - 选择需要的系统信息"""
    
    def __init__(self):
        """初始化选择器"""
        # 定义每种任务类型需要的上下文信息
        self.task_contexts = {
            TaskType.CODE_EXECUTION: [
                ContextType.PYTHON_ENV,  # Python 版本、虚拟环境
                ContextType.CPU_MEMORY,  # CPU/内存状态
                ContextType.DISK,  # 磁盘空间
                ContextType.WORKING_DIR,  # 工作目录
            ],
            TaskType.FILE_OPERATION: [
                ContextType.WORKING_DIR,  # 工作目录
                ContextType.DISK,  # 磁盘空间、分区信息
                ContextType.FILESYSTEM,  # 文件系统信息
            ],
            TaskType.APP_RECOMMENDATION: [
                ContextType.INSTALLED_APPS,  # 已安装的应用
                ContextType.CPU_MEMORY,  # CPU/内存以判断应用是否能运行
            ],
            TaskType.SYSTEM_DIAGNOSIS: [
                ContextType.CPU_MEMORY,  # CPU/内存使用情况
                ContextType.DISK,  # 磁盘使用情况
                ContextType.PROCESSES,  # 运行中的进程
                ContextType.WARNINGS,  # 系统警告
            ],
            TaskType.SYSTEM_MANAGEMENT: [
                ContextType.DISK,  # 磁盘空间（备份等）
                ContextType.WARNINGS,  # 系统警告
            ],
            TaskType.LEARNING: [
                # 学习/解释类问题不主动注入系统信息
            ],
            TaskType.GENERAL: [
                # 通用问题不主动注入系统信息，减少无关上下文
            ],
        }
    
    def select_contexts(self, task_type: TaskType) -> Set[ContextType]:
        """
        选择需要的上下文信息
        
        Args:
            task_type: 任务类型
            
        Returns:
            需要的上下文类型集合
        """
        return set(self.task_contexts.get(task_type, [ContextType.TIME]))


class ContextBuilder:
    """上下文构建器 - 生成系统指令中的上下文部分"""
    
    @staticmethod
    def build_time_context() -> str:
        """构建时间上下文"""
        from datetime import datetime
        
        now = datetime.now()
        date_str = now.strftime("%Y年%m月%d日")
        weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        time_str = now.strftime("%H:%M:%S")
        
        return f"""## 📅 当前时间（用于相对日期计算）
🕒 **系统时间**: {date_str} {weekday} {time_str}
📅 **ISO日期**: {now.strftime("%Y-%m-%d")}
⏰ **使用此时间计算**: "明天"、"下周"、"前天" 等相对时间"""
    
    @staticmethod
    def build_cpu_memory_context() -> str:
        """构建 CPU/内存上下文"""
        try:
            from web.system_info import get_system_info_collector
            
            collector = get_system_info_collector()
            cpu_info = collector.get_cpu_info()
            memory_info = collector.get_memory_info()
            
            # 提取数据（内存百分比用 'percent' 键）
            cpu_usage = cpu_info.get('usage_percent', 0)
            logical_cores = cpu_info.get('logical_cores', 0)
            mem_used = memory_info.get('used_gb', 0)
            mem_total = memory_info.get('total_gb', 0)
            mem_percent = memory_info.get('percent', 0)  # 注意：这里是 'percent'，不是 'usage_percent'
            mem_avail = memory_info.get('available_gb', 0)
            
            return f"""## 📊 CPU & 内存状态
- **CPU 使用率**: {cpu_usage:.1f}%（{logical_cores} 核）
- **内存**: {mem_used:.1f}GB / {mem_total:.1f}GB（{mem_percent:.1f}%）
- **可用内存**: {mem_avail:.1f}GB"""
        except Exception as e:
            logger.info(f"[Debug] build_cpu_memory_context error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return ""
    
    @staticmethod
    def build_disk_context() -> str:
        """构建磁盘上下文"""
        try:
            from web.system_info import get_system_info_collector
            
            collector = get_system_info_collector()
            disk_info = collector.get_disk_info()
            
            disk_lines = ["## 💿 磁盘信息"]
            
            # disk_info['drives'] 是 dict: {drive_name: {info}}
            if 'drives' in disk_info and isinstance(disk_info['drives'], dict):
                for device, drive_data in disk_info['drives'].items():
                    total = drive_data.get('total_gb', 0)
                    percent = drive_data.get('percent', 0)
                    disk_lines.append(f"- **{device}**: {total:.1f}GB（使用 {percent:.1f}%）")
            
            if 'free_gb' in disk_info:
                disk_lines.append(f"- **总可用空间**: {disk_info['free_gb']:.1f}GB")
            
            return "\n".join(disk_lines)
        except Exception as e:
            logger.info(f"[Debug] build_disk_context error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return ""
    
    @staticmethod
    def build_processes_context() -> str:
        """构建进程上下文"""
        try:
            from web.system_info import get_system_info_collector
            
            collector = get_system_info_collector()
            processes = collector.get_top_processes(limit=5)
            
            if not processes:
                return ""
            
            lines = ["## 🚀 最耗资源的进程"]
            for proc in processes:
                name = proc.get('name', '?')
                mem_pct = proc.get('memory_percent', 0)
                lines.append(f"- **{name}**: {mem_pct:.1f}% 内存")
            
            return "\n".join(lines)
        except Exception as e:
            logger.info(f"[Debug] build_processes_context error: {type(e).__name__}: {e}")
            return ""
    
    @staticmethod
    def build_python_env_context() -> str:
        """构建 Python 环境上下文"""
        try:
            from web.system_info import get_system_info_collector
            
            collector = get_system_info_collector()
            python_info = collector.get_python_environment()
            
            version = python_info.get('version', 'unknown')
            in_venv = python_info.get('in_virtualenv', False)
            pkg_count = python_info.get('package_count', 0)
            
            return f"""## 🐍 Python 环境
- **Python 版本**: {version}
- **虚拟环境**: {'✓ 已激活' if in_venv else '✗ 未激活'}
- **已安装包**: {pkg_count} 个"""
        except Exception as e:
            logger.info(f"[Debug] build_python_env_context error: {type(e).__name__}: {e}")
            return ""
    
    @staticmethod
    def build_installed_apps_context() -> str:
        """构建已安装应用上下文"""
        try:
            from web.system_info import get_system_info_collector
            
            collector = get_system_info_collector()
            apps = collector.get_installed_apps()
            
            if not apps:
                return ""
            
            lines = ["## 💻 已安装的关键应用"]
            for app in apps[:10]:  # 只显示前10个
                lines.append(f"- {app}")
            
            return "\n".join(lines)
        except Exception:
            return ""
    
    @staticmethod
    def build_working_dir_context() -> str:
        """构建工作目录上下文"""
        import os
        
        cwd = os.getcwd()
        return f"""## 📁 工作目录
- **当前目录**: `{cwd}`"""
    
    @staticmethod
    def build_filesystem_context() -> str:
        """构建文件系统上下文"""
        try:
            from web.system_info import get_system_info_collector
            
            collector = get_system_info_collector()
            disk_info = collector.get_disk_info()
            
            return f"""## 📂 文件系统信息
- **总可用空间**: {disk_info['free_gb']:.1f}GB
- **活跃分区**: {len(disk_info['drives'])} 个"""
        except Exception:
            return ""
    
    @staticmethod
    def build_network_context() -> str:
        """构建网络上下文"""
        try:
            from web.system_info import get_system_info_collector
            
            collector = get_system_info_collector()
            network_info = collector.get_network_info()
            
            lines = ["## 🌐 网络信息"]
            lines.append(f"- **主机名**: {network_info['hostname']}")
            if network_info['ip_addresses']:
                lines.append(f"- **IP 地址**: {', '.join(network_info['ip_addresses'][:2])}")
            
            return "\n".join(lines)
        except Exception:
            return ""
    
    @staticmethod
    def build_warnings_context() -> str:
        """构建系统警告上下文"""
        try:
            from web.system_info import get_system_warnings
            
            warnings = get_system_warnings()
            if not warnings:
                return ""
            
            lines = ["## ⚠️ 系统警告"]
            for warning in warnings:
                lines.append(f"  • {warning}")
            
            return "\n".join(lines)
        except Exception:
            return ""
    
    @staticmethod
    def build_contexts(context_types: Set['ContextType']) -> str:
        """
        构建多个上下文信息
        
        Args:
            context_types: 需要的上下文类型集合
            
        Returns:
            格式化的上下文信息字符串
        """
        contexts = []
        
        # 定义生成函数的映射
        builders = {
            ContextType.TIME: ContextBuilder.build_time_context,
            ContextType.CPU_MEMORY: ContextBuilder.build_cpu_memory_context,
            ContextType.DISK: ContextBuilder.build_disk_context,
            ContextType.PROCESSES: ContextBuilder.build_processes_context,
            ContextType.PYTHON_ENV: ContextBuilder.build_python_env_context,
            ContextType.INSTALLED_APPS: ContextBuilder.build_installed_apps_context,
            ContextType.WORKING_DIR: ContextBuilder.build_working_dir_context,
            ContextType.FILESYSTEM: ContextBuilder.build_filesystem_context,
            ContextType.NETWORK: ContextBuilder.build_network_context,
            ContextType.WARNINGS: ContextBuilder.build_warnings_context,
        }
        
        for context_type in context_types:
            builder = builders.get(context_type)
            if builder:
                context = builder()
                if context:
                    contexts.append(context)
        
        return "\n\n".join(contexts)


class ContextInjector:
    """主上下文注入器 - 协调所有组件"""
    
    def __init__(self):
        """初始化注入器"""
        self.classifier = QuestionClassifier()
        self.selector = ContextSelector()
        self.builder = ContextBuilder()
        self.cache = {}
        self.cache_timeout = 5  # 5 秒缓存
    
    def get_injected_instruction(self, question: str = None) -> str:
        """
        生成注入上下文的系统指令
        
        Args:
            question: 用户问题（用于智能上下文选择）
            
        Returns:
            注入了上下文的系统指令
        """
        # 确定任务类型和需要的上下文
        # 置信度阈值：低于此值时降级为 GENERAL（不注入重量级系统信息）
        _CONFIDENCE_THRESHOLD = 0.4

        if question:
            task_type, _confidence = self.classifier.classify(question)
            # 置信度不足时降级，避免误判导致不必要的系统信息注入
            if _confidence < _CONFIDENCE_THRESHOLD and task_type not in (
                TaskType.SYSTEM_DIAGNOSIS, TaskType.SYSTEM_MANAGEMENT
            ):
                task_type = TaskType.GENERAL
        else:
            task_type = TaskType.GENERAL

        context_types = self.selector.select_contexts(task_type)
        
        # 构建上下文
        context_section = self.builder.build_contexts(context_types)
        
        # 组合系统指令
        if context_section:
            context_part = f"\n\n{context_section}"
        else:
            context_part = ""

        # ── 注入个人记忆矩阵（认知风格/专长/近期目标）──
        _personality_part = ""
        try:
            import sys as _sys
            _emm_mod = _sys.modules.get("web.enhanced_memory_manager") or _sys.modules.get("enhanced_memory_manager")
            if _emm_mod is None:
                import importlib
                _emm_mod = importlib.import_module("web.enhanced_memory_manager")
            _PM = getattr(_emm_mod, "PersonalityMatrix", None)
            if _PM is not None:
                _pm_ctx = _PM().to_context_string()
                if _pm_ctx:
                    _personality_part = f"\n\n## 🧠 用户画像（持续学习更新）\n{_pm_ctx}"
        except Exception:
            pass

        return f"""你是 Koto (言)，一个与用户计算机深度融合的个人AI助手。{_personality_part}{context_part}

## 👤 角色定位
- 精通多个领域：编程、数据分析、写作、问题解决、系统管理
- 能在需要时查询用户的计算环境状态
- 快速理解用户意图，提供清晰、直接的答案
- 充当用户与Windows系统的智能中介

## 📋 回答原则
1. **简洁直接** - 不自我介绍，直接进入主题
2. **优先中文** - 默认用中文回答，除非用户要求其他语言
3. **清晰结构** - 使用标题、列表、代码块组织内容，便于快速理解
4. **按需引用系统信息** - 仅当用户明确询问系统状态、性能诊断或相关操作任务时，才引用 CPU/内存/磁盘数据；普通问答、学习解释、写作等场景**不主动提及**系统信息
5. **时间准确性** - 使用系统时间准确计算相对日期
6. **严格限制文件生成** - 绝对禁止主动生成任何文件（PPT/PDF/Word/Excel/Code）或使用了BEGIN_FILE标记，除非用户明确使用了"生成"、"创建"、"制作"等动词要求文件。对于"分析"、"解释"、"怎么做"等咨询类问题，仅提供纯文本回答。

## ✅ 能做的事
- 帮助用户分析本地文件、文档、图片
- 建议系统操作、自动化脚本、PowerShell命令
- 理解文件路径、应用名称、快捷键等Windows内容
- 协助处理剪贴板、监听快捷键、系统设置
- 联动本地应用（打开微信、邮件、浏览器等）
- 进行系统诊断：**仅当**用户反映电脑卡顿或主动查询时，才分析 CPU/内存/磁盘情况
- 准确理解和计算时间问题"""


# 全局实例
_context_injector = None


def get_context_injector() -> ContextInjector:
    """获取全局上下文注入器实例"""
    global _context_injector
    if _context_injector is None:
        _context_injector = ContextInjector()
    return _context_injector


def classify_question(question: str) -> Tuple[TaskType, float]:
    """分类用户问题"""
    injector = get_context_injector()
    return injector.classifier.classify(question)


def get_dynamic_system_instruction(question: str = None) -> str:
    """获取动态系统指令（注入了智能上下文）"""
    injector = get_context_injector()
    return injector.get_injected_instruction(question)
