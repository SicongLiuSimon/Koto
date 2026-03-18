#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量文档标注系统 - 直接修改模式 v2.0
核心特性：
1. 直接修改文本内容（不用Comments）
2. 改进的规则引擎（20+规则，分布均匀）
3. SSE实时反馈
4. 用户接受/拒绝UI支持
"""

import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Tuple

from web.document_direct_edit import ImprovedBatchAnnotator


def annotate_large_document(
    file_path: str,
    user_requirement: str = "把所有不合适的翻译、不符合中文语序逻辑、生硬的地方修改",
) -> Generator[str, None, None]:
    """
    流式标注大文档（直接修改模式）

    Args:
        file_path: Word文档路径
        user_requirement: 用户需求

    Yields:
        SSE事件流
    """
    annotator = ImprovedBatchAnnotator(batch_size=3)
    yield from annotator.annotate_document_streaming(file_path, user_requirement)
