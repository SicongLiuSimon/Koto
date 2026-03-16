"""
ChartVisionPlugin — 通过 Gemini 多模态视觉分析图表图片

工具：
  - analyze_chart_image(filepath, question?)  读取本地图片，调用 Gemini Vision 返回解读
  - analyze_screenshot(filepath, question?)   截图/界面分析，同上但提示词侧重 UI 解读
"""

import base64
import logging
import mimetypes
import os
from typing import Any, Dict, List

from app.core.agent.base import AgentPlugin

logger = logging.getLogger(__name__)

_SUPPORTED_MIME: set = {
    "image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp",
}
_EXT_MIME_MAP: dict = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
# Use a fast vision-capable model; fallback order handled in _call_vision
_VISION_MODEL = "gemini-2.0-flash"


class ChartVisionPlugin(AgentPlugin):
    """Send images to Gemini Vision for chart/screenshot analysis."""

    @property
    def name(self) -> str:
        return "ChartVision"

    @property
    def description(self) -> str:
        return "Analyze chart images and screenshots using Gemini multimodal vision."

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "analyze_chart_image",
                "func": self.analyze_chart_image,
                "description": (
                    "Send an image file (chart, graph, plot, dashboard screenshot) "
                    "to Gemini Vision and return a detailed interpretation: "
                    "chart type, axes, values, trends, and key insights. "
                    "Supports PNG, JPEG, WEBP, GIF, BMP."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "filepath": {
                            "type": "STRING",
                            "description": "Absolute or workspace-relative path to the image file.",
                        },
                        "question": {
                            "type": "STRING",
                            "description": (
                                "Optional specific question about the chart/image. "
                                "If omitted, a comprehensive chart analysis is returned."
                            ),
                        },
                    },
                    "required": ["filepath"],
                },
            },
            {
                "name": "analyze_screenshot",
                "func": self.analyze_screenshot,
                "description": (
                    "Send a UI screenshot or any image to Gemini Vision for analysis. "
                    "Use this for non-chart images (interfaces, documents, photos). "
                    "Supports PNG, JPEG, WEBP, GIF, BMP."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "filepath": {
                            "type": "STRING",
                            "description": "Absolute or workspace-relative path to the image file.",
                        },
                        "question": {
                            "type": "STRING",
                            "description": "Specific question or analysis focus for the image.",
                        },
                    },
                    "required": ["filepath"],
                },
            },
        ]

    # ------------------------------------------------------------------ #
    #  Public tool implementations                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def analyze_chart_image(filepath: str, question: str = "") -> str:
        """Analyze a chart/graph image using Gemini Vision."""
        if question:
            prompt = f"请分析这张图表，并回答以下问题：{question}"
        else:
            prompt = (
                "请对这张图表进行专业分析，包含以下结构化内容：\n\n"
                "### 1. 图表类型\n（柱状图 / 折线图 / 饼图 / 散点图 / 热力图 / 其他）\n\n"
                "### 2. 坐标轴与标题\n- 横轴：含义、单位、范围\n- 纵轴：含义、单位、范围\n\n"
                "### 3. 关键数据点\n- 最高值、最低值、平均水平\n- 特殊标注或标签\n\n"
                "### 4. 趋势与规律\n- 上升 / 下降 / 平稳 / 周期性\n- 断层或异常数据点\n\n"
                "### 5. 核心洞察（3 条）\n- 这张图表最重要的结论是什么？\n\n"
                "请用中文输出，结构清晰，数据引用准确。"
            )
        return ChartVisionPlugin._call_vision(filepath, prompt)

    @staticmethod
    def analyze_screenshot(filepath: str, question: str = "") -> str:
        """Analyze a screenshot or general image using Gemini Vision."""
        if question:
            prompt = f"请仔细观察这张图片并回答：{question}"
        else:
            prompt = (
                "请描述这张图片的内容：\n"
                "1. 图片整体描述（是什么？主要显示什么内容）\n"
                "2. 关键文字/数字信息（如界面文字、标注等）\n"
                "3. 值得注意的细节\n"
                "请用中文回答。"
            )
        return ChartVisionPlugin._call_vision(filepath, prompt)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_mime(filepath: str) -> str:
        mime_type, _ = mimetypes.guess_type(filepath)
        if not mime_type:
            ext = os.path.splitext(filepath)[1].lower()
            mime_type = _EXT_MIME_MAP.get(ext, "image/png")
        return mime_type

    @staticmethod
    def _call_vision(filepath: str, prompt: str) -> str:
        """Core: load image file → call Gemini Vision → return text result."""
        # ── validate file ──────────────────────────────────────────────
        if not os.path.isfile(filepath):
            return f"Error: 找不到图片文件：{filepath}"

        mime_type = ChartVisionPlugin._resolve_mime(filepath)
        if mime_type not in _SUPPORTED_MIME:
            return (
                f"Error: 不支持的图片格式 '{mime_type}'。"
                "支持的格式：PNG、JPEG、WEBP、GIF、BMP。"
            )

        # ── read bytes ────────────────────────────────────────────────
        try:
            with open(filepath, "rb") as fh:
                image_bytes = fh.read()
        except OSError as exc:
            return f"Error: 读取图片失败：{exc}"

        if len(image_bytes) > 20 * 1024 * 1024:
            return "Error: 图片文件过大（>20 MB），请先压缩后再分析。"

        # ── build Gemini client ────────────────────────────────────────
        api_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not api_key:
            return "Error: 未配置 Gemini API Key，无法进行图像分析。"

        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return "Error: 缺少 google-genai 包，请运行：pip install google-genai"

        try:
            client = genai.Client(api_key=api_key)
        except Exception as exc:
            return f"Error: 初始化 Gemini 客户端失败：{exc}"

        # ── call Vision API ────────────────────────────────────────────
        try:
            response = client.models.generate_content(
                model=_VISION_MODEL,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    types.Part.from_text(text=prompt),
                ],
            )
            result_text: str = getattr(response, "text", "") or ""
            if not result_text:
                return "Error: Gemini Vision 返回了空响应，请重试或检查图片内容。"
            return result_text
        except Exception as exc:
            logger.error(f"[ChartVisionPlugin] Vision API 调用失败: {exc}")
            return f"Error: Gemini Vision API 调用失败：{exc}"
