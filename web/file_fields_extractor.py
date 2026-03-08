"""
文档关键字段提取器
使用 Ollama 本地模型从文档内容中提取结构化字段：
  - 金额 / 合同金额
  - 关键日期（签署日、到期日、付款日）
  - 甲乙方 / 联系人
  - 主题摘要（1-2句）
"""
from __future__ import annotations

import json
import re
import socket
from datetime import datetime, date
from typing import Dict, List, Optional, Any

# ── Ollama 连接 ──────────────────────────────────────────────────────────────
_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434
_OLLAMA_URL = f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}"
_AI_MODEL = "qwen3:8b"

_EXTRACT_PROMPT = """\
你是文档信息提取助手。根据下方文档内容，提取所有关键字段。

只输出 JSON，不要解释，格式如下：
{
  "summary": "本文件的1-2句核心摘要",
  "parties": ["甲方名称", "乙方名称"],
  "amounts": [{"label": "合同金额", "value": "人民币50万元"}],
  "dates": [
    {"label": "签署日期", "value": "2026-03-01"},
    {"label": "合同到期", "value": "2027-03-01"},
    {"label": "付款截止", "value": "2026-04-15"}
  ],
  "contacts": [{"name": "张三", "phone": "138xxxx", "email": ""}],
  "key_terms": ["关键条款1", "关键条款2"]
}

若某字段内容为空则输出空数组/空字符串，不要省略该字段。
日期格式统一为 YYYY-MM-DD，无法确定则留空字符串。

文档内容：
"""


def _ollama_available() -> bool:
    try:
        s = socket.create_connection((_OLLAMA_HOST, _OLLAMA_PORT), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def extract_fields(file_name: str, content: str, file_type: str = "") -> Optional[Dict[str, Any]]:
    """
    用 Ollama 从文档内容中提取关键字段。
    返回 dict 或 None（Ollama 不可用时）。
    """
    if not content or not content.strip():
        return None
    if not _ollama_available():
        return None
    try:
        import requests as _req
        truncated = content[:3000]
        prompt = _EXTRACT_PROMPT + f"文件名: {file_name}\n文件类型: {file_type}\n---\n{truncated}"
        resp = _req.post(
            f"{_OLLAMA_URL}/api/generate",
            json={
                "model": _AI_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 512},
            },
            timeout=45,
        )
        if resp.status_code != 200:
            return None
        raw = resp.json().get("response", "")
        # 提取第一个 JSON 块
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        data = json.loads(m.group(0))
        # 标准化 dates 字段 —— 确保 value 是 YYYY-MM-DD 或空
        for d in data.get("dates", []):
            d["value"] = _normalize_date(d.get("value", ""))
        return data
    except Exception:
        return None


def _normalize_date(raw: str) -> str:
    """把各种日期写法统一为 YYYY-MM-DD，失败返回原字符串。"""
    if not raw:
        return ""
    # 已经是标准格式
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw.strip()):
        return raw.strip()
    # 中文格式: 2026年3月1日
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", raw.strip())
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            pass
    return raw.strip()


def fields_to_markdown(fields: Dict[str, Any], file_name: str = "") -> str:
    """把提取的字段格式化为 Markdown 卡片。"""
    lines = []
    if file_name:
        lines.append(f"**📄 {file_name}**\n")
    if fields.get("summary"):
        lines.append(f"> {fields['summary']}\n")
    if fields.get("parties"):
        lines.append(f"**当事方**: {' / '.join(fields['parties'])}")
    for a in fields.get("amounts", []):
        lines.append(f"**{a.get('label','金额')}**: {a.get('value','')}")
    for d in fields.get("dates", []):
        if d.get("value"):
            lines.append(f"**{d.get('label','日期')}**: {d['value']}")
    for c in fields.get("contacts", []):
        parts = [c.get("name", "")]
        if c.get("phone"):
            parts.append(c["phone"])
        if c.get("email"):
            parts.append(c["email"])
        if any(parts):
            lines.append(f"**联系人**: {' | '.join(p for p in parts if p)}")
    if fields.get("key_terms"):
        lines.append(f"**关键条款**: {'; '.join(fields['key_terms'][:3])}")
    return "\n".join(lines)
