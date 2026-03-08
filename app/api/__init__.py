# 延迟导入蓝图 - 避免启动时加载重型依赖
def __getattr__(name):
    if name == "agent_bp":
        from .agent_routes import agent_bp
        return agent_bp
    if name == "task_bp":
        from .task_routes import task_bp
        return task_bp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
