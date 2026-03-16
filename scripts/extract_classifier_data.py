#!/usr/bin/env python3
"""
从各数据源提取任务分类训练样本，输出到 models/task_classifier/training_data.json。

数据来源（按优先级叠加）：
  1. chats/ 历史对话 JSON（user message + model response 的 task 字段）
  2. SEED_EXAMPLES  — 手工精标样本（覆盖所有任务类型）
  3. CORPUS_EXAMPLES — 来自 SmartDispatcher.TASK_CORPUS 的锚定词

用法：
  python scripts/extract_classifier_data.py
"""

import glob
import json
import os
import sys
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHATS_DIR  = os.path.join(REPO_ROOT, "chats")
OUTPUT_DIR = os.path.join(REPO_ROOT, "models", "task_classifier")

VALID_TASK_TYPES = {
    "CHAT", "CODER", "RESEARCH", "FILE_GEN", "WEB_SEARCH",
    "FILE_OP", "FILE_EDIT", "FILE_SEARCH", "PAINTER",
    "SYSTEM", "AGENT", "DOC_ANNOTATE", "MULTI_STEP",
}

# ---------------------------------------------------------------------------
# 手工精标样本——覆盖所有任务类型的典型表达、边界案例、歧义案例
# ---------------------------------------------------------------------------
SEED_EXAMPLES = [
    # ── SYSTEM ──────────────────────────────────────────────────────────────
    ("打开微信", "SYSTEM"),
    ("启动vscode", "SYSTEM"),
    ("关闭chrome", "SYSTEM"),
    ("截图", "SYSTEM"),
    ("关机", "SYSTEM"),
    ("打开计算器", "SYSTEM"),
    ("运行程序", "SYSTEM"),
    ("打开steam", "SYSTEM"),
    ("打开网易云音乐", "SYSTEM"),
    ("打开edge浏览器", "SYSTEM"),
    ("启动任务管理器", "SYSTEM"),
    ("打开文件夹", "SYSTEM"),
    ("调高音量", "SYSTEM"),
    ("打开加速器", "SYSTEM"),
    ("打开QQ音乐", "SYSTEM"),
    ("关掉qq", "SYSTEM"),
    ("打开记事本", "SYSTEM"),
    # ── AGENT ───────────────────────────────────────────────────────────────
    ("提醒我下午3点开会", "AGENT"),
    ("设置明天早上7点的闹钟", "AGENT"),
    ("给张三发微信说我晚点到", "AGENT"),
    ("帮我订明天上海到北京的高铁票", "AGENT"),
    ("用浏览器打开淘宝搜索耳机", "AGENT"),
    ("帮我定一个30分钟后的提醒", "AGENT"),
    ("明天上午10点给李四发邮件", "AGENT"),
    ("发微信给王五", "AGENT"),
    ("帮我买今晚8点的电影票", "AGENT"),
    ("日历里加一个明天下午2点的会议", "AGENT"),
    ("给他发消息说项目完成了", "AGENT"),
    ("浏览器打开京东", "AGENT"),
    ("自动帮我在网上购买", "AGENT"),
    ("发邮件通知团队", "AGENT"),
    ("订一张明天去广州的机票", "AGENT"),
    # ── PAINTER ─────────────────────────────────────────────────────────────
    ("帮我画一张海边日落的图片", "PAINTER"),
    ("生成一张科幻风格的封面图", "PAINTER"),
    ("画一幅水墨山水画", "PAINTER"),
    ("AI生成一张猫的照片", "PAINTER"),
    ("创作一张未来城市壁纸", "PAINTER"),
    ("帮我做个卡通头像", "PAINTER"),
    ("画一个宇宙飞船的概念图", "PAINTER"),
    ("给我生成一张赛博朋克风的插画", "PAINTER"),
    ("画一张猫咪在月光下玩耍的图片", "PAINTER"),
    ("生成一张写实风格的山峰照片", "PAINTER"),
    ("帮我设计一张海报图片", "PAINTER"),
    ("画一张古风女子图", "PAINTER"),
    # ── CODER ────────────────────────────────────────────────────────────────
    ("帮我写一个Python快速排序函数", "CODER"),
    ("写个JavaScript的防抖函数", "CODER"),
    ("实现一个二叉树的遍历", "CODER"),
    ("写代码解析JSON字符串", "CODER"),
    ("给我写一个爬虫脚本", "CODER"),
    ("这段代码有什么bug？", "CODER"),
    ("帮我debug这段代码报错", "CODER"),
    ("用matplotlib画折线图", "CODER"),
    ("作一个柱状图展示销售数据", "CODER"),
    ("生成一张数据饼图", "CODER"),
    ("帮我重构这个函数让它更简洁", "CODER"),
    ("写一段SQL查询语句", "CODER"),
    ("给我写个登录接口", "CODER"),
    ("写一个递归的斐波那契函数", "CODER"),
    ("帮我实现一个简单的神经网络", "CODER"),
    ("写个Python读取Excel的脚本", "CODER"),
    ("帮我用seaborn画热力图", "CODER"),
    ("写一个类实现栈数据结构", "CODER"),
    ("用plotly生成交互式折线图", "CODER"),
    ("散点图展示两个变量的相关性", "CODER"),
    ("帮我写个数据清洗脚本", "CODER"),
    # ── RESEARCH ─────────────────────────────────────────────────────────────
    ("深入分析量子计算的技术原理和未来发展方向", "RESEARCH"),
    ("全面调研中国新能源汽车市场竞争格局", "RESEARCH"),
    ("系统研究大语言模型的安全性问题", "RESEARCH"),
    ("对比分析GPT-4和Claude3的能力差异", "RESEARCH"),
    ("深度研究碳中和政策对制造业的影响", "RESEARCH"),
    ("全面分析数字人民币的推进进展和挑战", "RESEARCH"),
    ("系统梳理区块链技术在供应链中的应用", "RESEARCH"),
    ("深入研究短视频平台的算法推荐机制", "RESEARCH"),
    ("全面调研全球芯片短缺的成因和影响", "RESEARCH"),
    ("对比分析React和Vue在大型项目中的优劣", "RESEARCH"),
    # ── WEB_SEARCH ───────────────────────────────────────────────────────────
    ("今天北京天气怎么样", "WEB_SEARCH"),
    ("明天上海天气", "WEB_SEARCH"),
    ("原油价格现在多少", "WEB_SEARCH"),
    ("比特币今日价格", "WEB_SEARCH"),
    ("黄金行情最新", "WEB_SEARCH"),
    ("现在美元兑人民币汇率", "WEB_SEARCH"),
    ("最新新闻今天", "WEB_SEARCH"),
    ("股市今日行情", "WEB_SEARCH"),
    ("布伦特原油期货价格", "WEB_SEARCH"),
    ("以太坊现价", "WEB_SEARCH"),
    ("下周杭州天气预报", "WEB_SEARCH"),
    ("今天有什么新闻", "WEB_SEARCH"),
    ("白银最新价格", "WEB_SEARCH"),
    ("道琼斯指数今天多少", "WEB_SEARCH"),
    ("目前乌克兰局势最新进展", "WEB_SEARCH"),
    ("最新科技新闻", "WEB_SEARCH"),
    ("近期基金行情", "WEB_SEARCH"),
    ("天气预报明天下雨吗", "WEB_SEARCH"),
    ("今日日元汇率", "WEB_SEARCH"),
    # ── FILE_GEN ─────────────────────────────────────────────────────────────
    ("帮我生成一份项目可行性分析Word文档", "FILE_GEN"),
    ("做一个产品介绍PPT", "FILE_GEN"),
    ("帮我做一份2025年Q1季度报告", "FILE_GEN"),
    ("创建一个Excel格式的考勤表模板", "FILE_GEN"),
    ("生成一份合同模板PDF", "FILE_GEN"),
    ("帮我写一份商业计划书文档", "FILE_GEN"),
    ("制作一个公司介绍PPT模板", "FILE_GEN"),
    ("帮我将研究内容导出为Word报告", "FILE_GEN"),
    ("生成一份项目总结文档", "FILE_GEN"),
    ("做一个员工培训PPT", "FILE_GEN"),
    ("帮我做财务总结的Excel表格", "FILE_GEN"),
    ("生成周报模板文档", "FILE_GEN"),
    ("做ppt展示项目进度", "FILE_GEN"),
    # ── DOC_ANNOTATE ─────────────────────────────────────────────────────────
    ("润色一下这篇论文摘要", "DOC_ANNOTATE"),
    ("帮我校对这段文字有没有错别字", "DOC_ANNOTATE"),
    ("修改这段描述使它更专业", "DOC_ANNOTATE"),
    ("优化这段营销文案的措辞", "DOC_ANNOTATE"),
    ("[FILE_ATTACHED:.docx] 润色这篇报告", "DOC_ANNOTATE"),
    ("[FILE_ATTACHED:.docx] 把不合适的翻译标注出来", "DOC_ANNOTATE"),
    ("[FILE_ATTACHED:.docx] 找出逻辑不通顺的地方批注", "DOC_ANNOTATE"),
    ("帮我批注这段代码的问题", "DOC_ANNOTATE"),
    ("改写这段文字让它更有感染力", "DOC_ANNOTATE"),
    ("这段话语义不清，帮我修改", "DOC_ANNOTATE"),
    ("[FILE_ATTACHED:.docx] 把语言生硬的地方改一改", "DOC_ANNOTATE"),
    # ── CHAT ─────────────────────────────────────────────────────────────────
    ("什么是机器学习", "CHAT"),
    ("你好", "CHAT"),
    ("帮我翻译这句话成英文", "CHAT"),
    ("介绍一下React框架", "CHAT"),
    ("如何写一个排序算法", "CHAT"),
    ("python怎么安装第三方库", "CHAT"),
    ("什么是docker", "CHAT"),
    ("二战是哪年开始的", "CHAT"),
    ("量子计算是什么", "CHAT"),
    ("给我解释一下递归的概念", "CHAT"),
    ("写一段自我介绍", "CHAT"),
    ("帮我想几个公司名字", "CHAT"),
    ("今天吃什么好", "CHAT"),
    ("谢谢你", "CHAT"),
    ("帮我翻译这段英文", "CHAT"),
    ("在Windows环境里快速启动bash虚拟环境一般用什么办法", "CHAT"),
    ("研究一下这个问题", "CHAT"),      # 日常"研究一下" ≠ 深度研究
    ("介绍一下量子计算", "CHAT"),
    ("布伦特原油和WTI原油有什么区别", "CHAT"),  # 知识问答
    ("原油是什么", "CHAT"),             # 概念问答
    ("什么是递归", "CHAT"),
    ("帮我分析一下这家公司", "CHAT"),
    ("[FILE_ATTACHED:.pdf] 告诉我这份文件说的是什么", "CHAT"),
    ("[FILE_ATTACHED:.pdf] 分析这份合同", "CHAT"),
    # ── FILE_SEARCH ──────────────────────────────────────────────────────────
    ("在桌面找一下叫report的文件", "FILE_SEARCH"),
    ("C:\\Users\\Downloads下有什么文件", "FILE_SEARCH"),
    ("找一下最近修改的pdf文件", "FILE_SEARCH"),
    ("查找workspace目录里的所有py文件", "FILE_SEARCH"),
    ("帮我找一个叫config的文件", "FILE_SEARCH"),
    # ── FILE_OP ──────────────────────────────────────────────────────────────
    ("把Downloads里的图片批量移动到Pictures", "FILE_OP"),
    ("读取C:\\data\\log.txt的内容", "FILE_OP"),
    ("列出当前文件夹下所有文件", "FILE_OP"),
    ("批量重命名桌面的截图文件", "FILE_OP"),
    # ── FILE_EDIT ────────────────────────────────────────────────────────────
    ("修改config.json里的端口号为8080", "FILE_EDIT"),
    ("替换readme.md里的作者名", "FILE_EDIT"),
    ("删除log.txt里的第5行", "FILE_EDIT"),
]

# ---------------------------------------------------------------------------
# TASK_CORPUS 锚定词（来自 SmartDispatcher，用于增强低频类别）
# ---------------------------------------------------------------------------
CORPUS_EXAMPLES = [
    ("画一张图", "PAINTER"), ("帮我画", "PAINTER"), ("生成图片", "PAINTER"),
    ("写代码", "CODER"), ("帮我写个函数", "CODER"), ("python实现", "CODER"),
    ("帮我作图", "CODER"), ("作一个折线图", "CODER"), ("画柱状图", "CODER"),
    ("画饼图", "CODER"), ("生成图表", "CODER"), ("数据可视化", "CODER"),
    ("用matplotlib画", "CODER"), ("画散点图", "CODER"), ("plot数据", "CODER"),
    ("生成word文档", "FILE_GEN"), ("做ppt", "FILE_GEN"), ("帮我做一份", "FILE_GEN"),
    ("创建pdf", "FILE_GEN"), ("制作幻灯片", "FILE_GEN"),
    ("深入分析", "RESEARCH"), ("全面调研", "RESEARCH"), ("对比分析", "RESEARCH"),
    ("今天天气", "WEB_SEARCH"), ("股价多少", "WEB_SEARCH"), ("最新新闻", "WEB_SEARCH"),
    ("目前价格", "WEB_SEARCH"), ("现在价格", "WEB_SEARCH"), ("原油价格", "WEB_SEARCH"),
    ("黄金价格", "WEB_SEARCH"), ("汇率", "WEB_SEARCH"), ("实时价格", "WEB_SEARCH"),
    ("你好", "CHAT"), ("是什么", "CHAT"), ("介绍一下", "CHAT"),
    ("打开微信", "SYSTEM"), ("启动chrome", "SYSTEM"), ("关闭qq", "SYSTEM"),
    ("截图", "SYSTEM"), ("关机", "SYSTEM"),
    ("发微信", "AGENT"), ("设提醒", "AGENT"), ("设闹钟", "AGENT"),
    ("帮我买票", "AGENT"), ("订票", "AGENT"), ("提醒我", "AGENT"),
    ("浏览器打开", "AGENT"),
]


def extract_from_chats(chats_dir: str) -> list[tuple[str, str]]:
    """从对话历史 JSON 中提取 (user_input, task_type) 对。"""
    samples = []
    json_files = glob.glob(os.path.join(chats_dir, "*.json"))

    for filepath in json_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            continue

        if not isinstance(history, list):
            continue

        for i, entry in enumerate(history):
            if entry.get("role") != "user":
                continue
            if i + 1 >= len(history):
                continue

            model_entry = history[i + 1]
            if model_entry.get("role") != "model":
                continue

            task = model_entry.get("task")
            if task not in VALID_TASK_TYPES:
                continue

            parts = entry.get("parts", [])
            if not parts or not isinstance(parts[0], str):
                continue
            user_text = parts[0].strip()
            if len(user_text) < 2:
                continue

            # 跳过错误响应（任务标签可能是错的）
            resp_parts = model_entry.get("parts", [])
            if resp_parts and str(resp_parts[0]).startswith("❌"):
                continue

            samples.append((user_text, task))

    return samples


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_samples: list[dict] = []
    seen: set[tuple] = set()

    def add(text: str, label: str):
        key = (text.strip().lower(), label)
        if key not in seen:
            seen.add(key)
            all_samples.append({"text": text.strip(), "label": label})

    # 1. 对话历史
    chat_samples = extract_from_chats(CHATS_DIR)
    for text, label in chat_samples:
        add(text, label)
    print(f"[Extract] 对话历史: {len(chat_samples)} 条")

    # 2. 手工精标样本
    for text, label in SEED_EXAMPLES:
        add(text, label)
    print(f"[Extract] 手工精标: {len(SEED_EXAMPLES)} 条")

    # 3. 语料锚定词
    for text, label in CORPUS_EXAMPLES:
        add(text, label)
    print(f"[Extract] 语料锚定: {len(CORPUS_EXAMPLES)} 条")

    # 保存
    output_path = os.path.join(OUTPUT_DIR, "training_data.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_samples, f, ensure_ascii=False, indent=2)

    print(f"\n[Extract] 总唯一样本: {len(all_samples)}")
    counts = Counter(s["label"] for s in all_samples)
    for label, count in sorted(counts.items()):
        print(f"  {label:<15}: {count}")
    print(f"\n[Extract] 已保存到: {output_path}")


if __name__ == "__main__":
    main()
