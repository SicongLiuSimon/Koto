#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PPT多模型协作系统 - Master Orchestrator
高级PPT制作框架：任务分配、模型协作、智能排版

核心概念：
1. 资源管理 - 统一管理搜索结果、图像、数据
2. 内容规划 - 智能大纲和详细内容生成
3. 排版规划 - 根据内容自动决定最佳布局
4. 模型协作 - 文本模型、图像模型、规划模型协同
5. 高级合成 - 应用美化规则生成高质量PPT
"""

import os
import re
import json
import asyncio
import threading
from typing import List, Dict, Optional, Any, Tuple
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path


class SlideType(Enum):
    """幻灯片类型"""
    TITLE = "title"           # 标题页
    SECTION = "section"       # 章节页
    CONTENT = "content"       # 纯文字内容
    CONTENT_IMAGE = "content_image"  # 文字+图片
    IMAGE_FULL = "image_full"  # 全图片幻灯片
    COMPARISON = "comparison"  # 对比页
    FLOW = "flow"            # 流程图
    DATA = "data"            # 数据/表格
    SUMMARY = "summary"       # 总结页


class ContentDensity(Enum):
    """内容密度"""
    LIGHT = "light"           # 轻 (1-2个要点)
    MEDIUM = "medium"         # 中 (3-4个要点)
    DENSE = "dense"          # 密 (5+要点)


@dataclass
class SlideBlueprint:
    """幻灯片蓝图 - 包含所有排版和内容信息"""
    slide_index: int
    slide_type: SlideType
    title: str
    content: List[str] = field(default_factory=list)  # 主要内容/要点
    details: Dict[str, Any] = field(default_factory=dict)  # 详细内容
    image_prompts: List[str] = field(default_factory=list)  # 图像生成提示
    image_paths: List[str] = field(default_factory=list)  # 已有图像路径
    layout_config: Dict[str, Any] = field(default_factory=dict)  # 排版配置
    density: ContentDensity = ContentDensity.MEDIUM
    notes: str = ""           # 演讲备注
    color_scheme: Optional[str] = None  # 配色方案
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "slide_index": self.slide_index,
            "slide_type": self.slide_type.value,
            "title": self.title,
            "content": self.content,
            "details": self.details,
            "image_prompts": self.image_prompts,
            "image_paths": self.image_paths,
            "layout_config": self.layout_config,
            "density": self.density.value,
            "notes": self.notes,
            "color_scheme": self.color_scheme,
        }


@dataclass
class PPTBlueprint:
    """PPT总体蓝图 - 完整的制作计划"""
    title: str
    subtitle: str
    slides: List[SlideBlueprint] = field(default_factory=list)
    theme: str = "business"
    search_keywords: List[str] = field(default_factory=list)
    data_sources: Dict[str, Any] = field(default_factory=dict)
    image_count: int = 0
    deadline_quality: Dict[str, Any] = field(default_factory=dict)  # 质量目标
    generation_log: List[str] = field(default_factory=list)
    visual_style: str = "realistic" # realistic, illustration, minimal, data
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "slides": [s.to_dict() for s in self.slides],
            "theme": self.theme,
            "search_keywords": self.search_keywords,
            "image_count": self.image_count,
            "visual_style": self.visual_style,
        }
    
    def add_log(self, message: str):
        """添加生成日志"""
        self.generation_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")


class PPTResourceManager:
    """PPT资源管理器 - 统一管理所有资源"""
    
    def __init__(self):
        self.search_results: Dict[str, List[Dict]] = {}  # 关键词 -> 搜索结果
        self.images: Dict[str, List[str]] = {}  # 关键词 -> 图像路径
        self.generated_images: List[str] = []  # 新生成的图像
        self.data_cache: Dict[str, Any] = {}  # 数据缓存
        self.references: List[Dict] = []  # 参考资料
    
    def add_search_results(self, keyword: str, results: List[Dict]):
        """添加搜索结果"""
        self.search_results[keyword] = results
        if results:
            # 提取数据和参考
            for result in results[:3]:  # 前3个结果
                self.references.append({
                    "title": result.get("title", ""),
                    "url": result.get("url", ""),
                    "summary": result.get("content", "")[:200]
                })
    
    def add_images(self, keyword: str, image_paths: List[str]):
        """添加图像"""
        self.images[keyword] = image_paths
        self.generated_images.extend(image_paths)
    
    def get_best_images(self, keyword: str, count: int = 2) -> List[str]:
        """获取最佳图像"""
        return self.images.get(keyword, [])[:count]
    
    def get_summary_for_blueprint(self) -> Dict[str, Any]:
        """获取资源摘要"""
        return {
            "search_keywords_count": len(self.search_results),
            "total_search_results": sum(len(v) for v in self.search_results.values()),
            "generated_images_count": len(self.generated_images),
            "references_count": len(self.references),
        }


class PPTContentPlanner:
    """PPT内容规划器 - 生成详细的内容结构"""
    
    def __init__(self, ai_client=None, model_name: str = "gemini-2.5-flash"):
        self.ai_client = ai_client
        # 使用 gemini-2.5-flash 为默认，但优先使用能用的模型
        self.model_name = model_name
        self._fallback_models = ["gemini-2.5-flash", "gemini-3-flash-preview"]
    
    async def plan_content_structure(
        self,
        user_request: str,
        search_results: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        规划PPT的内容结构
        
        返回：
        {
            "outline": [{"title": "...", "points": [...], "key_data": {...}}],
            "theme": "...",
            "slide_count": 10,
            "key_topics": [...],
            "data_points": {...}
        }
        """
        
        if not self.ai_client:
            return self._generate_default_plan(user_request)
        
        try:
            search_context = ""
            if search_results:
                search_context = "\n\n搜索参考资料：\n"
                for result in search_results[:5]:
                    search_context += f"- {result.get('title', '')}: {result.get('content', '')[:100]}\n"
            
            prompt = f"""
            Task: Create a highly detailed, professional presentation plan.
            
            User Request: {user_request}
            {search_context}
            
            Instructions:
            1. **Structure**: Think like a strategy consultant. Organize content logically (e.g., Situation -> Complication -> Resolution).
            2. **Layout Variety**: DO NOT just use bullet points. Explicitly request 'comparison', 'data', 'flow', or 'image_full' layouts where appropriate.
            3. **Content Depth**: 
               - For 'key_points', create 3-5 punchy, action-oriented bullets.
               - For 'data_requirements', specify exactly what number/chart is needed (e.g., "Line chart showing 20% growth").
            4. **Visuals**: Suggest specific imagery that reinforces the message.

            Output strictly in JSON format matching this schema:
            {{
                "outline": [
                    {{
                        "section_title": "Section Name",
                        "slides": [
                            {{
                                "slide_title": "Engaging Title",
                                "slide_type": "content|content_image|comparison|data|flow|image_full",
                                "key_points": ["Point 1", "Point 2"],
                                "content_description": "Detailed narrative for speaker notes",
                                "layout_suggestion": "split_screen|big_number|timeline|pyramid",
                                "data_requirements": ["Value: 50%", "Label: Market Share"],
                                "image_suggestions": ["Photo of office team collaborating"]
                            }}
                        ]
                    }}
                ],
                "theme_recommendation": "modern_tech|corporate_blue|creative_minimal",
                "visual_style": "clean, flat design with high-quality photography"
            }}
            """
            
            # 尝试主模型和备选模型
            models_to_try = [self.model_name] + [m for m in self._fallback_models if m != self.model_name]
            last_error = None
            
            for model in models_to_try:
                # 尝试两种方式：先用 JSON 模式，失败后用普通文本模式
                for use_json_mode in [True, False]:
                    try:
                        config = {}
                        if use_json_mode:
                            config["response_mime_type"] = "application/json"
                        
                        contents = prompt
                        if not use_json_mode:
                            contents = prompt + "\n\n请严格返回纯JSON格式，不要包含任何其他文字或markdown标记。"
                        
                        call_state = {
                            "done": False,
                            "response": None,
                            "error": None,
                        }
                        done_event = threading.Event()

                        def _invoke_model():
                            try:
                                call_state["response"] = self.ai_client.models.generate_content(
                                    model=model,
                                    contents=contents,
                                    config=config if config else None,
                                )
                            except Exception as invoke_err:
                                call_state["error"] = invoke_err
                            finally:
                                call_state["done"] = True
                                done_event.set()

                        t = threading.Thread(target=_invoke_model, daemon=True)
                        t.start()

                        if not done_event.wait(timeout=25):
                            raise TimeoutError(f"模型调用超时 (model={model})")

                        if call_state["error"]:
                            raise call_state["error"]

                        response = call_state["response"]
                        
                        if response and response.text:
                            import json as json_mod
                            # 尝试解析 JSON，处理可能的 markdown 包裹
                            text = response.text.strip()
                            if text.startswith('```'):
                                text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
                            result = json_mod.loads(text)
                            mode_str = "JSON模式" if use_json_mode else "文本模式"
                            # [Safety Check: Empty content]
                            # If result has slides but empty content lists, try to fix it
                            try:
                                for s in result.get('outline', [])[0].get('slides', []):
                                    if not s.get('key_points'):
                                        s['key_points'] = ["(Content missing, please expand)"]
                            except: pass

                            print(f"[PPTContentPlanner] ✅ 初步内容规划成功 (model={model}, {mode_str})")
                            
                            # [Phase 1: Critique Step]
                            # Run critique synchronously here (or via thread if needed)
                            # ensuring valid context and preventing blocking for too long
                            try:
                                print("[PPTContentPlanner] >>> 启动大纲优化 (Critique Phase)...")
                                # Run critique in a separate thread to avoid blocking main loop
                                refined_result = {"data": None}
                                def _run_critique():
                                    refined_result["data"] = self._run_critique_sync(result, user_request)
                                
                                ct = threading.Thread(target=_run_critique, daemon=True)
                                ct.start()
                                ct.join(timeout=30) # 30s timeout for critique
                                
                                if refined_result["data"]:
                                    result = refined_result["data"]
                            except Exception as critique_err:
                                print(f"[PPTContentPlanner] ⚠️ 优化阶段跳过: {critique_err}")

                            return result
                    except Exception as model_err:
                        last_error = model_err
                        mode_str = "JSON模式" if use_json_mode else "文本模式"
                        print(f"[PPTContentPlanner] 模型 {model} ({mode_str}) 失败: {model_err}")
                        err_text = str(model_err)
                        if ("FAILED_PRECONDITION" in err_text and "location" in err_text.lower()) or "User location is not supported" in err_text:
                            print("[PPTContentPlanner] 检测到地区限制，跳过AI规划并回退默认方案")
                            return self._generate_default_plan(user_request)
                        continue
            
            # 所有模型都失败
            raise last_error or Exception("所有模型均失败")
        
        except Exception as e:
            print(f"[PPTContentPlanner] 内容规划失败，使用默认方案: {e}")
            return self._generate_default_plan(user_request)

    def _run_critique_sync(self, initial_plan: Dict[str, Any], user_request: str) -> Dict[str, Any]:
        """
        [Phase 1 Upgrade] 批评与优化大纲 (Sync Version)
        让 LLM 扮演 "PPT 专家" 来审查初始大纲，优化结构、减少文字量、增加视觉建议。
        """
        if not self.ai_client:
            return initial_plan

        try:
            prompt = f"""
            Task: **Critique & Upgrade** this presentation outline.
            Role: Strict Chief Design Officer.
            
            Original User Request: {user_request}
            
            Current Outline (JSON):
            {json.dumps(initial_plan, ensure_ascii=False, indent=2)}
            
            Issues to Fix:
            1. **Content Depth**: Ensure every slide has 3-5 comprehensive points. If points are brief, EXPAND them to be actionable.
            2. **Visual Variety**: Force at least 40% of slides to use specific layouts like 'comparison', 'data', 'flow'.
            3. **Data**: If numbers are mentioned, ensure there's a dedicated 'data' slide with specific values.
            4. **Flow**: Ensure a "Call to Action" or "Next Steps" slide exists at the end.
            
            IMPORTANT: Do not shorten the content. MAKE IT SUBSTANTIAL.
            
            Output the REFINED JSON structure with expanded content.
            """
            
            # 使用最强模型进行 critique
            model = "gemini-2.0-pro-exp-02-05" 
            
            # Fallback to current model if specialized one fails
            try:
                response = self.ai_client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config={"response_mime_type": "application/json"}
                )
            except Exception:
                 response = self.ai_client.models.generate_content(
                    model="gemini-2.5-flash", # Faster fallback
                    contents=prompt,
                    config={"response_mime_type": "application/json"}
                )
            
            if response and response.text:
                refined_plan = json.loads(response.text)
                print("[PPTContentPlanner] ✅ 大纲优化完成")
                return refined_plan
            
        except Exception as e:
            print(f"[PPTContentPlanner] ⚠️ 大纲优化失败，使用初始大纲: {e}")
            
        return initial_plan

    async def expand_slide_content(self, slide_title: str, points: List[str], context: str = "") -> List[str]:
        """扩充幻灯片内容（如果内容太少）"""
        if not self.ai_client:
            return points
        if len(points) >= 4 and all(len(p) > 10 for p in points):
            return points
            
        try:
            prompt = (
                f"扩充PPT幻灯片内容。\n"
                f"标题: {slide_title}\n"
                f"上下文: {context}\n"
                f"原有要点: {'; '.join(points)}\n\n"
                "请生成 4-6 个详细、专业的演讲要点。\n"
                "要求：\n"
                "- 每个要点包含具体信息/数据\n"
                "- 风格专业、简洁\n"
                "- 直接列出要点，不要其他废话\n"
                "- 严格返回 JSON 列表格式: [\"要点1\", \"要点2\"]"
            )
            
            # 使用快速模型
            model = "gemini-2.5-flash"
            
            call_state = {"response": None, "error": None}
            done_event = threading.Event()

            def _invoke():
                try:
                    call_state["response"] = self.ai_client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config={"response_mime_type": "application/json"}
                    )
                except Exception as e:
                    call_state["error"] = e
                finally:
                    done_event.set()

            t = threading.Thread(target=_invoke, daemon=True)
            t.start()
            if not done_event.wait(timeout=10):
                return points

            if call_state["error"] or not call_state["response"]:
                return points
                
            text = call_state["response"].text
            if text:
                import json
                expanded = json.loads(text)
                if isinstance(expanded, list) and len(expanded) > 0:
                    return expanded
            return points
        except Exception as e:
            print(f"[PPTContentPlanner] 扩充内容失败: {e}")
            return points

    def _generate_default_plan(self, user_request: str) -> Dict[str, Any]:
        """生成默认内容规划"""
        # 从请求中提取关键词
        keywords = re.findall(r'关于|的|是', user_request)
        
        return {
            "outline": [
                {
                    "section_title": "概述",
                    "slides": [
                        {
                            "slide_title": "标题页",
                            "slide_type": "content",
                            "key_points": ["主题介绍", "背景说明"],
                            "content_description": "演示文稿概览"
                        },
                        {
                            "slide_title": "内容概览",
                            "slide_type": "content",
                            "key_points": ["要点1", "要点2", "要点3"],
                            "content_description": "主要内容预览"
                        }
                    ]
                },
                {
                    "section_title": "主要内容",
                    "slides": [
                        {
                            "slide_title": "重点分析",
                            "slide_type": "content_image",
                            "key_points": ["分析点1", "分析点2"],
                            "image_suggestions": ["相关配图"],
                            "content_description": "深入分析"
                        }
                    ]
                },
                {
                    "section_title": "总结",
                    "slides": [
                        {
                            "slide_title": "要点回顾",
                            "slide_type": "content",
                            "key_points": ["核心总结"],
                            "content_description": "演示文稿总结"
                        },
                        {
                            "slide_title": "谢谢",
                            "slide_type": "content",
                            "key_points": ["联系方式"],
                            "content_description": "结束页"
                        }
                    ]
                }
            ],
            "total_expected_slides": 6,
            "theme_recommendation": "business",
            "layout_strategy": "标准布局，清晰明了"
        }


class PPTLayoutPlanner:
    """PPT排版规划器 - 为每个幻灯片规划最优布局"""
    
    def __init__(self):
        self.layout_rules = {
            "title_heavy": {  # 标题占比重
                "title_height": 0.4,
                "content_height": 0.6,
                "title_size": 54,
                "content_size": 28
            },
            "balanced": {  # 均衡布局
                "title_height": 0.2,
                "content_height": 0.8,
                "title_size": 44,
                "content_size": 24,
                "image_width": 0.4,
                "text_width": 0.6
            },
            "image_dominant": {  # 图片主导
                "title_height": 0.15,
                "image_height": 0.85,
                "image_width": 0.7,
                "title_size": 40
            },
            "two_column": {  # 两列布局
                "left_width": 0.45,
                "right_width": 0.45,
                "spacing": 0.1,
                "content_size": 22
            }
        }
    
    def plan_layout(
        self,
        slide_blueprint: SlideBlueprint,
        has_images: bool = False,
        content_count: int = 3
    ) -> Dict[str, Any]:
        """
        为幻灯片规划最优布局
        
        根据：
        - 幻灯片类型
        - 内容数量
        - 是否有图片
        
        返回布局配置
        """
        
        if slide_blueprint.slide_type == SlideType.TITLE:
            layout_key = "title_heavy"
        elif slide_blueprint.slide_type == SlideType.IMAGE_FULL:
            layout_key = "image_dominant"
        elif slide_blueprint.slide_type == SlideType.COMPARISON:
            layout_key = "two_column"
        elif slide_blueprint.slide_type == SlideType.CONTENT_IMAGE and has_images:
            layout_key = "balanced"
        else:
            layout_key = "balanced" if content_count <= 3 else "balanced"
        
        layout_config = self.layout_rules.get(layout_key, self.layout_rules["balanced"]).copy()
        
        # 根据内容密度调整
        if slide_blueprint.density == ContentDensity.DENSE:
            layout_config["content_size"] = max(18, layout_config.get("content_size", 24) - 4)
            layout_config["line_spacing"] = 1.3
        elif slide_blueprint.density == ContentDensity.LIGHT:
            layout_config["content_size"] = layout_config.get("content_size", 24) + 2
            layout_config["line_spacing"] = 1.6
        
        # 添加美化规则
        layout_config["bullet_style"] = self._choose_bullet_style(
            slide_blueprint.slide_type
        )
        layout_config["shadow_enabled"] = True
        layout_config["border_accent"] = True
        
        slide_blueprint.layout_config = layout_config
        return layout_config
    
    def _choose_bullet_style(self, slide_type: SlideType) -> str:
        """选择子弹符号风格"""
        styles = {
            SlideType.TITLE: "none",
            SlideType.SECTION: "none",
            SlideType.CONTENT: "circle",
            SlideType.CONTENT_IMAGE: "square",
            SlideType.COMPARISON: "none",
            SlideType.FLOW: "none",
            SlideType.DATA: "none",
            SlideType.SUMMARY: "checkmark"
        }
        return styles.get(slide_type, "circle")
    
    def optimize_slide_count(self, slide_count: int) -> int:
        """
        优化幻灯片数量
        保证最佳的演示体验（5-15张为宜）
        """
        if slide_count < 5:
            return 5  # 至少5张
        elif slide_count > 15:
            return 15  # 最多15张
        return slide_count


class PPTImageMatcher:
    """PPT图像匹配和优化器 - 为内容智能匹配或生成图像"""
    
    def __init__(self, ai_client=None):
        self.ai_client = ai_client
        self.image_cache = {}
    
    async def generate_image_prompts(
        self,
        slides: List[SlideBlueprint],
        theme: str = "professional"
    ) -> List[SlideBlueprint]:
        """
        为幻灯片生成图像提示
        """
        
        for slide in slides:
            if slide.slide_type in [
                SlideType.CONTENT_IMAGE,
                SlideType.IMAGE_FULL,
                SlideType.COMPARISON,
                SlideType.FLOW
            ]:
                # 生成高质量的图像提示
                prompts = self._generate_image_prompts_for_slide(slide, theme)
                slide.image_prompts = prompts
        
        return slides
    
    def _generate_image_prompts_for_slide(
        self,
        slide: SlideBlueprint,
        theme: str
    ) -> List[str]:
        """生成单个幻灯片的图像提示"""
        
        if slide.slide_type == SlideType.CONTENT_IMAGE:
            return [
                f"{slide.title} - 专业插图，{theme}风格，高质量，演示文稿用途"
            ]
        elif slide.slide_type == SlideType.IMAGE_FULL:
            return [
                f"{slide.title} - 全屏配图，{theme}风格，视觉冲击力强"
            ]
        elif slide.slide_type == SlideType.COMPARISON:
            return [
                f"对比图：{slide.title} - 左右对比布局，清晰展示差异",
                f"对比图：{slide.title} - 数据对比表现"
            ]
        elif slide.slide_type == SlideType.FLOW:
            return [
                f"流程图：{slide.title} - 箭头流程，清晰展示步骤"
            ]
        
        return []


class PPTMasterOrchestrator:
    """
    PPT主协调器 - 核心编排引擎
    
    负责：
    1. 解析用户需求
    2. 规划任务流程
    3. 协调不同模块
    4. 执行最终生成
    """
    
    def __init__(self, ai_client=None):
        self.ai_client = ai_client
        self.resource_manager = PPTResourceManager()
        self.content_planner = PPTContentPlanner(ai_client)
        self.layout_planner = PPTLayoutPlanner()
        self.image_matcher = PPTImageMatcher(ai_client)
        self.log = []
    
    async def orchestrate_ppt_generation(
        self,
        user_request: str,
        search_results: Optional[List[Dict]] = None,
        existing_images: Optional[List[str]] = None,
        model_callbacks: Optional[Dict[str, Any]] = None,
        progress_callback=None,
        **kwargs
    ) -> PPTBlueprint:
        """
        编排完整的PPT生成流程
        
        返回PPTBlueprint - 完整的制作计划和蓝图
        """
        
        def _report(msg):
            if progress_callback:
                try:
                    progress_callback(msg)
                except: pass
            self._log(msg)

        _report(f"开始编排PPT生成: {user_request[:50]}...")
        
        # 1. 添加资源
        if search_results:
            self.resource_manager.add_search_results("main", search_results)
        if existing_images:
            self.resource_manager.add_images("main", existing_images)
        
        # 2. 规划内容结构
        _report("步骤1: 规划内容结构 (Content Planning)...")
        content_plan = await self.content_planner.plan_content_structure(
            user_request,
            search_results
        )
        
        # 3. 创建PPT蓝图
        _report("步骤2: 创建PPT蓝图 (Blueprint Creation)...")
        blueprint = self._create_blueprint_from_plan(user_request, content_plan)
        
        # 3.5 内容增强（Per-Slide Content Expansion）
        _report("步骤2.5: 幻灯片内容细节扩充 (Detail Expansion)...")
        expanded_count = 0
        tasks = []
        for slide in blueprint.slides:
            if slide.slide_type.value in ["content", "content_image", "comparison"] and len(slide.content) < 4:
                # 只针对内容型幻灯片且内容较少时进行扩充
                tasks.append(
                    self.content_planner.expand_slide_content(
                        slide.title, 
                        slide.content, 
                        context=f"Theme: {blueprint.theme}"
                    )
                )
            else:
                tasks.append(asyncio.sleep(0, result=slide.content)) # placeholder

        results = await asyncio.gather(*tasks)
        for i, res in enumerate(results):
             if len(res) > len(blueprint.slides[i].content):
                 blueprint.slides[i].content = res
                 expanded_count += 1
        
        if expanded_count > 0:
            self._log(f"   ✨ 已扩充 {expanded_count} 张幻灯片的详细内容")

        # 4. 规划每个幻灯片的排版
        self._log("步骤3: 规划排版布局")
        blueprint.slides = await self._plan_all_layouts(blueprint.slides)
        
        # 5. 生成图像提示
        self._log("步骤4: 生成图像提示")
        blueprint.slides = await self.image_matcher.generate_image_prompts(
            blueprint.slides,
            blueprint.theme
        )
        
        # 6. 应用模型回调（如需要）
        if model_callbacks:
            self._log("步骤5: 应用模型回调")
            await self._apply_model_callbacks(blueprint, model_callbacks)
        
        # 7. 最终优化
        self._log("步骤6: 最终优化")
        blueprint = self._finalize_blueprint(blueprint)
        
        # 8. 添加资源摘要
        blueprint.deadline_quality = self.resource_manager.get_summary_for_blueprint()
        blueprint.generation_log = self.log
        
        self._log(f"PPT蓝图编排完成，包含 {len(blueprint.slides)} 张幻灯片")
        return blueprint
    
    def _create_blueprint_from_plan(
        self,
        user_request: str,
        content_plan: Dict[str, Any]
    ) -> PPTBlueprint:
        """从内容规划创建PPT蓝图"""
        
        # 提取标题
        title_match = re.search(r'关于(.{2,20}?)(的|：|,)', user_request)
        title = title_match.group(1).strip() if title_match else "专业演示文稿"
        
        blueprint = PPTBlueprint(
            title=title,
            subtitle=content_plan.get("theme_recommendation", "演示"),
            theme=self._map_theme(content_plan.get("theme_recommendation", "business"))
        )
        
        # 从大纲创建幻灯片
        slide_index = 0
        
        # 添加标题页
        title_slide = SlideBlueprint(
            slide_index=slide_index,
            slide_type=SlideType.TITLE,
            title=title,
            content=[content_plan.get("theme_recommendation", "演示")],
            density=ContentDensity.LIGHT
        )
        blueprint.slides.append(title_slide)
        slide_index += 1
        
        # 处理大纲中的每个章节
        outline = content_plan.get("outline", [])
        for section in outline:
            section_title = section.get("section_title", "")
            section_slides = section.get("slides", [])
            
            # 添加章节页
            section_slide = SlideBlueprint(
                slide_index=slide_index,
                slide_type=SlideType.SECTION,
                title=section_title,
                content=[],
                density=ContentDensity.LIGHT
            )
            blueprint.slides.append(section_slide)
            slide_index += 1
            
            # 添加各个幻灯片
            for slide_info in section_slides:
                slide_title = slide_info.get("slide_title", "")
                slide_type = self._map_slide_type(slide_info.get("slide_type", "content"))
                key_points = slide_info.get("key_points", [])

                
                # Expand content if sparse (Interactive Planning - Phase 2 Detail Gen)
                if len(key_points) < 4:
                     # Since this is sync code but network call is async, we need a way to call it.
                     # However, orchestrate_ppt_generation handles blueprint creation.
                     # But _create_blueprint_from_plan is synchronous here.
                     # Let's mark it for expansion or just leave it as is for now.
                     pass 
                
                slide = SlideBlueprint(
                    slide_index=slide_index,
                    slide_type=slide_type,
                    title=slide_title,
                    content=key_points,
                    details={
                        "description": slide_info.get("content_description", ""),
                        "data_requirements": slide_info.get("data_requirements", []),
                        "emphasis_words": slide_info.get("emphasis_words", [])
                    },
                    image_prompts=slide_info.get("image_suggestions", []),
                    density=ContentDensity.MEDIUM
                )
                blueprint.slides.append(slide)
                slide_index += 1
        
        # 添加结束页
        end_slide = SlideBlueprint(
            slide_index=slide_index,
            slide_type=SlideType.SUMMARY,
            title="谢谢",
            content=["感谢观看"],
            density=ContentDensity.LIGHT
        )
        blueprint.slides.append(end_slide)
        
        return blueprint
    
    async def _plan_all_layouts(self, slides: List[SlideBlueprint]) -> List[SlideBlueprint]:
        """为所有幻灯片规划布局"""
        
        for slide in slides:
            has_images = len(slide.image_paths) > 0 or len(slide.image_prompts) > 0
            self.layout_planner.plan_layout(
                slide,
                has_images=has_images,
                content_count=len(slide.content)
            )
        
        return slides
    
    async def _apply_model_callbacks(
        self,
        blueprint: PPTBlueprint,
        callbacks: Dict[str, Any]
    ):
        """应用模型回调来增强蓝图"""
        
        if "enhance_content" in callbacks:
            self._log("应用内容增强回调")
            # 可以调用外部的content enhancement模型
            pass
        
        if "generate_images" in callbacks:
            self._log("应用图像生成回调")
            # 可以调用图像生成模型
            pass
    
    def _finalize_blueprint(self, blueprint: PPTBlueprint) -> PPTBlueprint:
        """最终化蓝图 - 应用优化和美化规则"""
        
        # 优化幻灯片数量
        target_count = self.layout_planner.optimize_slide_count(len(blueprint.slides))
        if len(blueprint.slides) > target_count:
            self._log(f"合并过多的幻灯片：{len(blueprint.slides)} -> {target_count}")
            blueprint.slides = blueprint.slides[:target_count]
        
        # 应用配色方案
        for slide in blueprint.slides:
            slide.color_scheme = self._choose_color_scheme(slide.slide_type, blueprint.theme)
        
        # 添加演讲备注
        for slide in blueprint.slides:
            if slide.slide_type == SlideType.CONTENT:
                slide.notes = " ".join(slide.content)
        
        return blueprint
    
    def _map_theme(self, theme_str: str) -> str:
        """映射主题名称"""
        theme_lower = str(theme_str).lower()
        if "tech" in theme_lower or "技术" in theme_str:
            return "tech"
        elif "creative" in theme_lower or "创意" in theme_str:
            return "creative"
        return "business"
    
    def _map_slide_type(self, type_str: str) -> SlideType:
        """映射幻灯片类型"""
        type_lower = str(type_str).lower()
        type_map = {
            "content": SlideType.CONTENT,
            "content_image": SlideType.CONTENT_IMAGE,
            "image": SlideType.IMAGE_FULL,
            "comparison": SlideType.COMPARISON,
            "flow": SlideType.FLOW,
            "data": SlideType.DATA,
        }
        return type_map.get(type_lower, SlideType.CONTENT)
    
    def _choose_color_scheme(self, slide_type: SlideType, theme: str) -> str:
        """选择配色方案"""
        if slide_type in [SlideType.TITLE, SlideType.SECTION]:
            return "primary"  # 主色调
        elif slide_type == SlideType.SUMMARY:
            return "accent"   # 强调色
        else:
            return "neutral"  # 中性色
    
    def _log(self, message: str):
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{timestamp}] {message}"
        self.log.append(log_msg)
        print(f"[PPTMasterOrchestrator] {log_msg}")
    
    def get_blueprint_summary(self, blueprint: PPTBlueprint) -> Dict[str, Any]:
        """获取蓝图摘要"""
        return {
            "title": blueprint.title,
            "slide_count": len(blueprint.slides),
            "theme": blueprint.theme,
            "total_content_points": sum(len(s.content) for s in blueprint.slides),
            "image_heavy_slides": len([s for s in blueprint.slides if s.slide_type == SlideType.CONTENT_IMAGE]),
            "has_layout_plans": all(s.layout_config for s in blueprint.slides),
            "resource_summary": self.resource_manager.get_summary_for_blueprint(),
            "generation_steps": len(self.log)
        }
