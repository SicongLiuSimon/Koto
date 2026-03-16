#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
洞察报告生成器 - 周期性用户活动分析报告
生成美观的周报、月报，展示用户工作模式和生产力洞察
"""

import sqlite3
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter, defaultdict

from behavior_monitor import BehaviorMonitor
from knowledge_graph import KnowledgeGraph
from suggestion_engine import SuggestionEngine
import logging


logger = logging.getLogger(__name__)

class InsightReporter:
    """洞察报告生成器 - 生成周期性分析报告"""
    
    # 报告类型
    REPORT_DAILY = "daily"
    REPORT_WEEKLY = "weekly"
    REPORT_MONTHLY = "monthly"
    
    def __init__(self, behavior_monitor: BehaviorMonitor = None,
                 knowledge_graph: KnowledgeGraph = None,
                 suggestion_engine: SuggestionEngine = None,
                 db_path: str = "config/insights.db"):
        """
        初始化报告生成器
        
        Args:
            behavior_monitor: 行为监控器
            knowledge_graph: 知识图谱
            suggestion_engine: 建议引擎
            db_path: 数据库路径
        """
        self.behavior_monitor = behavior_monitor or BehaviorMonitor()
        self.knowledge_graph = knowledge_graph or KnowledgeGraph()
        self.suggestion_engine = suggestion_engine or SuggestionEngine()
        self.db_path = db_path
        self._ensure_db()
    
    def _ensure_db(self):
        """确保数据库和表结构存在"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 报告表 - 存储生成的报告
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_type TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                report_data TEXT NOT NULL,  -- JSON格式的完整报告数据
                summary TEXT,  -- Markdown格式的摘要
                created_at TEXT NOT NULL
            )
        """)
        
        # 趋势数据表 - 存储时间序列数据用于趋势分析
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trend_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                recorded_at TEXT NOT NULL
            )
        """)
        
        # 创建索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reports_type ON reports(report_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reports_period ON reports(period_start, period_end)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trend_metric ON trend_data(metric_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trend_time ON trend_data(recorded_at DESC)")
        
        conn.commit()
        conn.close()
    
    def generate_weekly_report(self) -> Dict:
        """生成周报"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        return self._generate_report(
            self.REPORT_WEEKLY,
            start_date,
            end_date
        )
    
    def generate_monthly_report(self) -> Dict:
        """生成月报"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        
        return self._generate_report(
            self.REPORT_MONTHLY,
            start_date,
            end_date
        )
    
    def _generate_report(self, report_type: str, start_date: datetime, 
                        end_date: datetime) -> Dict:
        """
        生成报告
        
        Args:
            report_type: 报告类型
            start_date: 起始日期
            end_date: 结束日期
            
        Returns:
            报告字典
        """
        report = {
            "type": report_type,
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "days": (end_date - start_date).days
            },
            "generated_at": datetime.now().isoformat(),
            "sections": {}
        }
        
        # 1. 活动概览
        report["sections"]["activity_overview"] = self._get_activity_overview(start_date, end_date)
        
        # 2. 文件操作统计
        report["sections"]["file_operations"] = self._get_file_operations_stats(start_date, end_date)
        
        # 3. 生产力分析
        report["sections"]["productivity"] = self._get_productivity_analysis(start_date, end_date)
        
        # 4. 知识图谱洞察
        report["sections"]["knowledge_insights"] = self._get_knowledge_insights()
        
        # 5. 工作模式分析
        report["sections"]["work_patterns"] = self._get_work_patterns_analysis()
        
        # 6. 热门文件
        report["sections"]["top_files"] = self._get_top_files()
        
        # 7. 搜索分析
        report["sections"]["search_analysis"] = self._get_search_analysis()
        
        # 8. 建议总结
        report["sections"]["suggestions_summary"] = self._get_suggestions_summary()
        
        # 9. 趋势对比
        report["sections"]["trends"] = self._get_trends_comparison(report_type)
        
        # 10. 生成Markdown摘要
        report["summary_markdown"] = self._generate_markdown_summary(report)
        
        # 保存报告
        self._save_report(report)
        
        # 记录趋势数据
        self._record_trend_data(report)
        
        return report
    
    def _get_activity_overview(self, start_date: datetime, end_date: datetime) -> Dict:
        """获取活动概览"""
        # 获取每日活动
        daily_activity = self.behavior_monitor.get_daily_activity(days=(end_date - start_date).days)
        
        total_events = sum(day["event_count"] for day in daily_activity)
        avg_daily_events = total_events / max(len(daily_activity), 1)
        
        # 最活跃的一天
        most_active_day = max(daily_activity, key=lambda x: x["event_count"]) if daily_activity else None
        
        return {
            "total_events": total_events,
            "daily_average": round(avg_daily_events, 1),
            "most_active_day": most_active_day,
            "active_days": len([d for d in daily_activity if d["event_count"] > 0])
        }
    
    def _get_file_operations_stats(self, start_date: datetime, end_date: datetime) -> Dict:
        """获取文件操作统计"""
        stats = self.behavior_monitor.get_statistics()
        
        # 获取操作类型分布
        recent_events = self.behavior_monitor.get_recent_events(limit=1000)
        
        operation_counts = Counter()
        for event in recent_events:
            event_time = datetime.fromisoformat(event["timestamp"])
            if start_date <= event_time <= end_date:
                operation_counts[event["event_type"]] += 1
        
        return {
            "total_operations": sum(operation_counts.values()),
            "operations_by_type": dict(operation_counts),
            "most_common_operation": operation_counts.most_common(1)[0] if operation_counts else None
        }
    
    def _get_productivity_analysis(self, start_date: datetime, end_date: datetime) -> Dict:
        """生产力分析"""
        # 获取文件编辑次数
        frequent_files = self.behavior_monitor.get_frequently_used_files(limit=50)
        
        total_edits = sum(f.get("edit_count", 0) for f in frequent_files)
        total_opens = sum(f.get("open_count", 0) for f in frequent_files)
        
        # 计算生产力评分（编辑vs打开比例）
        productivity_score = (total_edits / max(total_opens, 1)) * 100
        
        return {
            "total_files_edited": len([f for f in frequent_files if f.get("edit_count", 0) > 0]),
            "total_edits": total_edits,
            "total_file_opens": total_opens,
            "productivity_score": round(productivity_score, 1),
            "interpretation": self._interpret_productivity_score(productivity_score)
        }
    
    def _interpret_productivity_score(self, score: float) -> str:
        """解释生产力评分"""
        if score >= 50:
            return "高效 - 你专注于创造内容"
        elif score >= 30:
            return "良好 - 保持了不错的编辑习惯"
        elif score >= 15:
            return "中等 - 更多时间在浏览文件"
        else:
            return "较低 - 可能在寻找资料或规划中"
    
    def _get_knowledge_insights(self) -> Dict:
        """知识图谱洞察"""
        try:
            kg_stats = self.knowledge_graph.get_statistics()
            
            # 获取热门概念
            top_concepts = self.knowledge_graph.concept_extractor.get_top_concepts(limit=10)
            
            return {
                "total_concepts": kg_stats.get("total_concepts", 0),
                "total_file_connections": kg_stats.get("file_relation_edges", 0),
                "average_connections_per_file": kg_stats.get("average_degree", 0),
                "graph_density": kg_stats.get("graph_density", 0),
                "top_concepts": [c["concept"] for c in top_concepts[:5]],
                "insight": self._interpret_graph_density(kg_stats.get("graph_density", 0))
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _interpret_graph_density(self, density: float) -> str:
        """解释图密度"""
        if density >= 0.3:
            return "文件关联度很高，知识体系连贯"
        elif density >= 0.1:
            return "文件有一定关联，形成了知识网络"
        elif density >= 0.05:
            return "文件关联度中等，可以加强整理"
        else:
            return "文件较为分散，建议建立更多关联"
    
    def _get_work_patterns_analysis(self) -> Dict:
        """工作模式分析"""
        patterns = self.behavior_monitor.get_work_patterns()
        
        # 找出最活跃的时间段
        time_patterns = patterns.get("time_of_day", [])
        most_active_period = time_patterns[0] if time_patterns else None
        
        # 找出最常用的操作
        operation_patterns = patterns.get("operation_types", [])
        top_operations = operation_patterns[:3]
        
        return {
            "most_active_period": most_active_period,
            "top_operations": top_operations,
            "work_style": self._determine_work_style(patterns)
        }
    
    def _determine_work_style(self, patterns: Dict) -> str:
        """判断工作风格"""
        operations = patterns.get("operation_types", [])
        if not operations:
            return "探索者 - 正在熟悉系统"
        
        op_dict = {op["operation"]: op["frequency"] for op in operations}
        
        edit_count = op_dict.get(BehaviorMonitor.EVENT_FILE_EDIT, 0)
        search_count = op_dict.get(BehaviorMonitor.EVENT_FILE_SEARCH, 0)
        organize_count = op_dict.get(BehaviorMonitor.EVENT_FILE_ORGANIZE, 0)
        
        if edit_count > search_count and edit_count > organize_count:
            return "创作者 - 专注于内容创作"
        elif search_count > edit_count:
            return "研究者 - 擅长查找和整理信息"
        elif organize_count > edit_count * 0.5:
            return "管理者 - 注重文件组织和管理"
        else:
            return "平衡者 - 在创作和管理间保持平衡"
    
    def _get_top_files(self) -> List[Dict]:
        """获取热门文件"""
        files = self.behavior_monitor.get_frequently_used_files(limit=10)
        
        return [
            {
                "path": f["file_path"],
                "name": Path(f["file_path"]).name,
                "opens": f["open_count"],
                "edits": f["edit_count"],
                "last_used": f.get("last_opened", f.get("last_edited"))
            }
            for f in files
        ]
    
    def _get_search_analysis(self) -> Dict:
        """搜索分析"""
        search_history = self.behavior_monitor.get_search_history(limit=100)
        
        if not search_history:
            return {"total_searches": 0}
        
        # 统计搜索频率
        query_counts = Counter(s["query"] for s in search_history)
        
        # 点击率分析
        total_searches = len(search_history)
        searches_with_click = len([s for s in search_history if s.get("clicked_result")])
        click_through_rate = (searches_with_click / total_searches) * 100
        
        return {
            "total_searches": total_searches,
            "unique_queries": len(query_counts),
            "most_searched": query_counts.most_common(5),
            "click_through_rate": round(click_through_rate, 1),
            "search_effectiveness": self._interpret_ctr(click_through_rate)
        }
    
    def _interpret_ctr(self, ctr: float) -> str:
        """解释点击率"""
        if ctr >= 70:
            return "优秀 - 搜索很准确"
        elif ctr >= 50:
            return "良好 - 通常能找到需要的"
        elif ctr >= 30:
            return "中等 - 有改进空间"
        else:
            return "较低 - 可能需要优化搜索策略"
    
    def _get_suggestions_summary(self) -> Dict:
        """建议总结"""
        stats = self.suggestion_engine.get_statistics()
        
        pending_suggestions = self.suggestion_engine.get_pending_suggestions(limit=5)
        
        return {
            "total_suggestions": stats.get("total_suggestions", 0),
            "applied": stats.get("applied_suggestions", 0),
            "dismissed": stats.get("dismissed_suggestions", 0),
            "pending": stats.get("pending_suggestions", 0),
            "acceptance_rate": stats.get("acceptance_rate", 0),
            "top_pending": [
                {"title": s["title"], "priority": s["priority"]}
                for s in pending_suggestions[:3]
            ]
        }
    
    def _get_trends_comparison(self, report_type: str) -> Dict:
        """趋势对比"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取历史报告进行对比
        cursor.execute("""
            SELECT report_data, created_at
            FROM reports
            WHERE report_type = ?
            ORDER BY created_at DESC
            LIMIT 2
        """, (report_type,))
        
        results = cursor.fetchall()
        conn.close()
        
        if len(results) < 2:
            return {"trend_available": False}
        
        try:
            current_report = json.loads(results[0][0])
            previous_report = json.loads(results[1][0])
            
            # 对比关键指标
            current_events = current_report["sections"]["activity_overview"]["total_events"]
            previous_events = previous_report["sections"]["activity_overview"]["total_events"]
            
            change_percent = ((current_events - previous_events) / max(previous_events, 1)) * 100
            
            return {
                "trend_available": True,
                "activity_change": round(change_percent, 1),
                "trend_direction": "up" if change_percent > 0 else "down" if change_percent < 0 else "stable",
                "interpretation": self._interpret_trend(change_percent)
            }
        except Exception as e:
            return {"trend_available": False, "error": str(e)}
    
    def _interpret_trend(self, change: float) -> str:
        """解释趋势"""
        if change > 20:
            return "活跃度大幅提升 📈"
        elif change > 5:
            return "活跃度稳步增长 📊"
        elif change > -5:
            return "活跃度保持稳定 ➡️"
        elif change > -20:
            return "活跃度有所下降 📉"
        else:
            return "活跃度显著下降 ⚠️"
    
    def _generate_markdown_summary(self, report: Dict) -> str:
        """生成Markdown格式的摘要"""
        sections = report["sections"]
        period = report["period"]
        
        # 计算日期范围
        start = datetime.fromisoformat(period["start"])
        end = datetime.fromisoformat(period["end"])
        
        md = f"""# 📊 Koto 工作报告

**报告周期**: {start.strftime('%Y-%m-%d')} 至 {end.strftime('%Y-%m-%d')} ({period['days']}天)
**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}

---

## 🎯 活动概览

- **总操作数**: {sections['activity_overview']['total_events']} 次
- **日均活跃**: {sections['activity_overview']['daily_average']} 次
- **活跃天数**: {sections['activity_overview']['active_days']}/{period['days']} 天

"""
        
        # 生产力分析
        productivity = sections.get("productivity", {})
        md += f"""## 📈 生产力分析

- **编辑文件数**: {productivity.get('total_files_edited', 0)} 个
- **总编辑次数**: {productivity.get('total_edits', 0)} 次
- **生产力评分**: {productivity.get('productivity_score', 0)}% - {productivity.get('interpretation', '暂无数据')}

"""
        
        # 热门文件
        top_files = sections.get("top_files", [])[:5]
        if top_files:
            md += "## 🔥 最常用文件\n\n"
            for i, file in enumerate(top_files, 1):
                md += f"{i}. **{file['name']}** - 打开{file['opens']}次，编辑{file['edits']}次\n"
            md += "\n"
        
        # 知识洞察
        knowledge = sections.get("knowledge_insights", {})
        if not knowledge.get("error"):
            md += f"""## 🧠 知识图谱洞察

- **概念总数**: {knowledge.get('total_concepts', 0)} 个
- **文件关联**: {knowledge.get('total_file_connections', 0)} 个
- **图谱评价**: {knowledge.get('insight', '暂无评价')}

"""
            
            if knowledge.get("top_concepts"):
                md += "**热门概念**: " + ", ".join(knowledge["top_concepts"]) + "\n\n"
        
        # 工作模式
        patterns = sections.get("work_patterns", {})
        if patterns.get("most_active_period"):
            period_name = patterns["most_active_period"]["period"]
            period_freq = patterns["most_active_period"]["frequency"]
            md += f"""## ⏰ 工作模式

- **最活跃时段**: {period_name} ({period_freq}次操作)
- **工作风格**: {patterns.get('work_style', '未知')}

"""
        
        # 搜索分析
        search = sections.get("search_analysis", {})
        if search.get("total_searches", 0) > 0:
            md += f"""## 🔍 搜索分析

- **搜索次数**: {search['total_searches']} 次
- **独特查询**: {search['unique_queries']} 个
- **点击率**: {search['click_through_rate']}% - {search['search_effectiveness']}

"""
        
        # 智能建议
        suggestions = sections.get("suggestions_summary", {})
        if suggestions.get("pending", 0) > 0:
            md += f"""## 💡 智能建议

- **待处理建议**: {suggestions['pending']} 条
- **采纳率**: {suggestions['acceptance_rate']}%

"""
            if suggestions.get("top_pending"):
                md += "**推荐建议**:\n"
                for sug in suggestions["top_pending"]:
                    priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(sug["priority"], "⚪")
                    md += f"- {priority_emoji} {sug['title']}\n"
                md += "\n"
        
        # 趋势
        trends = sections.get("trends", {})
        if trends.get("trend_available"):
            md += f"""## 📊 趋势对比

- **活跃度变化**: {trends['activity_change']:+.1f}%
- **趋势**: {trends['interpretation']}

"""
        
        md += """---

*由 Koto 智能文件大脑自动生成*
"""
        
        return md
    
    def _save_report(self, report: Dict):
        """保存报告到数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO reports (report_type, period_start, period_end, report_data, summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            report["type"],
            report["period"]["start"],
            report["period"]["end"],
            json.dumps(report),
            report.get("summary_markdown", ""),
            datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()
    
    def _record_trend_data(self, report: Dict):
        """记录趋势数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        timestamp = datetime.now().isoformat()
        sections = report["sections"]
        
        # 记录关键指标
        metrics = {
            "total_events": sections["activity_overview"]["total_events"],
            "daily_average": sections["activity_overview"]["daily_average"],
            "productivity_score": sections["productivity"]["productivity_score"],
            "total_edits": sections["productivity"]["total_edits"]
        }
        
        for metric_name, metric_value in metrics.items():
            cursor.execute("""
                INSERT INTO trend_data (metric_name, metric_value, recorded_at)
                VALUES (?, ?, ?)
            """, (metric_name, metric_value, timestamp))
        
        conn.commit()
        conn.close()
    
    def get_latest_report(self, report_type: str = REPORT_WEEKLY) -> Optional[Dict]:
        """获取最新报告"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT report_data, summary, created_at
            FROM reports
            WHERE report_type = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (report_type,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            report_data = json.loads(result[0])
            report_data["summary_markdown"] = result[1]
            return report_data
        
        return None
    
    def export_report_markdown(self, report: Dict, output_path: str):
        """导出报告为Markdown文件"""
        md_content = report.get("summary_markdown", "")
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        return output_path


if __name__ == "__main__":
    # 测试代码
    reporter = InsightReporter()
    
    logger.info("📊 洞察报告生成器测试")
    logger.info("=" * 50)
    
    # 生成周报
    logger.info("\n生成周报...")
    report = reporter.generate_weekly_report()
    
    logger.info(f"\n报告类型: {report['type']}")
    logger.info(f"报告周期: {report['period']['days']}天")
    logger.info(f"\n生成的Markdown摘要:\n")
    logger.info(report.get("summary_markdown", "暂无摘要")[:500] + "...\n")
    
    logger.info("=" * 50)
    logger.info("✅ 洞察报告生成器已就绪")
