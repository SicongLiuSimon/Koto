#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
图像管理器 - 统一处理图像搜索与生成
为 PPT 制作提供 "生辰大哥" (AI生成) 或 "网上找图" (Web Search) 的能力
"""

import base64
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests
import logging

# 尝试导入 web_searcher，如果失败则在方法内部导入

logger = logging.getLogger(__name__)

try:
    from web.web_searcher import search_with_grounding
except ImportError:
    search_with_grounding = None


class ImageManager:
    """图像资源管理器"""

    def __init__(self, client=None, workspace_dir: str = "workspace"):
        self.client = client
        self.workspace_dir = workspace_dir
        self.images_dir = os.path.join(workspace_dir, "images")
        os.makedirs(self.images_dir, exist_ok=True)

    def get_image(self, prompt: str, method: str = "auto") -> Optional[str]:
        """
        获取一张图像

        Args:
            prompt: 图像描述/搜索词
            method: "auto", "generate" (生辰大哥), "search" (网上找)

        Returns:
            本地图像路径 or None
        """
        logger.info(f"[ImageManager] 请求图像: {prompt}, 方式: {method}")

        # 自动决策逻辑
        if method == "auto":
            # 简单的启发式规则：包含"真实"、"照片"、"图表"、"数据"倾向于搜索
            # 包含"创意"、"插画"、"卡通"、"未来感"倾向于生成
            search_keywords = [
                "真实",
                "照片",
                "实拍",
                "图表",
                "数据",
                "logo",
                "标志",
                "截图",
                "剧照",
            ]
            if any(k in prompt.lower() for k in search_keywords):
                method = "search"
            else:
                method = "generate"
            logger.info(f"[ImageManager] 自动决策为: {method}")

        local_path = None

        if method == "generate":
            local_path = self._generate_image(prompt)
            # 如果生成失败，自动回退到搜索
            if not local_path:
                logger.info("[ImageManager] 生成失败，尝试回退到搜索...")
                local_path = self._search_image(prompt)

        elif method == "search":
            local_path = self._search_image(prompt)
            # 如果搜索失败，自动回退到生成
            if not local_path:
                logger.info("[ImageManager] 搜索失败，尝试回退到生成...")
                local_path = self._generate_image(prompt)

        return local_path

    def _generate_image(self, prompt: str) -> Optional[str]:
        """使用 AI 生成图像 - 多模型回退链"""
        logger.info(f"[ImageManager] 开始生成图像: {prompt}")
        if not self.client:
            logger.error("[ImageManager] ❌ 无 AI 客户端，无法生成")
            return None

        try:
            from google.genai import types

            # 构建更详细的绘图提示
            refined_prompt = (
                f"Create a clean, modern, professional illustration for a presentation slide. "
                f"Topic: {prompt}. "
                f"Style: flat design, clean layout, soft gradients, business-appropriate color palette. "
                f"Requirements: NO text, NO words, NO letters, NO numbers in the image. "
                f"Pure visual illustration only. 8k resolution."
            )

            # ========================================
            # 回退链 1: Gemini 3.1 Flash Image (原生图像生成)
            # ========================================
            try:
                model_name = "gemini-3.1-flash-image-preview"
                print(f"[ImageManager] 尝试模型: {model_name}")
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=refined_prompt,
                    config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
                )
                result = self._extract_image_from_response(response, prompt)
                if result:
                    logger.info(f"[ImageManager] ✅ {model_name} 成功")
                    return result
            except Exception as e1:
                logger.warning(f"[ImageManager] ⚠️ {model_name} 失败: {e1}")

            # ========================================
            # 回退链 2: Imagen 4.0 (高质量图像生成 API)
            # ========================================
            try:
                model_name = "imagen-4.0-generate-001"
                logger.info(f"[ImageManager] 尝试模型: {model_name}")
                response = self.client.models.generate_images(
                    model=model_name,
                    prompt=refined_prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1, aspect_ratio="16:9"
                    ),
                )
                if response.generated_images:
                    image_bytes = response.generated_images[0].image.image_bytes
                    filepath = self._save_image_bytes(image_bytes, prompt)
                    if filepath:
                        logger.info(f"[ImageManager] ✅ {model_name} 成功")
                        return filepath
            except Exception as e2:
                logger.warning(f"[ImageManager] ⚠️ {model_name} 失败: {e2}")

            # ========================================
            # 回退链 3: Imagen 4.0 其他备选
            # ========================================
            for imagen_model in [
                "imagen-4.0-fast-generate-001",
                "imagen-4.0-ultra-generate-001",
            ]:
                try:
                    logger.info(f"[ImageManager] 尝试模型: {imagen_model}")
                    response = self.client.models.generate_images(
                        model=imagen_model,
                        prompt=refined_prompt,
                        config=types.GenerateImagesConfig(
                            number_of_images=1, aspect_ratio="16:9"
                        ),
                    )
                    if response.generated_images:
                        image_bytes = response.generated_images[0].image.image_bytes
                        filepath = self._save_image_bytes(image_bytes, prompt)
                        if filepath:
                            logger.info(f"[ImageManager] ✅ {imagen_model} 成功")
                            return filepath
                except Exception as e3:
                    logger.warning(f"[ImageManager] ⚠️ {imagen_model} 失败: {e3}")

            # ========================================
            # 回退链 4: Gemini 多模态图像生成
            # ========================================
            for gemini_model in [
                "gemini-2.0-flash-exp-image-generation",
                "gemini-2.5-flash-image",
            ]:
                try:
                    logger.info(f"[ImageManager] 尝试模型: {gemini_model}")
                    response = self.client.models.generate_content(
                        model=gemini_model,
                        contents=f"Generate an image: {refined_prompt}",
                        config=types.GenerateContentConfig(
                            response_modalities=["IMAGE", "TEXT"]
                        ),
                    )
                    result = self._extract_image_from_response(response, prompt)
                    if result:
                        logger.info(f"[ImageManager] ✅ {gemini_model} 成功")
                        return result
                except Exception as e4:
                    logger.warning(f"[ImageManager] ⚠️ {gemini_model} 失败: {e4}")

            logger.error("[ImageManager] ❌ 所有图像生成模型均不可用")

        except Exception as e:
            logger.error(f"[ImageManager] ❌ 图像生成出错: {e}")

        return None

    def _extract_image_from_response(self, response, prompt: str) -> Optional[str]:
        """从 generate_content 响应中提取图像并保存"""
        if response and response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if (
                            hasattr(part, "inline_data")
                            and part.inline_data
                            and part.inline_data.data
                        ):
                            return self._save_image_bytes(part.inline_data.data, prompt)
        return None

    def _save_image_bytes(self, image_bytes: bytes, prompt: str) -> Optional[str]:
        """保存图像字节到文件"""
        try:
            filename = f"gen_{int(time.time())}_{abs(hash(prompt)) % 100000}.png"
            filepath = os.path.join(self.images_dir, filename)
            with open(filepath, "wb") as f:
                f.write(image_bytes)
            logger.info(f"[ImageManager] ✅ 图像已保存: {filepath} ({len(image_bytes)} bytes)")
            return filepath
        except Exception as e:
            logger.error(f"[ImageManager] ❌ 保存图像失败: {e}")
            return None

    def _search_image(self, query: str) -> Optional[str]:
        """
        网络搜索图像
        注意：Google Search Grounding 主要返回文本，但也可能包含 image tags 或 links
        这里我们做一个简单的模拟：
        1. 尝试搜索
        2. 如果结果中有图片 URL，下载之
        3. 如果没有，由于我们没有专门的 Image Search API，只能返回 None (并触发回退)
        """
        logger.info(f"[ImageManager] 开始搜索图像: {query}")

        # 真正的 Image Search 需要专门 API (如 SerpApi, Bing Search API)
        # Koto 目前只有 google_search tool (Grounding)
        # Grounding 有时会返回内嵌图片，但比较少

        # 为了演示 "NotebookLM" 能力，这里我们可以尝试请求 Grounding "Find an image of ..."
        # 但通常它只给链接。我们尝试解析链接。

        try:
            # 简化版：目前 Koto 的 search_with_grounding 只返回文本
            # 所以我们难以直接获得图片流。
            # 策略：如果无法真实搜索图片，暂时全部回退到 Generate，直到有了 Image Search API。
            # 或者：我们可以 "伪装" 一个搜索结果（如果 query 也是发给 AI 生成的，那就用 AI 画一个 "搜索结果"）

            # 为了满足用户需求 "图可以是生辰大哥(生成)也可以是网上找的"
            # 我们先优先使用生成，因为现有工具链对生成支持更好。
            # 如果一定要支持"网上找"，我们需要一个专门的 image scraper。
            # 鉴于环境限制，我们先打印日志，然后返回 None，让它回退到生成。

            # 除非... 我们有 Wikipedia API 或类似的？
            logger.warning("[ImageManager] ⚠️ 当前搜索接口仅支持文本，尝试回退到生成模式实现可视化...")
            return None

        except Exception as e:
            logger.error(f"[ImageManager] ❌ 搜索出错: {e}")

        return None
