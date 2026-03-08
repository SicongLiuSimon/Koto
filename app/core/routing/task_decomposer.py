class TaskDecomposer:
    """
    分解复杂任务为多个子任务
    
    例如：
    输入: "查黄金价格，生成一个价格波动表格"
    分解为:
      1. WEB_SEARCH: 查询黄金价格历史数据
      2. FILE_GEN: 基于数据生成 Excel/Word 表格
      3. 返回完整的文件和数据
    """
    
    # 定义常见的任务组合模式
    TASK_PATTERNS = {
        # (源任务，目标任务) -> 组合类型
        ("WEB_SEARCH", "FILE_GEN"): "search_and_document",  # 查询并生成文档
        ("WEB_SEARCH", "PAINTER"): "search_and_visualize",  # 查询并可视化
        ("RESEARCH", "FILE_GEN"): "research_and_document",  # 研究并生成文档
        ("FILE_GEN", "FILE_GEN"): "sequential_generation",  # 多步文件生成
        ("CHAT", "FILE_GEN"): "discuss_and_document",       # 讨论并生成文档
        ("PAINTER", "FILE_GEN"): "image_and_document",      # 生成图像并放入文档
        ("MULTI", "PPT"): "enhanced_ppt_generation",        # 增强型PPT生成（多模型协作）
    }
    
    @classmethod
    def detect_compound_task(cls, user_input: str, initial_task: str) -> dict:
        """
        检测是否是复杂任务（包含多个操作）
        
        返回:
            {
                "is_compound": bool,
                "primary_task": str,      # 主任务类型
                "secondary_tasks": [],    # 次要任务类型列表
                "pattern": str,           # 任务模式
                "subtasks": []            # 分解后的子任务列表
            }
        """
        result = {
            "is_compound": False,
            "primary_task": initial_task,
            "secondary_tasks": [],
            "pattern": None,
            "subtasks": []
        }
        
        text_lower = user_input.lower()
        
        # 检测关键短语，标志复合任务
        compound_indicators = [
            # 查询 + 生成文档 (更宽松的匹配)
            ("查", "表格"), ("查", "文档"), ("查", "做成"), ("查", "生成"),
            ("查询", "生成"), ("查询", "做表格"), ("查询", "做成"),
            ("价格", "表格"), ("价格", "文档"),  # 新增
            ("搜索", "生成"), ("搜索", "做表格"),
            # 查询 + 可视化
            ("查询", "画图"), ("查询", "绘制"), ("查询", "生成图"),
            # 研究 + 生成
            ("研究", "写份"), ("分析", "生成文档"), ("研究", "文档"),
            # 生成 + 优化
            ("生成", "优化"), ("做个", "改进"),
            # 分析 + 生成
            ("分析", "生成"), ("分析", "做成"), ("分析", "表格"), ("分析", "报告"),
            ("整理", "生成"), ("整理", "做成"), ("整理", "表格"), ("整理", "文档"),
            # 先…再…（自然语言多步）
            ("先", "再"), ("首先", "然后"), ("先", "然后"),
            # 汇总 + 文档
            ("汇总", "文档"), ("汇总", "报告"), ("汇总", "表格"),
            ("收集", "整理"), ("收集", "生成"), ("收集", "写"),
            # 英文
            ("search", "generate"), ("search", "create"), ("research", "document"),
            ("analyze", "generate"), ("collect", "report"),
        ]
        
        # 先检查是否包含复合指标
        has_compound_indicator = False
        for indicators in compound_indicators:
            if all(ind in text_lower for ind in indicators):
                has_compound_indicator = True
                result["is_compound"] = True
                break
        
        # 额外检查：包含"然后"、"再"、"接着"等连接词
        if not has_compound_indicator:
            connector_words = ["然后", "再", "接着", "之后", "后", "并且", "同时"]
            if any(conn in text_lower for conn in connector_words):
                # 检查是否有多个动作词
                action_words = ["查", "搜", "生成", "做", "写", "画", "创建", "制作"]
                action_count = sum(1 for action in action_words if action in text_lower)
                if action_count >= 2:
                    result["is_compound"] = True
        
        # ===== 文档工作流检测 (Document Workflow Detection) =====
        doc_workflow_keywords = ["执行文档", "运行文档", "按文档", "文档流程", "文档工作流", "按照.*执行", "按.*流程"]
        has_doc_workflow = any(keyword in text_lower for keyword in doc_workflow_keywords)
        
        if has_doc_workflow:
            result["is_compound"] = True
            result["primary_task"] = "DOC_WORKFLOW"
            result["pattern"] = "document_workflow"
            result["subtasks"] = [
                {
                    "task_type": "DOC_WORKFLOW",
                    "description": "加载并执行文档中定义的工作流",
                    "input": user_input,
                    "expected_output": "工作流执行结果"
                }
            ]
            result["is_multi_step_task"] = True
            result["multi_step_info"] = {
                "pattern": "document_workflow",
                "requires_document": True,
                "quality_level": "high"
            }
            return result
        
        # ===== PPT请求已在 SmartDispatcher 中直通 FILE_GEN，此处不再作为复合任务处理 =====
        # PPT 专用管线已内置搜索、深度研究、内容充实、配图生成功能，无需拆分为多步任务
        
        # 如果明确不是复合任务，直接返回
        if not result["is_compound"]:
            return result
        
        # ===== 检测其他任务组合 =====
        
        # 查询价格 + 生成表格/文档 (更宽松的匹配)
        search_keywords = ["查", "搜", "搜索", "查询", "价格", "黄金", "股票", "比特币"]
        doc_keywords = ["表格", "文档", "报告", "做成", "生成", "做个", "做一个", "excel", "word"]
        
        has_search = any(k in text_lower for k in search_keywords)
        has_doc = any(k in text_lower for k in doc_keywords)
        
        if has_search and has_doc:
            result["primary_task"] = "WEB_SEARCH"
            result["secondary_tasks"] = ["FILE_GEN"]
            result["pattern"] = "search_and_document"
            result["subtasks"] = [
                {
                    "task_type": "WEB_SEARCH",
                    "description": "查询数据",
                    "input": user_input,
                    "expected_output": "实时数据"
                },
                {
                    "task_type": "FILE_GEN",
                    "description": "根据数据生成表格/文档",
                    "input": "来自WEB_SEARCH的数据",
                    "expected_output": "格式化的表格/文档"
                }
            ]
            return result
        
        # 搜索/研究 + 文档/报告/word
        # 支持的模式: "研究XXX并做一个word", "研究XXX并生成报告", "查询XXX写份报告" 等
        if any(k in text_lower for k in ["搜索", "查询", "研究", "研究一下"]) and \
           any(k in text_lower for k in ["写份", "生成报告", "做份", "写个报告", "做一个word", "做个word", "做一个文档", "做个文档", "做一个总结", "做个总结"]):
            result["primary_task"] = "WEB_SEARCH" if "搜索" in text_lower or "查询" in text_lower else "RESEARCH"
            result["secondary_tasks"] = ["FILE_GEN"]
            result["pattern"] = "search_and_document"
            result["subtasks"] = [
                {
                    "task_type": result["primary_task"],
                    "description": "收集信息",
                    "input": user_input,
                    "expected_output": "详细的信息和数据"
                },
                {
                    "task_type": "FILE_GEN",
                    "description": "生成报告文档",
                    "input": "来自前一步的数据",
                    "expected_output": "完整的报告文档"
                }
            ]
            return result
        
        # 其他复合任务 (如果需要可继续添加)
        if result["is_compound"]:
            # 如果检测到是复合任务但不匹配具体模式，还是记录为复合
            result["secondary_tasks"] = ["FILE_GEN"]  # 默认最后生成文档
        
        return result
    
    @classmethod
    def create_subtasks(cls, original_input: str, compound_info: dict) -> list:
        """
        根据分解信息创建具体的子任务
        """
        subtasks = []
        
        for i, task_template in enumerate(compound_info["subtasks"]):
            subtask = {
                "id": i + 1,
                "task_type": task_template["task_type"],
                "description": task_template["description"],
                "original_input": original_input,
                "index": i,
                "status": "pending",
                "result": None,
                "error": None
            }
            if task_template.get("input"):
                subtask["input"] = task_template["input"]
            if task_template.get("expected_output"):
                subtask["expected_output"] = task_template["expected_output"]
            subtasks.append(subtask)
        
        return subtasks
