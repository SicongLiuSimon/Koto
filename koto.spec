# -*- mode: python ; coding: utf-8 -*-
"""
Koto 言 - PyInstaller 完整打包配置 v2.0
入口：koto_setup.py（含首次设置向导 + 本地模型下载器）
模式：目录模式（启动更快，杀毒误报少）
目标：Windows x64 独立发布包
"""

import os
import sys
from pathlib import Path

block_cipher = None
ROOT = os.path.abspath('.')

# ═══════════════════════════════════════════════
# 数据文件（资源 + Python 源码）
# ═══════════════════════════════════════════════

datas = []

def _add(src, dst):
    """安全添加数据文件/目录，仅在存在时加入"""
    if os.path.exists(src):
        datas.append((src, dst))

# ── 前端资源 ──
_add(os.path.join(ROOT, 'web', 'templates'),                  os.path.join('web', 'templates'))
_add(os.path.join(ROOT, 'web', 'static'),                     os.path.join('web', 'static'))
# uploads 只放空占位（不打包用户文件）
_add(os.path.join(ROOT, 'web', 'uploads', '.gitkeep'),        os.path.join('web', 'uploads'))

# ── Python 包 ──
_add(os.path.join(ROOT, 'app'),                     'app')
_add(os.path.join(ROOT, 'launcher'),                'launcher')

# ── 图标资源 ──
_add(os.path.join(ROOT, 'src', 'assets', 'koto_icon.ico'), os.path.join('assets', 'koto_icon.ico'))
_add(os.path.join(ROOT, 'src', 'assets', 'koto_icon.png'), os.path.join('assets', 'koto_icon.png'))
_add(os.path.join(ROOT, 'src', 'assets', 'koto_icon.svg'), os.path.join('assets', 'koto_icon.svg'))

# ── 默认配置模板 ──
_add(os.path.join(ROOT, 'config', 'gemini_config.env.example'),
     os.path.join('config', 'gemini_config.env.example'))
# user_settings.json 不打包：含开发者本机绝对路径，且运行时由 web/settings.py
# 自动按 DEFAULT_SETTINGS 在 exe 同级 config/ 目录创建，无需预置。
# skill_packs 目录目前为空，保留 _add 调用确保目录未来有内容时自动打包
_add(os.path.join(ROOT, 'config', 'skill_packs'),   os.path.join('config', 'skill_packs'))
_add(os.path.join(ROOT, 'config', 'skills'),        os.path.join('config', 'skills'))
_add(os.path.join(ROOT, 'config', 'workflows'),     os.path.join('config', 'workflows'))

# ── src/ 入口脚本（作为数据一同打包，供 runpy 兜底使用）──
for _script in ['koto_app.py', 'model_downloader.py', 'koto_setup.py', 'server.py']:
    _add(os.path.join(ROOT, 'src', _script), '.')

# ── web/*.py 全部作为数据文件（动态 import 兜底）──
_web_dir = os.path.join(ROOT, 'web')
if os.path.isdir(_web_dir):
    for _f in os.listdir(_web_dir):
        if _f.endswith('.py'):
            datas.append((os.path.join(_web_dir, _f), 'web'))

# ── 用户文档 ──
_add(os.path.join(ROOT, 'README.md'), '.')

# ═══════════════════════════════════════════════
# 隐式导入
# ═══════════════════════════════════════════════

hiddenimports = [
    # ── 标准库 tkinter（模型下载器 GUI）──
    'tkinter', 'tkinter.font', 'tkinter.ttk', 'tkinter.messagebox',
    'tkinter.scrolledtext', 'tkinter.simpledialog',
    '_tkinter',

    # ── Flask & 相关 ──
    'flask', 'flask.json', 'flask_cors',
    'jinja2', 'jinja2.ext', 'markupsafe',
    'werkzeug', 'werkzeug.serving', 'werkzeug.routing',
    'werkzeug.middleware.proxy_fix',

    # ── Socket.IO / engineio ──
    'engineio', 'engineio.async_drivers', 'engineio.async_drivers.threading',
    'socketio',

    # ── Google GenAI / API core ──
    'google', 'google.genai', 'google.genai.types',
    'google.api_core', 'google.api_core.gapic_v1',
    'google.auth', 'google.auth.transport', 'google.auth.transport.requests',
    'google.protobuf',

    # ── HTTP ──
    'httpx', 'httpx._client', 'httpcore', 'httpcore._async',
    'anyio', 'anyio._backends._asyncio', 'anyio._backends._trio',
    'sniffio', 'h11', 'h2', 'certifi',

    # ── 文档处理 ──
    'docx', 'docx.oxml', 'docx.oxml.ns', 'docx.oxml.table',
    'lxml', 'lxml.etree', 'lxml._elementpath', 'lxml.html',
    'openpyxl', 'openpyxl.styles', 'openpyxl.utils',
    'pptx', 'pptx.util', 'pptx.enum', 'pptx.dml', 'pptx.chart',
    'PyPDF2', 'pdfplumber', 'pypdf',
    'bs4', 'bs4.builder', 'bs4.builder._lxml',
    'jieba', 'jieba.posseg', 'jieba.analyse',
    'docx2txt', 'striprtf',

    # ── 数据分析 ──
    'pandas', 'pandas.io.formats.format',
    'numpy', 'numpy.core', 'numpy.lib',
    'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont', 'PIL.ImageFilter',

    # ── 调度 ──
    'schedule',

    # ── 安全 / JWT ──
    'cryptography', 'cryptography.fernet', 'cryptography.hazmat.primitives',
    'jwt',

    # ── 系统 ──
    'psutil', 'markdown', 'markdown.extensions.extra',
    'dotenv', 'python_dotenv',
    'subprocess', 'socket', 'threading', 'pathlib', 'urllib',

    # ── 桌面应用 ──
    'webview', 'webview.platforms.winforms',
    'pystray', 'pystray._win32',
    'pyperclip',
    'win32api', 'win32con', 'win32gui', 'win32process', 'win32event',
    'pywintypes', 'pythoncom',

    # ── 语音输入（可选，打包后优雅降级）──
    'speech_recognition',
    'pyaudio',
    'vosk',
    'edge_tts',
    'sounddevice', 'soundfile',
    'wave', 'audioop',
    'comtypes', 'comtypes.client',
    'win32com', 'win32com.client',

    # ── LangChain / LangGraph ──
    'langchain_core', 'langchain_core.messages',
    'langchain_google_genai',
    'langgraph', 'langgraph.graph',
    'langchain_community',
    # transformers / peft / trl / accelerate / datasets 属于 LoRA 训练依赖，
    # 全部在函数体内懒加载，不打包进发行版（节省数 GB 体积）。

    # ── App 路由模块 ──
    'app', 'app.core', 'app.api',
    'app.core.routing',
    'app.core.routing.smart_dispatcher',
    'app.core.routing.local_model_router',
    'app.core.routing.local_planner',
    'app.core.routing.ai_router',
    'app.core.routing.intent_analyzer',
    'app.core.routing.task_decomposer',
    'app.core.agent', 'app.core.agent.factory',
    'app.core.agent.base', 'app.core.agent.types',
    'app.core.agent.unified_agent',
    'app.core.agent.langgraph_agent',
    'app.core.agent.multi_agent',
    'app.core.agent.tool_registry',
    'app.core.agent.checkpoint_manager',
    'app.core.agent.plugins',
    'app.core.agent.plugins.basic_tools_plugin',
    'app.core.agent.plugins.file_editor_plugin',
    'app.core.agent.plugins.search_plugin',
    'app.core.agent.plugins.system_tools_plugin',
    'app.core.agent.plugins.data_process_plugin',
    'app.core.agent.plugins.image_process_plugin',
    'app.core.agent.plugins.network_plugin',
    'app.core.agent.plugins.script_generation_plugin',
    'app.core.agent.plugins.performance_analysis_plugin',
    'app.core.agent.plugins.trend_analysis_plugin',
    'app.core.agent.plugins.configuration_plugin',
    'app.core.agent.plugins.alerting_plugin',
    'app.core.agent.plugins.auto_remediation_plugin',
    'app.core.agent.plugins.system_event_monitoring_plugin',
    'app.core.agent.plugins.system_info_plugin',
    'app.core.analytics', 'app.core.analytics.trend_analyzer',
    'app.core.config', 'app.core.config.configuration_manager',
    'app.core.learning', 'app.core.learning.distill_manager',
    'app.core.learning.lora_pipeline',
    'app.core.learning.shadow_tracer',
    'app.core.learning.training_data_builder',
    'app.core.llm', 'app.core.llm.base',
    'app.core.llm.gemini', 'app.core.llm.langchain_adapter',
    'app.core.llm.ollama_provider',
    'app.core.monitoring',
    'app.core.monitoring.alert_manager',
    'app.core.monitoring.event_database',
    'app.core.monitoring.system_event_monitor',
    'app.core.remediation', 'app.core.remediation.remediation_manager',
    'app.core.scripts', 'app.core.scripts.script_generator',
    'app.core.security',
    'app.core.security.output_validator',
    'app.core.security.pii_filter',
    'app.core.services',
    'app.core.services.file_service',
    'app.core.services.rag_service',
    'app.core.services.search_service',
    'app.core.skills',
    'app.core.skills.skill_manager',
    'app.core.skills.skill_auto_builder',
    'app.core.skills.skill_recorder',
    'app.core.skills.skill_schema',
    'app.core.workflow',
    'app.core.workflow.interactive_planner',
    'app.core.workflow.langgraph_workflow',
    'app.api.agent_routes',
    'app.api.skill_routes',
    'app.api.skill_marketplace_routes',

    # ── 模型下载器 ──
    'model_downloader',

    # ── web/ 全部模块 ──
    'web.app',
    'web.analytics_engine', 'web.archive_search_engine', 'web.audio_overview',
    'web.audit_logger', 'web.auth', 'web.auth_manager',
    'web.auto_catalog_scheduler', 'web.auto_execution',
    'web.batch_file_ops', 'web.batch_processor',
    'web.behavior_monitor', 'web.browser_automation',
    'web.cache_manager', 'web.calendar_manager',
    'web.clipboard_manager', 'web.clipboard_ocr_assistant',
    'web.code_generator', 'web.collaboration_engine',
    'web.concept_extractor', 'web.config_manager',
    'web.consistency_checker', 'web.context_awareness',
    'web.context_injector', 'web.data_encryption',
    'web.data_pipeline', 'web.doc_converter', 'web.doc_planner',
    'web.document_annotator', 'web.document_batch_annotator_v2',
    'web.document_comparator', 'web.document_direct_edit',
    'web.document_editor', 'web.document_feedback',
    'web.document_generator', 'web.document_reader',
    'web.document_validator', 'web.document_workflow_executor',
    'web.email_manager',
    'web.enhanced_memory_manager', 'web.etl_engine',
    'web.excel_analyzer', 'web.feedback_loop',
    'web.file_analyzer', 'web.file_converter', 'web.file_editor',
    'web.file_indexer', 'web.file_organizer', 'web.file_parser',
    'web.file_processor', 'web.file_quality_checker', 'web.file_scanner',
    'web.folder_catalog_organizer', 'web.hotkey_manager',
    'web.image_generator', 'web.image_manager',
    'web.insight_reporter', 'web.intelligent_document_analyzer',
    'web.knowledge_base', 'web.knowledge_graph',
    'web.logger_setup', 'web.memory_api_routes',
    'web.memory_integration', 'web.memory_manager',
    'web.mini_koto', 'web.mobile_integration',
    'web.note_manager', 'web.notification_manager',
    'web.operation_history', 'web.organize_cleanup',
    'web.parallel_api', 'web.parallel_executor',
    'web.performance_monitor', 'web.permission_manager',
    'web.ppt_api_routes', 'web.ppt_generator', 'web.ppt_master',
    'web.ppt_pipeline', 'web.ppt_quality', 'web.ppt_session_manager',
    'web.ppt_synthesizer', 'web.ppt_themes', 'web.ppt_workflow',
    'web.proactive_dialogue', 'web.proactive_trigger',
    'web.processed_file_network', 'web.progress_tracker',
    'web.prompt_adapter', 'web.quality_evaluator',
    'web.rate_limiter', 'web.reminder_manager',
    'web.search_engine', 'web.security_manager',
    'web.settings', 'web.setup_local_model',
    'web.smart_feedback', 'web.speech_transcriber',
    'web.suggestion_annotator', 'web.suggestion_engine',
    'web.system_info', 'web.task_dispatcher', 'web.task_scheduler',
    'web.template_library', 'web.test_generator',
    'web.token_tracker', 'web.tool_registry',
    'web.track_changes_editor', 'web.voice_api_enhanced',
    'web.voice_fast', 'web.voice_input', 'web.voice_interaction',
    'web.voice_recognition_enhanced',
    'web.web_searcher', 'web.wechat_automation',
    'web.windows_notifier', 'web.workflow_manager',
]

# ═══════════════════════════════════════════════
# 安全收集整个包（collect_all 语义）
# ═══════════════════════════════════════════════
from PyInstaller.utils.hooks import collect_all

def _safe_collect(pkg):
    try:
        d, b, h = collect_all(pkg)
        return d, b, h
    except Exception:
        return [], [], []

_collect_pkgs = [
    'flask', 'flask_cors', 'jinja2', 'werkzeug',
    'google.genai', 'google.api_core', 'google.auth',
    'httpx', 'httpcore', 'anyio', 'certifi',
    'PIL', 'lxml', 'bs4',
    'pandas', 'numpy',
    'webview',
    'pystray',
    'cryptography',
    'langchain_core',
    'langchain_google_genai',
    'langgraph',
    'speech_recognition',  # 语音识别（SpeechRecognition 包）
    'pyaudio',             # 麦克风输入（需 portaudio.dll）
]

for _pkg in _collect_pkgs:
    _d, _b, _h = _safe_collect(_pkg)
    datas += _d
    hiddenimports += _h

# ═══════════════════════════════════════════════
# 过滤掉体积庞大但运行时无用的数据目录
# （主要是 numpy/pandas 的 tests/、docs/、benchmarks/ 等）
# ═══════════════════════════════════════════════
import re as _re
_SKIP_PATTERNS = [
    r'[\\/]tests[\\/]',
    r'[\\/]test[\\/]',
    r'[\\/]testing[\\/]',
    r'[\\/]benchmarks[\\/]',
    r'[\\/]_bench[\\/]',
    r'[\\/]docs[\\/]',
]
_skip_re = _re.compile('|'.join(_SKIP_PATTERNS), _re.IGNORECASE)

def _filter_datas(datas_list):
    kept, dropped = [], 0
    for src, dst in datas_list:
        if _skip_re.search(src.replace('\\', '/')):
            dropped += 1
        else:
            kept.append((src, dst))
    if dropped:
        print(f'[koto.spec] 已过滤 {dropped} 条测试/文档数据文件')
    return kept

datas = _filter_datas(datas)

# ═══════════════════════════════════════════════
# Analysis
# ═══════════════════════════════════════════════

a = Analysis(
    ['src/koto_setup.py'],       # ← 新入口（含下载器向导）
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(ROOT, 'src', 'rth_voice_fallback.py')],
    excludes=[
        'tkinter.test', 'unittest', 'test', 'tests',
        'setuptools', 'pip', 'distutils',
        'matplotlib', 'scipy', 'IPython', 'notebook',
        'pytest', 'pylint', 'black', 'mypy', 'flake8',
        'jupyter', 'nbconvert', 'nbformat',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx',
        # LoRA 训练依赖：体积庞大，按需单独安装，不打包进发行版
        'torch', 'torchvision', 'torchaudio',
        'transformers', 'peft', 'trl', 'accelerate', 'datasets', 'bitsandbytes',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Koto',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 容易被杀毒误报
    console=False,      # 无控制台窗口
    icon=os.path.join(ROOT, 'src', 'assets', 'koto_icon.ico'),
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Koto',         # 输出到 dist/Koto/
)
