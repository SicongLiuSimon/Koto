"""
自动归纳调度器
每日定时执行微信文件归纳，备份验证，存入归纳库
"""
import os
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional


class AutoCatalogScheduler:
    """自动归纳调度器，负责定时执行文件夹归纳并验证备份"""

    def __init__(self, settings_file: str = None):
        """
        Args:
            settings_file: 用户设置JSON文件路径
        """
        if settings_file is None:
            import sys
            if getattr(sys, 'frozen', False):
                project_root = os.path.dirname(sys.executable)
            else:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.dirname(script_dir)
            settings_file = os.path.join(project_root, 'config', 'user_settings.json')

        self.settings_file = settings_file
        self.config = self._load_config()

        # 引用核心模块（延迟加载）
        self.folder_organizer = None
        self.task_scheduler = None
        self.catalog_task_id = None

    def _load_config(self) -> Dict:
        """加载用户设置"""
        if os.path.exists(self.settings_file):
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def _save_config(self):
        """保存用户设置"""
        with open(self.settings_file, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def is_auto_catalog_enabled(self) -> bool:
        """检查自动归纳是否启用"""
        return self.config.get('auto_catalog', {}).get('enabled', False)

    def get_catalog_schedule(self) -> str:
        """获取归纳时间（默认每天凌晨2点）"""
        return self.config.get('auto_catalog', {}).get('schedule_time', '02:00')

    def get_source_directories(self) -> list:
        """获取待归纳的源目录列表"""
        wechat_dir = self.config.get('storage', {}).get('wechat_files_dir')
        custom_dirs = self.config.get('auto_catalog', {}).get('source_directories', [])

        dirs = []
        if wechat_dir and os.path.exists(wechat_dir):
            dirs.append(wechat_dir)
        dirs.extend([d for d in custom_dirs if os.path.exists(d)])

        return dirs

    def get_backup_directory(self) -> str:
        """获取备份目录路径"""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        backup_dir = self.config.get('auto_catalog', {}).get('backup_dir')

        if backup_dir is None:
            backup_dir = os.path.join(project_root, 'workspace', '_organize', '_backups')

        os.makedirs(backup_dir, exist_ok=True)
        return backup_dir

    def enable_auto_catalog(self, schedule_time: str = '02:00', source_dirs: list = None):
        """
        启用自动归纳

        Args:
            schedule_time: 调度时间，格式 "HH:MM"
            source_dirs: 自定义源目录列表（可选）
        """
        if 'auto_catalog' not in self.config:
            self.config['auto_catalog'] = {}

        self.config['auto_catalog']['enabled'] = True
        self.config['auto_catalog']['schedule_time'] = schedule_time

        if source_dirs is not None:
            self.config['auto_catalog']['source_directories'] = source_dirs

        self._save_config()
        print(f"[自动归纳] 已启用，每日 {schedule_time} 执行")

        # 注册定时任务
        self._register_scheduled_task()

    def disable_auto_catalog(self):
        """禁用自动归纳"""
        if 'auto_catalog' not in self.config:
            self.config['auto_catalog'] = {}

        self.config['auto_catalog']['enabled'] = False
        self._save_config()
        print("[自动归纳] 已禁用")

        # 取消定时任务
        self._cancel_scheduled_task()

    def _register_scheduled_task(self):
        """向任务调度器注册定时任务"""
        try:
            from web.task_scheduler import get_task_scheduler
        except ImportError:
            from task_scheduler import get_task_scheduler

        self.task_scheduler = get_task_scheduler()

        # 如果已有任务，先取消
        if self.catalog_task_id is not None:
            self.task_scheduler.cancel_task(self.catalog_task_id)

        schedule_time = self.get_catalog_schedule()

        self.catalog_task_id = self.task_scheduler.schedule_task(
            name="自动文件归纳",
            action=self.execute_auto_catalog,
            schedule_type="daily",
            time_str=schedule_time
        )

        print(f"[自动归纳] 任务已注册，ID: {self.catalog_task_id}")

    def _cancel_scheduled_task(self):
        """取消定时任务"""
        if self.task_scheduler and self.catalog_task_id:
            self.task_scheduler.cancel_task(self.catalog_task_id)
            print(f"[自动归纳] 任务已取消，ID: {self.catalog_task_id}")
            self.catalog_task_id = None

    def execute_auto_catalog(self) -> Dict[str, Any]:
        """
        执行自动归纳（主函数）

        Returns:
            执行结果字典
        """
        print(f"\n[自动归纳] 开始执行 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        source_dirs = self.get_source_directories()

        if not source_dirs:
            return {
                "success": False,
                "error": "没有配置的源目录"
            }

        # 延迟加载 folder_catalog_organizer
        if self.folder_organizer is None:
            try:
                from web.folder_catalog_organizer import FolderCatalogOrganizer
                from web.file_analyzer import FileAnalyzer
                from web.file_organizer import FileOrganizer
            except ImportError:
                from folder_catalog_organizer import FolderCatalogOrganizer
                from file_analyzer import FileAnalyzer
                from file_organizer import FileOrganizer

            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(script_dir)
            organize_root = os.path.join(project_root, 'workspace', '_organize')

            analyzer = FileAnalyzer()
            organizer = FileOrganizer(organize_root)

            self.folder_organizer = FolderCatalogOrganizer(organize_root, analyzer, organizer)

        total_files = 0
        total_organized = 0
        total_backed_up = 0
        errors = []

        for source_dir in source_dirs:
            print(f"[自动归纳] 处理目录: {source_dir}")

            try:
                # 1. 执行归纳
                result = self.folder_organizer.organize_folder(source_dir, recursive=True)

                if not result.get('success'):
                    errors.append(f"{source_dir}: {result.get('error')}")
                    continue

                files_count = result.get('total_files', 0)
                organized_count = result.get('organized_count', 0)

                total_files += files_count
                total_organized += organized_count

                print(f"[自动归纳] 归纳完成: {organized_count}/{files_count} 个文件")

                # 2. 备份验证
                entries = result.get('entries', [])
                backup_result = self._verify_and_backup(entries, source_dir)

                total_backed_up += backup_result.get('backed_up_count', 0)

                if not backup_result.get('success'):
                    errors.append(f"{source_dir} 备份失败: {backup_result.get('error')}")

            except Exception as e:
                error_msg = f"处理 {source_dir} 时出错: {str(e)}"
                print(f"[自动归纳] {error_msg}")
                errors.append(error_msg)

        # 3. ★ 整合去重清理（合并相似文件夹，删除重复文件）
        cleanup_result = self._run_cleanup(organize_root if 'organize_root' in dir() else None)

        # 4. 生成完成报告
        report = self._generate_completion_report(total_files, total_organized, total_backed_up, errors)

        print(f"[自动归纳] 执行完成 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  总文件数: {total_files}")
        print(f"  已归纳: {total_organized}")
        print(f"  已备份: {total_backed_up}")
        if cleanup_result:
            print(f"  去重合并: {cleanup_result.get('deduped_files', 0)} 文件, {cleanup_result.get('removed_folders', 0)} 文件夹")

        return {
            "success": len(errors) == 0,
            "total_files": total_files,
            "organized_count": total_organized,
            "backed_up_count": total_backed_up,
            "cleanup": cleanup_result,
            "errors": errors,
            "report_path": report
        }

    def _verify_and_backup(self, entries: list, source_dir: str) -> Dict[str, Any]:
        """
        验证文件归纳结果并创建备份清单

        Args:
            entries: 归纳条目列表（包含 source_path, organized_path）
            source_dir: 源目录路径

        Returns:
            {
                "success": bool,
                "backed_up_count": int,
                "backup_manifest_path": str,
                "error": str (optional)
            }
        """
        backup_dir = self.get_backup_directory()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        source_name = Path(source_dir).name

        backup_manifest = {
            "timestamp": timestamp,
            "source_dir": source_dir,
            "backup_time": datetime.now().isoformat(),
            "files": []
        }

        backed_up_count = 0

        for entry in entries:
            source_path = entry.get('source_path')
            organized_path = entry.get('organized_path')

            if not source_path or not organized_path:
                continue

            # 检查源文件是否仍存在
            source_exists = os.path.exists(source_path)

            # 检查归纳后文件是否存在
            organized_exists = os.path.exists(organized_path)

            if not organized_exists:
                print(f"[备份] 警告: 归纳文件不存在 {organized_path}")
                continue

            # 文件已成功归纳，记录备份信息
            backup_manifest['files'].append({
                "original_path": source_path,
                "organized_path": organized_path,
                "source_exists": source_exists,
                "organized_exists": organized_exists,
                "file_size": os.path.getsize(organized_path),
                "organized_at": datetime.now().isoformat()
            })

            backed_up_count += 1

        # 保存备份清单
        manifest_filename = f"backup_manifest_{source_name}_{timestamp}.json"
        manifest_path = os.path.join(backup_dir, manifest_filename)

        try:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(backup_manifest, f, ensure_ascii=False, indent=2)

            print(f"[备份] 清单已保存: {manifest_path}")

            return {
                "success": True,
                "backed_up_count": backed_up_count,
                "backup_manifest_path": manifest_path
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"备份清单保存失败: {str(e)}"
            }

    def _generate_completion_report(self, total_files: int, organized_count: int, backed_up_count: int, errors: list) -> str:
        """
        生成自动归纳完成报告

        Returns:
            报告文件路径
        """
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        reports_dir = os.path.join(project_root, 'workspace', '_organize', '_reports')
        os.makedirs(reports_dir, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_filename = f"auto_catalog_report_{timestamp}.md"
        report_path = os.path.join(reports_dir, report_filename)

        report_content = f"""# 自动归纳报告

**执行时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 统计

- 总文件数: {total_files}
- 已归纳: {organized_count}
- 已备份: {backed_up_count}
- 成功率: {organized_count / total_files * 100 if total_files > 0 else 0:.1f}%

## 源目录

{chr(10).join(f'- {d}' for d in self.get_source_directories())}

## 备份目录

{self.get_backup_directory()}

"""

        if errors:
            report_content += f"""## 错误信息

{chr(10).join(f'- {e}' for e in errors)}
"""
        else:
            report_content += "## 执行状态\n\n✅ 全部成功，无错误\n"

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_content)

        print(f"[报告] 已生成: {report_path}")
        return report_path

    def manual_catalog_now(self) -> Dict[str, Any]:
        """手动立即执行一次归纳（不依赖调度）"""
        return self.execute_auto_catalog()

    def _run_cleanup(self, organize_root: str = None) -> Optional[Dict]:
        """执行整合去重清理。"""
        try:
            if not organize_root:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                project_root = os.path.dirname(script_dir)
                organize_root = os.path.join(project_root, 'workspace', '_organize')

            try:
                from web.organize_cleanup import OrganizeCleanup
            except ImportError:
                from organize_cleanup import OrganizeCleanup

            cleanup = OrganizeCleanup(organize_root=organize_root)
            report = cleanup.run(dry_run=False, ai_rename=False)
            print(f"[自动归纳] 整合清理完成: 去重 {report.get('deduped_files', 0)} 文件, 合并 {report.get('removed_folders', 0)} 文件夹")
            return {
                "deduped_files": report.get("deduped_files", 0),
                "removed_folders": report.get("removed_folders", 0),
                "merged_files": report.get("merged_files", 0),
            }
        except Exception as e:
            print(f"[自动归纳] 整合清理失败: {e}")
            return None


# 全局单例
_auto_catalog_scheduler = None


def get_auto_catalog_scheduler() -> AutoCatalogScheduler:
    """获取自动归纳调度器单例"""
    global _auto_catalog_scheduler
    if _auto_catalog_scheduler is None:
        _auto_catalog_scheduler = AutoCatalogScheduler()
    return _auto_catalog_scheduler
