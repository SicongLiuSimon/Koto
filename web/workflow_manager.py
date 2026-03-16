#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工作流管理系统 - Phase 5
支持：工作流定义、保存、加载、执行、模板库管理
"""

import json
import os
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path
import logging


logger = logging.getLogger(__name__)

class Workflow:
    """工作流定义"""
    
    def __init__(self, name: str, description: str = "", steps: List[Dict] = None):
        self.id = name.replace(" ", "_")
        self.name = name
        self.description = description
        self.steps = steps or []
        self.variables = {}  # 工作流变量模板
        self.tags = []
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.execution_count = 0
        self.is_template = False  # 是否为模板
    
    def add_step(self, step_name: str, step_type: str, config: Dict = None):
        """添加工作流步骤"""
        step = {
            "name": step_name,
            "type": step_type,  # "agent", "tool", "conditional", "parallel"
            "config": config or {},
            "order": len(self.steps)
        }
        self.steps.append(step)
        return step
    
    def set_variable(self, var_name: str, default_value: Any = None, description: str = ""):
        """定义工作流变量"""
        self.variables[var_name] = {
            "default": default_value,
            "description": description,
            "required": default_value is None
        }
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "steps": self.steps,
            "variables": self.variables,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "execution_count": self.execution_count,
            "is_template": self.is_template
        }
    
    @staticmethod
    def from_dict(data: Dict) -> "Workflow":
        """从字典创建"""
        wf = Workflow(data.get("name", ""), data.get("description", ""), data.get("steps", []))
        wf.id = data.get("id", wf.id)
        wf.variables = data.get("variables", {})
        wf.tags = data.get("tags", [])
        wf.created_at = data.get("created_at", wf.created_at)
        wf.updated_at = data.get("updated_at", wf.updated_at)
        wf.execution_count = data.get("execution_count", 0)
        wf.is_template = data.get("is_template", False)
        return wf


class WorkflowManager:
    """工作流管理器"""
    
    def __init__(self, storage_dir: str = "config/workflows"):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        
        self.workflows = {}  # 工作流缓存
        self._load_all()
    
    def _load_all(self):
        """加载所有工作流"""
        try:
            for file in Path(self.storage_dir).glob("*.json"):
                try:
                    with open(file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        wf = Workflow.from_dict(data)
                        self.workflows[wf.id] = wf
                except Exception as e:
                    logger.info(f"[WorkflowManager] 加载工作流失败: {file}: {e}")
        except Exception as e:
            logger.info(f"[WorkflowManager] 加载工作流目录失败: {e}")
    
    def save_workflow(self, workflow: Workflow) -> bool:
        """保存工作流到磁盘"""
        try:
            workflow.updated_at = datetime.now().isoformat()
            file_path = os.path.join(self.storage_dir, f"{workflow.id}.json")
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(workflow.to_dict(), f, ensure_ascii=False, indent=2)
            
            self.workflows[workflow.id] = workflow
            return True
        except Exception as e:
            logger.info(f"[WorkflowManager] 保存工作流失败: {e}")
            return False
    
    def load_workflow(self, workflow_id: str) -> Optional[Workflow]:
        """加载工作流"""
        if workflow_id in self.workflows:
            return self.workflows[workflow_id]
        
        file_path = os.path.join(self.storage_dir, f"{workflow_id}.json")
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    wf = Workflow.from_dict(data)
                    self.workflows[workflow_id] = wf
                    return wf
            except Exception as e:
                logger.info(f"[WorkflowManager] 加载工作流失败: {e}")
        
        return None
    
    def delete_workflow(self, workflow_id: str) -> bool:
        """删除工作流"""
        try:
            file_path = os.path.join(self.storage_dir, f"{workflow_id}.json")
            if os.path.exists(file_path):
                os.remove(file_path)
            
            self.workflows.pop(workflow_id, None)
            return True
        except Exception as e:
            logger.info(f"[WorkflowManager] 删除工作流失败: {e}")
            return False
    
    def list_workflows(self, tags: List[str] = None, is_template: bool = None) -> List[Workflow]:
        """列出工作流"""
        results = []
        
        for wf in self.workflows.values():
            # 按标签过滤
            if tags and not any(tag in wf.tags for tag in tags):
                continue
            
            # 按模板类型过滤
            if is_template is not None and wf.is_template != is_template:
                continue
            
            results.append(wf)
        
        return sorted(results, key=lambda x: x.updated_at, reverse=True)
    
    def create_workflow(self, name: str, description: str = "", steps: List[Dict] = None) -> Workflow:
        """创建新工作流"""
        wf = Workflow(name, description, steps)
        self.save_workflow(wf)
        return wf
    
    def clone_workflow(self, source_id: str, new_name: str) -> Optional[Workflow]:
        """克隆工作流"""
        source = self.load_workflow(source_id)
        if not source:
            return None
        
        cloned = Workflow(new_name, source.description, [step.copy() for step in source.steps])
        cloned.variables = source.variables.copy()
        cloned.tags = source.tags.copy()
        cloned.is_template = source.is_template
        
        self.save_workflow(cloned)
        return cloned
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_workflows": len(self.workflows),
            "total_templates": sum(1 for w in self.workflows.values() if w.is_template),
            "total_executions": sum(w.execution_count for w in self.workflows.values()),
            "most_used": self._get_most_used(3),
            "recently_updated": self._get_recently_updated(3)
        }
    
    def _get_most_used(self, limit: int = 3) -> List[Dict]:
        """获取最常用的工作流"""
        sorted_wfs = sorted(self.workflows.values(), key=lambda x: x.execution_count, reverse=True)
        return [{"name": w.name, "executions": w.execution_count} for w in sorted_wfs[:limit]]
    
    def _get_recently_updated(self, limit: int = 3) -> List[Dict]:
        """获取最近更新的工作流"""
        sorted_wfs = sorted(self.workflows.values(), key=lambda x: x.updated_at, reverse=True)
        return [{"name": w.name, "updated": w.updated_at} for w in sorted_wfs[:limit]]


class WorkflowExecutor:
    """工作流执行器"""
    
    def __init__(self, agent_planner=None, agent_loop=None):
        self.agent_planner = agent_planner
        self.agent_loop = agent_loop
        self.execution_history = []
    
    def execute(
        self,
        workflow: Workflow,
        variables: Dict[str, Any] = None,
        session: str = None,
        callbacks: Dict = None
    ) -> Dict[str, Any]:
        """
        执行工作流
        
        Args:
            workflow: 工作流对象
            variables: 变量赋值
            session: 会话ID
            callbacks: 回调函数 {"on_step_start", "on_step_done", "on_error"}
        
        Returns:
            执行结果
        """
        
        callbacks = callbacks or {}
        variables = variables or {}
        execution_id = f"{workflow.id}_{int(datetime.now().timestamp() * 1000)}"
        
        execution = {
            "id": execution_id,
            "workflow_id": workflow.id,
            "workflow_name": workflow.name,
            "status": "running",
            "start_time": datetime.now().isoformat(),
            "end_time": None,
            "steps_completed": 0,
            "steps_failed": 0,
            "results": []
        }
        
        try:
            # 执行每一步
            for step_index, step in enumerate(workflow.steps):
                step_start = datetime.now()
                step_result = {
                    "step": step_index + 1,
                    "name": step.get("name", ""),
                    "type": step.get("type", ""),
                    "status": "pending",
                    "result": None,
                    "error": None
                }
                
                try:
                    # 调用回调
                    if "on_step_start" in callbacks:
                        callbacks["on_step_start"](step_index + 1, step)
                    
                    # 执行步骤
                    result = self._execute_step(step, variables, session)
                    
                    step_result["status"] = "completed"
                    step_result["result"] = result
                    execution["steps_completed"] += 1
                    
                except Exception as e:
                    step_result["status"] = "failed"
                    step_result["error"] = str(e)
                    execution["steps_failed"] += 1
                    
                    if "on_error" in callbacks:
                        callbacks["on_error"](step_index + 1, step, e)
                
                finally:
                    step_result["duration"] = (datetime.now() - step_start).total_seconds()
                    execution["results"].append(step_result)
                    
                    if "on_step_done" in callbacks:
                        callbacks["on_step_done"](step_index + 1, step_result)
            
            execution["status"] = "completed"
            
        except Exception as e:
            execution["status"] = "error"
            execution["error"] = str(e)
        
        finally:
            execution["end_time"] = datetime.now().isoformat()
            execution["duration"] = (datetime.now() - datetime.fromisoformat(execution["start_time"])).total_seconds()
            
            # 更新工作流的执行次数
            workflow.execution_count += 1
            
            # 保存执行历史
            self.execution_history.append(execution)
        
        return execution
    
    def _execute_step(self, step: Dict, variables: Dict, session: str = None) -> Any:
        """执行单个步骤"""
        
        step_type = step.get("type", "")
        step_config = step.get("config", {})
        
        if step_type == "agent":
            # 使用 agent 执行
            if self.agent_loop:
                # 使用 agent_loop 对话
                request = step_config.get("request", "")
                # 这里需要用 agent_loop 执行
                return {"type": "agent", "request": request, "status": "executed"}
            else:
                raise Exception("Agent 执行器未初始化")
        
        elif step_type == "tool":
            # 使用工具执行
            tool_name = step_config.get("tool", "")
            tool_args = step_config.get("args", {})
            # 这里需要基于 tool_registry 执行
            return {"type": "tool", "tool": tool_name, "args": tool_args, "status": "executed"}
        
        elif step_type == "conditional":
            # 条件判断
            condition = step_config.get("condition", "")
            # 执行条件逻辑
            return {"type": "conditional", "condition": condition, "status": "evaluated"}
        
        elif step_type == "parallel":
            # 并行执行
            parallel_steps = step_config.get("steps", [])
            # 并行执行多个子步骤
            return {"type": "parallel", "steps": len(parallel_steps), "status": "executed"}
        
        elif step_type == "delay":
            # 延迟
            delay_seconds = step_config.get("seconds", 0)
            import time
            time.sleep(min(delay_seconds, 5))  # 限制最长延迟5秒
            return {"type": "delay", "seconds": delay_seconds, "status": "executed"}
        
        else:
            raise Exception(f"未知的步骤类型: {step_type}")
    
    def get_execution_history(self, workflow_id: str = None, limit: int = 10) -> List[Dict]:
        """获取执行历史"""
        if workflow_id:
            results = [e for e in self.execution_history if e["workflow_id"] == workflow_id]
        else:
            results = self.execution_history
        
        return sorted(results, key=lambda x: x["start_time"], reverse=True)[:limit]


# ==================== 模板库 ====================

WORKFLOW_TEMPLATES = {
    "daily_report": {
        "name": "每日报告生成",
        "description": "自动生成每日工作报告",
        "steps": [
            {"name": "收集信息", "type": "agent", "config": {"request": "总结今天完成的工作"}},
            {"name": "组织内容", "type": "tool", "config": {"tool": "organize_text", "args": {}}},
            {"name": "生成报告", "type": "agent", "config": {"request": "生成最终报告"}}
        ],
        "tags": ["report", "daily"],
        "is_template": True
    },
    "project_plan": {
        "name": "项目计划制定",
        "description": "为新项目制定详细计划",
        "steps": [
            {"name": "分析需求", "type": "agent", "config": {"request": "分析项目需求"}},
            {"name": "制定任务", "type": "agent", "config": {"request": "制定任务清单"}},
            {"name": "分配资源", "type": "tool", "config": {"tool": "allocate_resources"}},
            {"name": "制定时间表", "type": "agent", "config": {"request": "制定项目时间表"}}
        ],
        "tags": ["planning", "project"],
        "is_template": True
    },
    "research": {
        "name": "研究流程",
        "description": "进行深度话题研究",
        "steps": [
            {"name": "搜索信息", "type": "tool", "config": {"tool": "web_search"}},
            {"name": "知识库查询", "type": "tool", "config": {"tool": "kb_search"}},
            {"name": "分析信息", "type": "agent", "config": {"request": "分析信息"}},
            {"name": "生成报告", "type": "agent", "config": {"request": "生成研究报告"}}
        ],
        "tags": ["research", "analysis"],
        "is_template": True
    }
}


def create_template_workflows(workflow_manager: WorkflowManager):
    """创建模板工作流"""
    for template_id, template_data in WORKFLOW_TEMPLATES.items():
        wf = Workflow(
            template_data["name"],
            template_data["description"],
            template_data["steps"]
        )
        wf.tags = template_data.get("tags", [])
        wf.is_template = template_data.get("is_template", False)
        workflow_manager.save_workflow(wf)
