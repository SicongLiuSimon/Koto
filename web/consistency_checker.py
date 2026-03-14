#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文档一致性检查器 - 术语统一、格式统一、引用规范检查
"""

import os
import re
from typing import Dict, List, Any, Tuple
from collections import Counter


class ConsistencyChecker:
    """文档一致性检查器"""
    
    def __init__(self):
        # 预定义术语词典（可扩展）
        self.term_dictionary = {
            "人工智能": ["AI", "ai", "A.I.", "artificial intelligence"],
            "机器学习": ["ML", "ml", "M.L.", "machine learning"],
            "深度学习": ["DL", "dl", "D.L.", "deep learning"],
            "自然语言处理": ["NLP", "nlp", "N.L.P.", "natural language processing"],
        }
        # Precompile variant patterns
        self._compiled_patterns = {}
        for standard_term, variants in self.term_dictionary.items():
            self._compiled_patterns[standard_term] = [
                (variant, re.compile(re.escape(variant), re.IGNORECASE))
                for variant in variants
            ]
    
    def check_document(self, file_path: str) -> Dict[str, Any]:
        """
        全面检查文档一致性
        
        Returns:
            {
                "term_issues": [...],
                "format_issues": [...],
                "reference_issues": [...],
                "statistics": {...}
            }
        """
        if not os.path.exists(file_path):
            return {"success": False, "error": "文件不存在"}
        
        # 读取文档内容
        text = self._read_file(file_path)
        if not text:
            return {"success": False, "error": "无法读取文件内容"}
        
        # 执行各项检查
        term_issues = self.check_terminology(text)
        format_issues = self.check_formatting(text)
        reference_issues = self.check_references(text)
        statistics = self.get_statistics(text)
        
        return {
            "success": True,
            "file_path": file_path,
            "term_issues": term_issues,
            "format_issues": format_issues,
            "reference_issues": reference_issues,
            "statistics": statistics,
            "total_issues": len(term_issues) + len(format_issues) + len(reference_issues)
        }
    
    def check_terminology(self, text: str) -> List[Dict[str, Any]]:
        """检查术语一致性"""
        issues = []
        
        for standard_term, compiled_variants in self._compiled_patterns.items():
            # 统计各种变体出现次数
            counts = {}
            for variant, pattern in compiled_variants:
                matches = pattern.findall(text)
                if matches:
                    counts[variant] = len(matches)
            
            # 如果同一概念有多种写法，报告不一致
            if len(counts) > 1:
                issues.append({
                    "type": "术语不统一",
                    "standard_term": standard_term,
                    "variants_found": counts,
                    "suggestion": f"建议统一使用 '{standard_term}'",
                    "severity": "warning"
                })
        
        # 检查常见拼写错误
        common_typos = {
            "teh": "the",
            "recieve": "receive",
            "occurence": "occurrence",
            "seperate": "separate"
        }
        
        for typo, correct in common_typos.items():
            if re.search(r'\b' + typo + r'\b', text, re.IGNORECASE):
                issues.append({
                    "type": "拼写错误",
                    "typo": typo,
                    "correct": correct,
                    "suggestion": f"'{typo}' 应为 '{correct}'",
                    "severity": "error"
                })
        
        return issues
    
    def check_formatting(self, text: str) -> List[Dict[str, Any]]:
        """检查格式一致性"""
        issues = []
        
        # 检查标题层级
        headings = re.findall(r'^(#{1,6})\s+(.+)$', text, re.MULTILINE)
        if headings:
            levels = [len(h[0]) for h in headings]
            
            # 检查是否跳级（如 # 直接跳到 ###）
            for i in range(len(levels) - 1):
                if levels[i+1] - levels[i] > 1:
                    issues.append({
                        "type": "标题跳级",
                        "line": i + 1,
                        "from_level": levels[i],
                        "to_level": levels[i+1],
                        "suggestion": "标题层级不应跳跃",
                        "severity": "warning"
                    })
        
        # 检查列表符号一致性
        bullet_types = []
        for match in re.finditer(r'^[\s]*([•\-\*])\s', text, re.MULTILINE):
            bullet_types.append(match.group(1))
        
        if bullet_types:
            counter = Counter(bullet_types)
            if len(counter) > 1:
                issues.append({
                    "type": "列表符号不统一",
                    "found_symbols": dict(counter),
                    "suggestion": f"建议统一使用 '{counter.most_common(1)[0][0]}'",
                    "severity": "info"
                })
        
        # 检查空行使用
        consecutive_blanks = re.findall(r'\n\n\n+', text)
        if consecutive_blanks:
            issues.append({
                "type": "多余空行",
                "count": len(consecutive_blanks),
                "max_consecutive": max(len(cb) - 1 for cb in consecutive_blanks),
                "suggestion": "段落间使用单个空行即可",
                "severity": "info"
            })
        
        # 检查中英文混排空格
        # 中文后接英文应有空格
        missing_space = re.findall(r'[\u4e00-\u9fff][a-zA-Z]', text)
        if missing_space:
            issues.append({
                "type": "中英文混排缺少空格",
                "count": len(missing_space),
                "examples": missing_space[:5],
                "suggestion": "中文与英文之间建议加空格",
                "severity": "info"
            })
        
        return issues
    
    def check_references(self, text: str) -> List[Dict[str, Any]]:
        """检查引用规范"""
        issues = []
        
        # 检查链接格式
        # Markdown 链接格式: [text](url)
        markdown_links = re.findall(r'\[([^\]]+)\]\(([^\)]+)\)', text)
        
        # 检查空链接文本
        for link_text, url in markdown_links:
            if not link_text.strip():
                issues.append({
                    "type": "空链接文本",
                    "url": url,
                    "suggestion": "链接应有描述性文本",
                    "severity": "warning"
                })
        
        # 检查图片引用: ![alt](path)
        image_refs = re.findall(r'!\[([^\]]*)\]\(([^\)]+)\)', text)
        for alt, path in image_refs:
            if not alt.strip():
                issues.append({
                    "type": "图片缺少alt文本",
                    "path": path,
                    "suggestion": "图片应有描述性alt文本",
                    "severity": "info"
                })
        
        # 检查脚注引用
        footnote_refs = re.findall(r'\[\^(\d+)\]', text)
        footnote_defs = re.findall(r'^\[\^(\d+)\]:', text, re.MULTILINE)
        
        # 检查未定义的脚注
        undefined = set(footnote_refs) - set(footnote_defs)
        if undefined:
            issues.append({
                "type": "未定义的脚注",
                "footnotes": list(undefined),
                "suggestion": "应定义所有引用的脚注",
                "severity": "error"
            })
        
        # 检查未使用的脚注定义
        unused = set(footnote_defs) - set(footnote_refs)
        if unused:
            issues.append({
                "type": "未使用的脚注定义",
                "footnotes": list(unused),
                "suggestion": "删除未使用的脚注定义",
                "severity": "info"
            })
        
        return issues
    
    def get_statistics(self, text: str) -> Dict[str, Any]:
        """获取文档统计信息"""
        lines = text.split('\n')
        words_cn = len(re.findall(r'[\u4e00-\u9fff]', text))
        words_en = len(re.findall(r'\b[a-zA-Z]+\b', text))
        
        return {
            "total_chars": len(text),
            "total_lines": len(lines),
            "blank_lines": sum(1 for line in lines if not line.strip()),
            "chinese_chars": words_cn,
            "english_words": words_en,
            "headings": len(re.findall(r'^#{1,6}\s+', text, re.MULTILINE)),
            "lists": len(re.findall(r'^[\s]*[•\-\*]\s', text, re.MULTILINE)),
            "code_blocks": len(re.findall(r'```', text)) // 2,
            "links": len(re.findall(r'\[([^\]]+)\]\(([^\)]+)\)', text)),
            "images": len(re.findall(r'!\[([^\]]*)\]\(([^\)]+)\)', text))
        }
    
    def _read_file(self, file_path: str) -> str:
        """读取文件内容"""
        ext = os.path.splitext(file_path)[1].lower()
        
        try:
            if ext in ['.txt', '.md']:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            
            elif ext == '.docx':
                from docx import Document
                doc = Document(file_path)
                return '\n'.join([p.text for p in doc.paragraphs])
            
            else:
                return ""
        except Exception as e:
            print(f"读取文件失败: {e}")
            return ""
    
    def generate_report(self, check_result: Dict[str, Any]) -> str:
        """生成检查报告"""
        if not check_result.get("success"):
            return f"检查失败: {check_result.get('error')}"
        
        report = []
        report.append("=" * 60)
        report.append("文档一致性检查报告")
        report.append("=" * 60)
        report.append(f"\n文件: {check_result['file_path']}")
        report.append(f"总问题数: {check_result['total_issues']}")
        
        # 术语问题
        if check_result["term_issues"]:
            report.append("\n## 术语一致性问题")
            for issue in check_result["term_issues"]:
                report.append(f"\n- [{issue['severity'].upper()}] {issue['type']}")
                report.append(f"  {issue['suggestion']}")
                if 'variants_found' in issue:
                    report.append(f"  发现变体: {issue['variants_found']}")
        
        # 格式问题
        if check_result["format_issues"]:
            report.append("\n## 格式一致性问题")
            for issue in check_result["format_issues"]:
                report.append(f"\n- [{issue['severity'].upper()}] {issue['type']}")
                report.append(f"  {issue['suggestion']}")
        
        # 引用问题
        if check_result["reference_issues"]:
            report.append("\n## 引用规范问题")
            for issue in check_result["reference_issues"]:
                report.append(f"\n- [{issue['severity'].upper()}] {issue['type']}")
                report.append(f"  {issue['suggestion']}")
        
        # 统计信息
        stats = check_result["statistics"]
        report.append("\n## 文档统计")
        report.append(f"- 总字符数: {stats['total_chars']}")
        report.append(f"- 中文字符: {stats['chinese_chars']}")
        report.append(f"- 英文单词: {stats['english_words']}")
        report.append(f"- 总行数: {stats['total_lines']}")
        report.append(f"- 标题数: {stats['headings']}")
        report.append(f"- 列表项: {stats['lists']}")
        report.append(f"- 代码块: {stats['code_blocks']}")
        report.append(f"- 链接数: {stats['links']}")
        
        return '\n'.join(report)


if __name__ == "__main__":
    checker = ConsistencyChecker()
    
    print("=" * 60)
    print("文档一致性检查器测试")
    print("=" * 60)
    
    # 创建测试文档
    test_content = """# AI技术概览

这是一个关于AI和机器学习的文档。

## 什么是人工智能

AI (artificial intelligence) 是计算机科学的一个分支。

### 机器学习基础

ML技术包括深度学习(DL)等。

- 列表项1
* 列表项2
• 列表项3



这里有多余的空行。

中文English混排示例。

[空链接]()

![](image.png)
"""
    
    test_file = "test_consistency.md"
    with open(test_file, 'w', encoding='utf-8') as f:
        f.write(test_content)
    
    # 执行检查
    result = checker.check_document(test_file)
    
    # 生成报告
    report = checker.generate_report(result)
    print(report)
    
    # 清理测试文件
    os.remove(test_file)
    
    print("\n✅ 一致性检查器就绪")
