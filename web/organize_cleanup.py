"""
_organize 目录整合清理工具

功能：
1. 扫描所有文件夹，识别重复/相似文件夹
2. 合并重复文件夹（保留内容最多/名称最完整的文件夹）
3. 文件内容hash去重（相同内容只保留一份）
4. 清理空文件夹
5. 重建 index.json
6. （可选）使用 AI 模型智能命名文件夹

使用方式:
    python -m web.organize_cleanup [--dry-run] [--ai-rename]
"""

import hashlib
import json
import logging
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class OrganizeCleanup:
    """智能整合清理 _organize 目录"""

    # 文件名修订后缀模式（与 FileAnalyzer._REVISION_PATTERNS 保持一致）
    _REVISION_PATTERNS = [
        re.compile(r"_revised\(\d+\)$", re.IGNORECASE),
        re.compile(r"_revised_\d{8,14}$", re.IGNORECASE),
        re.compile(r"_revised$", re.IGNORECASE),
        re.compile(r"\(\d+\)$"),
        re.compile(r"_\d{8,14}$"),
        re.compile(r"_copy\d*$", re.IGNORECASE),
        re.compile(r"_副本\d*$"),
        re.compile(r"_\d+$"),
    ]

    def __init__(self, organize_root: str = "workspace/_organize"):
        self.organize_root = Path(organize_root)
        self.index_file = self.organize_root / "index.json"
        self.log: List[str] = []

    def run(self, dry_run: bool = True, ai_rename: bool = False) -> Dict:
        """执行整合清理流程。

        Args:
            dry_run: 如果 True，只输出计划不实际操作
            ai_rename: 如果 True，使用 AI 模型重命名文件夹

        Returns:
            清理报告 dict
        """
        self._log(
            f"===== _organize 整合清理 {'(预演)' if dry_run else '(实际执行)'} ====="
        )
        self._log(f"根目录: {self.organize_root}")

        # 1. 扫描所有文件夹及其内容
        folder_info = self._scan_folders()
        self._log(f"\n找到 {len(folder_info)} 个文件夹")

        # 2. 构建相似度分组
        groups = self._build_similarity_groups(folder_info)
        self._log(f"\n找到 {len(groups)} 组相似文件夹")

        # 3. 决定每组保留哪个文件夹
        merge_plan = self._create_merge_plan(groups, folder_info)

        # 4. 执行合并
        merged_count = 0
        deduped_files = 0
        removed_folders = 0

        for plan in merge_plan:
            target = plan["target"]
            sources = plan["sources"]
            self._log(f"\n★ 合并组: 目标 → {target}")
            for src in sources:
                self._log(f"  ← 合并来源: {src}")

            if not dry_run:
                result = self._execute_merge(target, sources, folder_info)
                merged_count += result["merged_files"]
                deduped_files += result["deduped_files"]
                removed_folders += result["removed_folders"]
            else:
                # 预演统计
                for src in sources:
                    info = folder_info.get(src, {})
                    merged_count += len(info.get("files", []))
                    removed_folders += 1

        # 5. 文件夹内部去重（清理同一文件夹内的重复文件）
        if not dry_run:
            intra_dedup = self._deduplicate_within_folders()
            deduped_files += intra_dedup

        # 6. 清理空文件夹
        empty_cleaned = 0
        if not dry_run:
            empty_cleaned = self._cleanup_empty_folders()

        # 7. AI 重命名
        ai_renames = 0
        if ai_rename and not dry_run:
            ai_renames = self._ai_rename_folders()

        # 8. 重建索引
        if not dry_run:
            self._rebuild_index()
            self._log("\n索引已重建")

        report = {
            "dry_run": dry_run,
            "total_folders_scanned": len(folder_info),
            "similarity_groups": len(groups),
            "merge_plans": len(merge_plan),
            "merged_files": merged_count,
            "deduped_files": deduped_files,
            "removed_folders": removed_folders,
            "empty_cleaned": empty_cleaned,
            "ai_renames": ai_renames,
            "log": self.log,
        }

        # 保存报告
        report_path = self.organize_root / "_cleanup_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        self._log(f"\n报告已保存: {report_path}")

        return report

    # ──────────────────────────────────────────────
    # 1. 扫描
    # ──────────────────────────────────────────────
    def _scan_folders(self) -> Dict[str, Dict]:
        """扫描 _organize 下所有文件夹，记录每个文件夹的文件列表和 hash。"""
        folder_info = {}

        for root, dirs, files in os.walk(self.organize_root):
            root_path = Path(root)
            if root_path == self.organize_root:
                continue

            # 跳过特殊目录
            try:
                rel = str(root_path.relative_to(self.organize_root)).replace("\\", "/")
            except ValueError:
                continue
            if rel.startswith("_"):
                continue

            real_files = [f for f in files if not f.startswith("_")]
            if not real_files:
                folder_info[rel] = {"files": [], "hashes": {}, "total_size": 0}
                continue

            hashes = {}
            total_size = 0
            for f in real_files:
                fp = root_path / f
                h = self._file_hash(fp)
                hashes[f] = h
                try:
                    total_size += fp.stat().st_size
                except Exception:
                    pass

            folder_info[rel] = {
                "files": real_files,
                "hashes": hashes,
                "total_size": total_size,
            }

        return folder_info

    # ──────────────────────────────────────────────
    # 2. 相似度分组
    # ──────────────────────────────────────────────
    def _build_similarity_groups(self, folder_info: Dict[str, Dict]) -> List[Set[str]]:
        """将相似的文件夹归为同一组。"""
        folders = list(folder_info.keys())
        visited: Set[str] = set()
        groups: List[Set[str]] = []

        for i, a in enumerate(folders):
            if a in visited:
                continue
            group = {a}
            a_clean = self._clean_folder_name(a)

            for j in range(i + 1, len(folders)):
                b = folders[j]
                if b in visited:
                    continue
                b_clean = self._clean_folder_name(b)

                if self._are_similar(a_clean, b_clean):
                    group.add(b)

            if len(group) > 1:
                groups.append(group)
                visited.update(group)

        return groups

    def _clean_folder_name(self, folder_path: str) -> str:
        """清理文件夹名称以用于相似比较（去掉路径前缀和修订后缀）。"""
        # 取最后一段
        leaf = folder_path.replace("\\", "/").split("/")[-1]
        # 去修订后缀
        for pat in self._REVISION_PATTERNS:
            leaf = pat.sub("", leaf)
        return leaf.strip().lower()

    def _are_similar(self, a: str, b: str) -> bool:
        """判断两个清理后的名称是否相似。"""
        if not a or not b:
            return False
        # 完全匹配
        if a == b:
            return True
        # 前缀匹配
        if a.startswith(b) or b.startswith(a):
            shorter = min(len(a), len(b))
            longer = max(len(a), len(b))
            if shorter / longer > 0.3:
                return True
        # 模糊匹配
        ratio = SequenceMatcher(None, a, b).ratio()
        return ratio > 0.65

    # ──────────────────────────────────────────────
    # 3. 合并计划
    # ──────────────────────────────────────────────
    def _create_merge_plan(
        self, groups: List[Set[str]], folder_info: Dict[str, Dict]
    ) -> List[Dict]:
        """为每个相似组选择最佳保留目标。"""
        plans = []
        for group in groups:
            members = sorted(group)
            # 选择最佳文件夹：优先最多文件 → 最长名称（通常更完整）
            best = max(
                members,
                key=lambda f: (
                    len(folder_info.get(f, {}).get("files", [])),
                    len(f),
                ),
            )
            sources = [f for f in members if f != best]
            plans.append(
                {
                    "target": best,
                    "sources": sources,
                }
            )
        return plans

    # ──────────────────────────────────────────────
    # 4. 执行合并
    # ──────────────────────────────────────────────
    def _execute_merge(
        self, target: str, sources: List[str], folder_info: Dict[str, Dict]
    ) -> Dict:
        """将 sources 中的文件移到 target 文件夹，跳过内容重复的文件。"""
        target_dir = self.organize_root / target
        target_dir.mkdir(parents=True, exist_ok=True)

        # 收集 target 现有文件的 hash
        target_hashes: Set[str] = set()
        for f in target_dir.iterdir():
            if f.is_file() and not f.name.startswith("_"):
                target_hashes.add(self._file_hash(f))

        merged_files = 0
        deduped_files = 0
        removed_folders = 0

        for src in sources:
            src_dir = self.organize_root / src
            if not src_dir.exists():
                continue

            for f in list(src_dir.iterdir()):
                if not f.is_file() or f.name.startswith("_"):
                    continue

                fhash = self._file_hash(f)
                if fhash in target_hashes:
                    # 内容重复，直接删除源文件
                    self._log(f"  去重删除: {src}/{f.name} (hash 已在目标)")
                    f.unlink()
                    deduped_files += 1
                else:
                    # 移动到目标
                    dest = target_dir / f.name
                    if dest.exists():
                        dest = self._unique_dest(dest)
                    shutil.move(str(f), str(dest))
                    target_hashes.add(fhash)
                    merged_files += 1
                    self._log(f"  移动: {src}/{f.name} → {target}/{dest.name}")

            # 尝试删除空文件夹（含 _metadata.json）
            self._try_remove_folder(src_dir)
            removed_folders += 1

        return {
            "merged_files": merged_files,
            "deduped_files": deduped_files,
            "removed_folders": removed_folders,
        }

    # ──────────────────────────────────────────────
    # 5. 文件夹内部去重
    # ──────────────────────────────────────────────
    def _deduplicate_within_folders(self) -> int:
        """清理每个文件夹内部的重复文件（保留名称最短的版本）。"""
        deduped = 0
        for root, dirs, files in os.walk(self.organize_root):
            root_path = Path(root)
            real_files = [root_path / f for f in files if not f.startswith("_")]
            if len(real_files) <= 1:
                continue

            # 按 hash 分组
            hash_groups: Dict[str, List[Path]] = defaultdict(list)
            for fp in real_files:
                h = self._file_hash(fp)
                if h:
                    hash_groups[h].append(fp)

            for h, group in hash_groups.items():
                if len(group) <= 1:
                    continue
                # 保留名称最短的（通常是原始版本），删除修订版
                group.sort(key=lambda p: len(p.stem))
                keeper = group[0]
                for dup in group[1:]:
                    self._log(f"  内部去重: 删除 {dup.name} (保留 {keeper.name})")
                    dup.unlink()
                    deduped += 1

        return deduped

    # ──────────────────────────────────────────────
    # 6. 清理空文件夹
    # ──────────────────────────────────────────────
    def _cleanup_empty_folders(self) -> int:
        """递归删除空文件夹（只含 _metadata.json 的也算空）。"""
        cleaned = 0
        # 从最深层开始
        for root, dirs, files in os.walk(self.organize_root, topdown=False):
            root_path = Path(root)
            if root_path == self.organize_root:
                continue
            try:
                rel = str(root_path.relative_to(self.organize_root))
            except ValueError:
                continue
            if rel.startswith("_"):
                continue

            real_files = [f for f in files if not f.startswith("_")]
            sub_dirs = [d for d in dirs if not d.startswith("_")]
            if not real_files and not sub_dirs:
                self._try_remove_folder(root_path)
                cleaned += 1

        return cleaned

    # ──────────────────────────────────────────────
    # 7. AI 重命名（可选）
    # ──────────────────────────────────────────────
    def _ai_rename_folders(self) -> int:
        """使用 Gemini AI 模型分析文件内容并重命名文件夹。"""
        renames = 0
        try:
            import google.genai as genai

            from app.core.config import get_settings

            settings = get_settings()
            api_key = settings.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
            if not api_key:
                self._log("AI 重命名: 未找到 API key，跳过")
                return 0

            client = genai.Client(api_key=api_key)
        except Exception as e:
            self._log(f"AI 重命名: 初始化失败 - {e}")
            return 0

        for root, dirs, files in os.walk(self.organize_root):
            root_path = Path(root)
            if root_path == self.organize_root:
                continue
            try:
                rel = str(root_path.relative_to(self.organize_root)).replace("\\", "/")
            except ValueError:
                continue
            if rel.startswith("_") or "/" in rel:
                # 只处理叶子目录（第二级）
                continue

            real_files = [f for f in files if not f.startswith("_")]
            if not real_files:
                continue

            # 构建文件名列表给 AI
            file_list = "\n".join(real_files[:20])  # 最多 20 个

            prompt = f"""你是文件管理助手。以下是一个文件夹中的文件列表：

{file_list}

请根据这些文件名分析该文件夹的主题，给出一个简洁、准确的中文文件夹名称（5-20字）。
只输出文件夹名称，不要解释。"""

            try:
                response = client.models.generate_content(
                    model="gemini-2.0-flash-lite",
                    contents=prompt,
                )
                suggested_name = response.text.strip().strip('"').strip("'")
                if suggested_name and 2 <= len(suggested_name) <= 30:
                    # 清理名称
                    safe_name = re.sub(r'[\\/:*?"<>|]', "_", suggested_name)
                    if safe_name != root_path.name:
                        new_path = root_path.parent / safe_name
                        if not new_path.exists():
                            root_path.rename(new_path)
                            self._log(f"  AI 重命名: {root_path.name} → {safe_name}")
                            renames += 1
            except Exception as e:
                self._log(f"  AI 重命名失败 ({root_path.name}): {e}")

        return renames

    # ──────────────────────────────────────────────
    # 8. 重建索引
    # ──────────────────────────────────────────────
    def _rebuild_index(self):
        """根据当前目录结构重建 index.json。"""
        entries = []
        for root, dirs, files in os.walk(self.organize_root):
            root_path = Path(root)
            if root_path == self.organize_root:
                continue

            try:
                rel = str(root_path.relative_to(self.organize_root)).replace("\\", "/")
            except ValueError:
                continue
            if rel.startswith("_"):
                continue

            for f in files:
                if f.startswith("_"):
                    continue
                fp = root_path / f
                try:
                    file_size = fp.stat().st_size
                except Exception:
                    file_size = 0

                entries.append(
                    {
                        "source_path": "",
                        "organized_path": str(fp),
                        "folder": rel,
                        "file_name": f,
                        "file_size": file_size,
                        "organized_at": datetime.now().isoformat(),
                    }
                )

        index = {
            "version": "1.0",
            "created_at": datetime.now().isoformat(),
            "total_files": len(entries),
            "last_updated": datetime.now().isoformat(),
            "files": entries,
        }

        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        self._log(f"索引重建完成: {len(entries)} 条记录")

    # ──────────────────────────────────────────────
    # 工具方法
    # ──────────────────────────────────────────────
    @staticmethod
    def _file_hash(file_path: Path, chunk_size: int = 8192) -> str:
        h = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    h.update(chunk)
        except Exception:
            return ""
        return h.hexdigest()

    @staticmethod
    def _unique_dest(path: Path) -> Path:
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 1
        while True:
            new_path = parent / f"{stem}_{counter}{suffix}"
            if not new_path.exists():
                return new_path
            counter += 1

    def _try_remove_folder(self, folder: Path):
        """尝试删除文件夹（先清理元数据文件）。"""
        try:
            for f in folder.iterdir():
                if f.is_file() and f.name.startswith("_"):
                    f.unlink()
            if not any(folder.iterdir()):
                folder.rmdir()
                self._log(f"  删除空文件夹: {folder.name}")
                # 也尝试删除空的父目录
                parent = folder.parent
                if parent != self.organize_root and parent.exists():
                    remaining = [
                        x for x in parent.iterdir() if not x.name.startswith("_")
                    ]
                    if not remaining:
                        self._try_remove_folder(parent)
        except Exception as e:
            self._log(f"  删除文件夹失败 ({folder}): {e}")

    def _log(self, msg: str):
        self.log.append(msg)
        logger.info(msg)


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    dry = "--dry-run" in sys.argv or "-n" in sys.argv
    ai = "--ai-rename" in sys.argv

    if not dry:
        logger.warning("⚠️  即将实际执行整合清理！")
        logger.info("   加 --dry-run 参数可以先预演")
        confirm = input("   确认执行? (y/N): ").strip().lower()
        if confirm != "y":
            logger.info("已取消")
            sys.exit(0)

    cleanup = OrganizeCleanup()
    report = cleanup.run(dry_run=dry, ai_rename=ai)

    logger.info(f"\n===== 清理完成 =====")
    logger.info(f"扫描文件夹: {report['total_folders_scanned']}")
    logger.info(f"相似组数: {report['similarity_groups']}")
    logger.info(f"合并计划: {report['merge_plans']}")
    logger.info(f"合并文件: {report['merged_files']}")
    logger.info(f"去重文件: {report['deduped_files']}")
    logger.info(f"删除文件夹: {report['removed_folders']}")
    logger.info(f"清理空文件夹: {report['empty_cleaned']}")
    if ai:
        logger.info(f"AI 重命名: {report['ai_renames']}")
