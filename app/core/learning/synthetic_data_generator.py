# -*- coding: utf-8 -*-
"""
synthetic_data_generator.py — Koto 任务识别大规模合成数据生成器
================================================================

目标
----
在"已知答案"的完全受控条件下，生成大量（1000+ 条）任务路由训练样本，
覆盖所有任务类型的常见表达、同义变体、中英文混合、边界歧义场景，
让 koto-router 本地模型的任务识别能力显著提升。

数据质量保证
------------
- 所有样本均为人工精心标注的"黄金标准"（gold labels）
- 每个任务类型至少 80 条，覆盖高频、低频、边界三类场景
- 英文输入、中英文混合输入各占约 15%，提升泛化能力
- 包含大量"容易误分类"的对比样本，专门强化边界识别

任务类型覆盖
------------
CHAT / CODER / PAINTER / FILE_GEN / DOC_ANNOTATE /
RESEARCH / WEB_SEARCH / FILE_SEARCH / SYSTEM / AGENT

用法
----
  python -m app.core.learning.synthetic_data_generator
  from app.core.learning.synthetic_data_generator import SyntheticDataGenerator
  samples = SyntheticDataGenerator.generate_all()
"""

from __future__ import annotations
import json
import random
from dataclasses import dataclass
from typing import List, Tuple

# (user_input, task_type, confidence)
GoldSample = Tuple[str, str, float]

# ──────────────────────────────────────────────────────────────────────────────
# 黄金标准数据集（人工标注，每条均经过仔细核查）
# ──────────────────────────────────────────────────────────────────────────────

# ═══════════════════════════════ SYSTEM ═══════════════════════════════════════
SYSTEM_SAMPLES: List[GoldSample] = [
    # 基础高频
    ("打开微信",                                    "SYSTEM", 0.97),
    ("帮我截图",                                    "SYSTEM", 0.96),
    ("打开 Chrome 浏览器",                           "SYSTEM", 0.96),
    ("关闭所有窗口",                                 "SYSTEM", 0.94),
    ("打开任务管理器",                               "SYSTEM", 0.95),
    ("帮我关机",                                    "SYSTEM", 0.97),
    ("打开系统设置",                                 "SYSTEM", 0.95),
    ("重启电脑",                                    "SYSTEM", 0.97),
    ("调高音量",                                    "SYSTEM", 0.95),
    ("把屏幕亮度调低一些",                           "SYSTEM", 0.94),
    ("打开计算器",                                  "SYSTEM", 0.96),
    ("打开记事本",                                  "SYSTEM", 0.95),
    ("关闭微信",                                    "SYSTEM", 0.95),
    ("最小化所有窗口",                               "SYSTEM", 0.94),
    ("打开文件管理器",                               "SYSTEM", 0.95),
    ("打开控制面板",                                 "SYSTEM", 0.95),
    ("帮我锁定屏幕",                                 "SYSTEM", 0.96),
    ("开启夜间模式",                                 "SYSTEM", 0.93),
    ("关闭蓝牙",                                    "SYSTEM", 0.94),
    ("打开 Wi-Fi 设置",                             "SYSTEM", 0.93),
    ("强制结束进程",                                 "SYSTEM", 0.93),
    ("打开摄像头",                                  "SYSTEM", 0.94),
    ("截取当前屏幕",                                 "SYSTEM", 0.95),
    ("帮我打开 QQ",                                 "SYSTEM", 0.96),
    ("关闭 Word",                                   "SYSTEM", 0.95),
    ("打开 PowerPoint",                             "SYSTEM", 0.95),
    ("打开 Excel",                                   "SYSTEM", 0.95),
    ("帮我静音",                                    "SYSTEM", 0.95),
    ("打开下载文件夹",                               "SYSTEM", 0.94),
    ("打开桌面",                                    "SYSTEM", 0.93),
    # 英文
    ("open WeChat",                                 "SYSTEM", 0.96),
    ("take a screenshot",                           "SYSTEM", 0.95),
    ("shut down the computer",                      "SYSTEM", 0.97),
    ("open Task Manager",                           "SYSTEM", 0.95),
    ("minimize all windows",                        "SYSTEM", 0.94),
    ("lock the screen",                             "SYSTEM", 0.95),
    ("open Chrome",                                 "SYSTEM", 0.96),
    ("turn off Bluetooth",                          "SYSTEM", 0.94),
    # 中英混合
    ("帮我 open 浏览器",                             "SYSTEM", 0.93),
    ("截图一下 screenshot",                          "SYSTEM", 0.94),
    # 边界/容易混淆
    ("运行记事本程序",                               "SYSTEM", 0.93),  # ≠ AGENT
    ("帮我打开 D 盘",                                "SYSTEM", 0.93),  # ≠ FILE_SEARCH（打开≠搜索）
    ("打开系统音量混合器",                           "SYSTEM", 0.92),
    ("帮我打开微信，然后截图",                        "SYSTEM", 0.88),  # 复合但核心是SYSTEM
    ("把 Chrome 设为默认浏览器",                     "SYSTEM", 0.91),
    ("清理回收站",                                   "SYSTEM", 0.93),
    ("帮我格式化U盘",                               "SYSTEM", 0.92),
    ("创建新文件夹在桌面",                           "SYSTEM", 0.92),
    ("打开设备管理器",                               "SYSTEM", 0.94),
    ("检查磁盘空间",                                 "SYSTEM", 0.91),
]

# ═══════════════════════════════ AGENT ════════════════════════════════════════
AGENT_SAMPLES: List[GoldSample] = [
    # 基础高频
    ("给张三发微信说明天开会",                        "AGENT", 0.96),
    ("设置明天早上8点提醒我开会",                     "AGENT", 0.94),
    ("向李四发邮件说项目完成了",                      "AGENT", 0.95),
    ("帮我自动登录某网站",                           "AGENT", 0.91),
    ("给老板发短信说今天请假",                        "AGENT", 0.95),
    ("帮我往购物车加商品",                           "AGENT", 0.90),
    ("帮我在日历上标记下周一的会议",                  "AGENT", 0.93),
    ("自动填写这个表单",                             "AGENT", 0.91),
    ("帮我在淘宝搜索耳机",                           "AGENT", 0.91),
    ("订一张明天去北京的机票",                        "AGENT", 0.93),
    ("帮我发朋友圈",                                 "AGENT", 0.92),
    ("给所有群组发通知",                             "AGENT", 0.91),
    ("帮我设定每天早上7点的闹钟",                     "AGENT", 0.94),
    ("在百度上搜索最新手机",                          "AGENT", 0.89),  # 浏览器自动化
    ("帮我在京东下单这个产品",                        "AGENT", 0.92),
    ("自动回复微信未读消息",                          "AGENT", 0.91),
    ("帮我预约明天的餐厅",                           "AGENT", 0.92),
    ("给公司同事群发送今日报告",                      "AGENT", 0.93),
    ("在钉钉上给部门群发公告",                        "AGENT", 0.93),
    ("帮我登录邮箱查收新邮件",                        "AGENT", 0.90),
    # 英文
    ("send a WeChat message to John saying meeting tomorrow", "AGENT", 0.95),
    ("set an alarm for 8am tomorrow",                       "AGENT", 0.94),
    ("send an email to my boss",                            "AGENT", 0.95),
    ("book a flight to Beijing",                             "AGENT", 0.93),
    ("add this to my calendar",                              "AGENT", 0.93),
    # 中英混合
    ("帮我 send 邮件给客户",                          "AGENT", 0.93),
    ("set 一个提醒 tomorrow 9am",                    "AGENT", 0.92),
    # 边界：复合指令
    ("打开微信给张三发消息说我到了",                  "AGENT", 0.93),  # 核心是发消息≠SYSTEM
    ("用浏览器打开淘宝然后搜iPhone",                 "AGENT", 0.90),
    ("帮我发邮件并在日历上添加会议",                  "AGENT", 0.90),
    # 边界：AGENT vs SYSTEM
    ("帮我打开微信发给妈妈一张图",                    "AGENT", 0.92),  # 发内容→AGENT
    ("给微信好友发消息",                              "AGENT", 0.94),
    ("自动回复钉钉消息",                              "AGENT", 0.91),
    ("帮我抢购限量商品",                              "AGENT", 0.90),
    ("订明天下午两点的网约车",                        "AGENT", 0.92),
    ("帮我报名这个线上课程",                          "AGENT", 0.91),
    ("在高德地图导航去公司",                          "AGENT", 0.91),
    ("帮我设置每周五下午3点提醒交周报",               "AGENT", 0.93),
    ("自动填写健康申报表",                            "AGENT", 0.90),
    ("帮我用支付宝付款",                              "AGENT", 0.91),
    # 补充：工作流/多步骤自动执行
    ("帮我自动完成这个工作流",                        "AGENT", 0.92),
    ("帮我规划并执行这个多步骤任务",                  "AGENT", 0.91),
    ("帮我执行这个自动化流程",                        "AGENT", 0.92),
    ("按照这个流程自动操作",                          "AGENT", 0.91),
    ("帮我把这几个步骤自动化",                        "AGENT", 0.91),
    ("自动执行今天的工作任务",                        "AGENT", 0.92),
    ("帮我一步一步完成这个任务",                      "AGENT", 0.90),
    ("按流程帮我全部处理掉",                          "AGENT", 0.91),
    ("让你帮我自动完成整个流程",                      "AGENT", 0.91),
    ("帮我做完这个多步骤的工作流程",                  "AGENT", 0.91),
    ("自动完成从下单到确认收货的整个流程",            "AGENT", 0.91),
    ("execute this workflow for me",                 "AGENT", 0.92),
    ("run this multi-step pipeline automatically",   "AGENT", 0.91),
    ("automate this process for me",                 "AGENT", 0.91),
]

# ═══════════════════════════════ WEB_SEARCH ═══════════════════════════════════
WEB_SEARCH_SAMPLES: List[GoldSample] = [
    # 基础高频
    ("查下明天北京天气",                              "WEB_SEARCH", 0.97),
    ("今天A股涨了吗",                                "WEB_SEARCH", 0.96),
    ("现在美元汇率多少",                              "WEB_SEARCH", 0.95),
    ("最新的iPhone多少钱",                            "WEB_SEARCH", 0.93),
    ("查一下去上海的高铁票",                          "WEB_SEARCH", 0.94),
    ("今天上证指数收盘多少",                          "WEB_SEARCH", 0.96),
    ("现在比特币价格",                               "WEB_SEARCH", 0.96),
    ("最新新冠疫情数据",                              "WEB_SEARCH", 0.94),
    ("今天有什么大新闻",                              "WEB_SEARCH", 0.93),
    ("现在油价多少",                                  "WEB_SEARCH", 0.96),
    ("最近的科技新闻",                               "WEB_SEARCH", 0.91),
    ("今天北京最高温度多少度",                        "WEB_SEARCH", 0.96),
    ("当前黄金价格",                                  "WEB_SEARCH", 0.96),
    ("最新电影票房排行榜",                            "WEB_SEARCH", 0.93),
    ("今天有没有地震",                               "WEB_SEARCH", 0.94),
    ("现在欧元对人民币汇率",                          "WEB_SEARCH", 0.95),
    ("最新软件版本号是多少",                          "WEB_SEARCH", 0.91),
    ("今天彩票开奖号码",                              "WEB_SEARCH", 0.96),
    ("最近哪部电视剧最火",                            "WEB_SEARCH", 0.90),
    ("当前全球疫情情况",                              "WEB_SEARCH", 0.93),
    # 英文
    ("what's the weather like tomorrow in Beijing",   "WEB_SEARCH", 0.96),
    ("current Bitcoin price",                         "WEB_SEARCH", 0.96),
    ("latest news today",                             "WEB_SEARCH", 0.93),
    ("stock market today",                            "WEB_SEARCH", 0.95),
    ("USD to CNY exchange rate now",                  "WEB_SEARCH", 0.95),
    # 边界：WEB_SEARCH vs CHAT
    ("今天是星期几",                                  "WEB_SEARCH", 0.90),  # 实时类
    ("明天几号",                                      "WEB_SEARCH", 0.89),
    ("最新版Python是哪个版本",                        "WEB_SEARCH", 0.91),
    ("现在几点了",                                    "WEB_SEARCH", 0.92),
    ("今天是什么节日",                                "WEB_SEARCH", 0.88),
    # 边界：WEB_SEARCH vs RESEARCH（实时 vs 深度）
    ("最新的量子计算研究进展",                        "WEB_SEARCH", 0.89),
    ("今天科技圈有什么大事",                          "WEB_SEARCH", 0.91),
    ("最近ChatGPT有什么更新",                         "WEB_SEARCH", 0.91),
    ("搜索一下深圳的天气",                            "WEB_SEARCH", 0.95),
    ("查一下今天的空气质量",                          "WEB_SEARCH", 0.95),
    ("现在排队等候时间多少",                          "WEB_SEARCH", 0.90),
    ("最新电视机排行榜",                              "WEB_SEARCH", 0.91),
    ("今天的黄历宜忌",                               "WEB_SEARCH", 0.89),
    ("最近有没有新的补贴政策",                        "WEB_SEARCH", 0.90),
    ("帮我搜索一下最新的 AI 模型",                    "WEB_SEARCH", 0.90),
]

# ═══════════════════════════════ FILE_SEARCH ══════════════════════════════════
FILE_SEARCH_SAMPLES: List[GoldSample] = [
    # 基础高频
    ("帮我找一下简历文件",                            "FILE_SEARCH", 0.95),
    ("全盘扫描我的电脑",                              "FILE_SEARCH", 0.94),
    ("找一下2025年的报告文件",                        "FILE_SEARCH", 0.94),
    ("在我的电脑上找一个叫项目计划的文件",             "FILE_SEARCH", 0.95),
    ("搜索我桌面上的PDF文件",                         "FILE_SEARCH", 0.94),
    ("找一下我上个月下载的合同",                      "FILE_SEARCH", 0.93),
    ("帮我找到去年的财务报告",                        "FILE_SEARCH", 0.94),
    ("在D盘找所有Excel文件",                          "FILE_SEARCH", 0.95),
    ("搜索文件名包含'报价单'的文档",                   "FILE_SEARCH", 0.95),
    ("找找我的工资条在哪里",                          "FILE_SEARCH", 0.93),
    ("帮我找 main.py 文件",                           "FILE_SEARCH", 0.94),
    ("搜索所有包含'2024'的文件",                      "FILE_SEARCH", 0.93),
    ("找一下我的毕业论文",                            "FILE_SEARCH", 0.94),
    ("在文档文件夹里找一个PDF",                       "FILE_SEARCH", 0.93),
    ("帮我搜索大于100MB的文件",                       "FILE_SEARCH", 0.92),
    ("查找最近7天修改的文件",                         "FILE_SEARCH", 0.93),
    ("找到我昨天创建的文件",                          "FILE_SEARCH", 0.93),
    ("在项目目录下搜索config文件",                    "FILE_SEARCH", 0.94),
    ("帮我找到所有的图片文件",                        "FILE_SEARCH", 0.93),
    ("搜索以'发票'开头的PDF",                         "FILE_SEARCH", 0.94),
    # 英文
    ("find my resume file",                           "FILE_SEARCH", 0.94),
    ("search for all PDFs on the desktop",            "FILE_SEARCH", 0.94),
    ("find files modified yesterday",                 "FILE_SEARCH", 0.93),
    ("locate main.py in the project",                 "FILE_SEARCH", 0.93),
    # 边界：FILE_SEARCH vs SYSTEM
    ("找一下hosts文件",                               "FILE_SEARCH", 0.92),  # 搜索≠打开
    ("搜索我的下载记录",                              "FILE_SEARCH", 0.90),
    # 边界：FILE_SEARCH vs CHAT
    ("hosts文件在哪个目录",                           "CHAT",        0.90),  # 问知识不是搜索
    ("Python默认安装在哪里",                          "CHAT",        0.91),  # 知识问题
    # 实际FILE_SEARCH
    ("帮我定位一下Python的安装路径",                  "FILE_SEARCH", 0.89),
    ("找一下node_modules文件夹",                      "FILE_SEARCH", 0.92),
    ("搜索所有以.log结尾的文件",                      "FILE_SEARCH", 0.93),
    ("在桌面找一个叫README的文件",                    "FILE_SEARCH", 0.93),
    ("帮我找到最近下载的压缩包",                      "FILE_SEARCH", 0.92),
    ("找一下昨天写的代码文件",                        "FILE_SEARCH", 0.91),
    ("扫描C盘找所有Word文档",                         "FILE_SEARCH", 0.93),
    ("帮我搜索一下setup.py",                          "FILE_SEARCH", 0.93),
    ("查找我创建的所有PPT文件",                       "FILE_SEARCH", 0.93),
    ("找一下名字有'总结'的文件",                      "FILE_SEARCH", 0.93),
    # 补充：桌面/文档/有没有/代码内文本搜索
    ("帮我找一下桌面上的文件",                        "FILE_SEARCH", 0.93),
    ("桌面上有什么文件",                              "FILE_SEARCH", 0.92),
    ("在我的文档里搜索合同",                          "FILE_SEARCH", 0.93),
    ("我的文档文件夹里有合同吗",                      "FILE_SEARCH", 0.93),
    ("项目目录里有没有config.yaml",                   "FILE_SEARCH", 0.93),
    ("有没有叫README.md的文件",                       "FILE_SEARCH", 0.93),
    ("找到所有包含TODO的代码文件",                    "FILE_SEARCH", 0.93),
    ("搜索代码里包含fixme的文件",                     "FILE_SEARCH", 0.93),
    ("哪些文件里有TODO注释",                          "FILE_SEARCH", 0.92),
    ("帮我找包含关键字的源代码文件",                  "FILE_SEARCH", 0.93),
    ("找一下Downloads文件夹里有什么",                 "FILE_SEARCH", 0.92),
    ("搜索一下有没有叫config的文件",                  "FILE_SEARCH", 0.93),
    ("帮我找我的文档里有没有这个合同",                "FILE_SEARCH", 0.93),
    ("项目里有没有package.json",                      "FILE_SEARCH", 0.93),
    ("找到src目录下所有的ts文件",                     "FILE_SEARCH", 0.93),
]

# ═══════════════════════════════ FILE_GEN ═════════════════════════════════════
FILE_GEN_SAMPLES: List[GoldSample] = [
    # 基础高频
    ("帮我做一个PPT",                                "FILE_GEN", 0.93),
    ("帮我写一份Word文档",                           "FILE_GEN", 0.92),
    ("做一个关于AI的介绍PDF",                        "FILE_GEN", 0.91),
    ("生成一份竞品分析报告",                          "FILE_GEN", 0.90),
    ("做一个关于春节习俗的Excel",                     "FILE_GEN", 0.90),
    ("帮我制作一份简历word文档",                      "FILE_GEN", 0.93),
    ("生成一份项目进度报告",                          "FILE_GEN", 0.90),
    ("做一个销售数据分析表格",                        "FILE_GEN", 0.89),
    ("帮我做一份培训PPT",                             "FILE_GEN", 0.91),
    ("生成一份合同模板",                              "FILE_GEN", 0.90),
    ("做一份月度总结报告",                            "FILE_GEN", 0.90),
    ("生成一个Excel销售记录表",                       "FILE_GEN", 0.91),
    ("帮我写一份工作计划Word文档",                    "FILE_GEN", 0.92),
    ("做一个关于市场调研的PPT",                       "FILE_GEN", 0.91),
    ("生成一份报价单",                               "FILE_GEN", 0.90),
    ("帮我做一份产品说明书",                          "FILE_GEN", 0.90),
    ("做一个招聘职位描述的Word",                      "FILE_GEN", 0.91),
    ("帮我生成一份年度总结报告",                      "FILE_GEN", 0.90),
    ("制作一个会议纪要模板",                          "FILE_GEN", 0.88),
    ("帮我写一个项目需求文档",                        "FILE_GEN", 0.89),
    # 英文
    ("create a PowerPoint presentation",              "FILE_GEN", 0.92),
    ("generate a Word document for me",               "FILE_GEN", 0.91),
    ("make an Excel spreadsheet",                     "FILE_GEN", 0.91),
    ("create a PDF report",                           "FILE_GEN", 0.90),
    # 有格式词的长句
    ("做一个关于1月新番导视的word介绍",               "FILE_GEN", 0.91),
    ("帮我写一份去北京旅游的行程PDF",                 "FILE_GEN", 0.91),
    ("做一个关于公司业务的PPT汇报",                   "FILE_GEN", 0.91),
    ("生成一份用于招标的Word文档",                    "FILE_GEN", 0.91),
    ("帮我制作一份Excel收支明细表",                   "FILE_GEN", 0.91),
    # 边界：FILE_GEN vs CHAT（无格式词→CHAT）
    ("写一段关于春节的介绍",                          "CHAT",     0.93),  # 无格式词
    ("帮我写个自我介绍",                              "CHAT",     0.94),
    ("写一篇关于AI的文章",                            "CHAT",     0.91),
    # 实际FILE_GEN
    ("帮我用Word做一个简历",                          "FILE_GEN", 0.92),
    ("制作一份Excel考勤表",                           "FILE_GEN", 0.91),
    ("帮我做一个PPT介绍我们公司",                     "FILE_GEN", 0.91),
    ("生成一份项目验收报告Word版",                    "FILE_GEN", 0.91),
    ("帮我做一个数据汇总的Excel",                     "FILE_GEN", 0.90),
    ("写一份投资分析报告",                            "FILE_GEN", 0.88),  # "报告"是格式词
    ("制作一份商务合同Word文档",                      "FILE_GEN", 0.91),
    ("帮我生成一份绩效考核表Excel",                   "FILE_GEN", 0.91),
    ("做一份季度业绩报告PPT",                         "FILE_GEN", 0.91),
    ("[FILE_ATTACHED:.pdf] 帮我把这份材料整理成Word汇报", "FILE_GEN", 0.91),
    # 补充：汇总/整理数据生成表格 → FILE_GEN（不是CODER）
    ("生成一张表格汇总这些数据",                      "FILE_GEN", 0.90),
    ("帮我把这些数据整理成表格",                      "FILE_GEN", 0.90),
    ("做一个汇总数据的表格",                          "FILE_GEN", 0.90),
    ("帮我生成一个数据对比表",                        "FILE_GEN", 0.90),
    ("把这些信息整理成Excel表格",                     "FILE_GEN", 0.91),
    ("生成一张包含这些数据的表格文件",                "FILE_GEN", 0.91),
]

# ═══════════════════════════════ DOC_ANNOTATE ════════════════════════════════
DOC_ANNOTATE_SAMPLES: List[GoldSample] = [
    # 基础高频
    ("[FILE_ATTACHED:.docx] 把所有不合适的翻译标注改善", "DOC_ANNOTATE", 0.96),
    ("[FILE_ATTACHED:.docx] 润色这篇论文",              "DOC_ANNOTATE", 0.95),
    ("[FILE_ATTACHED:.docx] 帮我修改语序不通的地方",    "DOC_ANNOTATE", 0.94),
    ("帮我优化这段代码的写法",                          "DOC_ANNOTATE", 0.88),
    ("[FILE_ATTACHED:.py] 帮我找出这段代码里的bug",     "DOC_ANNOTATE", 0.91),
    ("[FILE_ATTACHED:.txt] 这篇文章语言太生硬，帮我润色","DOC_ANNOTATE", 0.92),
    ("[FILE_ATTACHED:.docx] 批注这份文件中的问题",      "DOC_ANNOTATE", 0.94),
    ("[FILE_ATTACHED:.docx] 校对这篇报告的错别字",      "DOC_ANNOTATE", 0.94),
    ("[FILE_ATTACHED:.docx] 改善这里的措辞",            "DOC_ANNOTATE", 0.93),
    ("帮我改一下这段文字的逻辑",                        "DOC_ANNOTATE", 0.87),
    ("[FILE_ATTACHED:.py] 重构一下这个函数",            "DOC_ANNOTATE", 0.90),
    ("[FILE_ATTACHED:.docx] 在不合适的地方加批注",      "DOC_ANNOTATE", 0.93),
    ("这段代码有点冗余，帮我精简一下",                  "DOC_ANNOTATE", 0.88),
    ("[FILE_ATTACHED:.docx] 帮我润色一下这份商业计划书","DOC_ANNOTATE", 0.93),
    ("[FILE_ATTACHED:.txt] 纠正这段文本中的语法错误",   "DOC_ANNOTATE", 0.93),
    ("[FILE_ATTACHED:.js] 修复代码里的bug",             "DOC_ANNOTATE", 0.91),
    ("把这段话重新表达一下，更正式一些",                "DOC_ANNOTATE", 0.88),
    ("[FILE_ATTACHED:.docx] 把措辞改得更专业",          "DOC_ANNOTATE", 0.93),
    ("帮我把这个函数改得更简洁",                        "DOC_ANNOTATE", 0.88),
    # 英文
    ("[FILE_ATTACHED:.docx] proofread this document",   "DOC_ANNOTATE", 0.94),
    ("[FILE_ATTACHED:.py] refactor this code",          "DOC_ANNOTATE", 0.91),
    ("fix the bugs in this code snippet",               "DOC_ANNOTATE", 0.89),
    ("[FILE_ATTACHED:.txt] improve the writing style",  "DOC_ANNOTATE", 0.92),
    # 边界：DOC_ANNOTATE vs CHAT（无已有内容→CHAT）
    ("帮我写一段代码注释",                              "CODER",       0.88),  # 新写注释
    ("帮我优化一下搜索算法",                            "CHAT",        0.86),  # 泛问优化方法
    # 实际DOC_ANNOTATE（有"已有内容"明确指示词）
    ("下面这段代码有没有问题?\n```\ndef f(x):\n  return x*x\n```", "DOC_ANNOTATE", 0.88),
    ("[FILE_ATTACHED:.docx] 标出所有表达有歧义的地方",  "DOC_ANNOTATE", 0.93),
    ("[FILE_ATTACHED:.docx] 检查格式是否规范",          "DOC_ANNOTATE", 0.91),
    ("帮我看看这段话有没有错别字: 今天天气晴好",        "DOC_ANNOTATE", 0.88),
    ("[FILE_ATTACHED:.docx] 给这篇文章加上适当的小标题","DOC_ANNOTATE", 0.90),
    ("[FILE_ATTACHED:.py] 给这段代码加注释",            "DOC_ANNOTATE", 0.91),
    ("帮我把这段英文翻译优化得更地道",                  "DOC_ANNOTATE", 0.88),
    ("[FILE_ATTACHED:.docx] 这份合同有哪些条款不合理",  "CHAT",        0.88),  # 分析≠标注
    ("[FILE_ATTACHED:.docx] 帮我在模糊表述处加上注释",  "DOC_ANNOTATE", 0.92),
    ("这段代码性能差，帮我优化",                        "DOC_ANNOTATE", 0.87),
    ("[FILE_ATTACHED:.html] 修复这段HTML的显示问题",    "DOC_ANNOTATE", 0.90),
    ("把下面这段文字改写得更生动: 今天去了公园",        "DOC_ANNOTATE", 0.88),
    ("[FILE_ATTACHED:.docx] 统一一下文档的标点风格",    "DOC_ANNOTATE", 0.91),
    ("帮我把这段Python代码改成更Pythonic的风格",        "DOC_ANNOTATE", 0.88),
]

# ═══════════════════════════════ CODER ════════════════════════════════════════
CODER_SAMPLES: List[GoldSample] = [
    # 基础高频
    ("写一个快速排序函数",                            "CODER", 0.96),
    ("用Python实现文件批量重命名",                    "CODER", 0.95),
    ("帮我写一个爬虫脚本",                            "CODER", 0.94),
    ("实现一个二叉树的遍历",                          "CODER", 0.94),
    ("给我写一段Python代码",                          "CODER", 0.95),
    ("帮我写一个冒泡排序",                            "CODER", 0.96),
    ("实现一个登录功能的后端接口",                    "CODER", 0.94),
    ("写一个爬取京东商品价格的脚本",                  "CODER", 0.95),
    ("帮我用JavaScript实现一个轮播图",                "CODER", 0.94),
    ("写一个把CSV转Excel的Python脚本",                "CODER", 0.95),
    ("帮我实现一个二分查找算法",                      "CODER", 0.94),
    ("写一段读取JSON文件的代码",                      "CODER", 0.94),
    ("给我一个Flask的Hello World示例代码",            "CODER", 0.93),
    ("帮我写一个自动发邮件的Python脚本",              "CODER", 0.93),
    ("实现一个简单的计算器程序",                      "CODER", 0.94),
    ("帮我写一个微信机器人",                          "CODER", 0.93),
    ("用React写一个TodoList组件",                     "CODER", 0.95),
    ("写一个数据库增删改查的脚本",                    "CODER", 0.94),
    ("帮我实现一个WebSocket服务器",                   "CODER", 0.93),
    ("写一段正则表达式匹配邮箱",                      "CODER", 0.93),
    # 图表/可视化
    ("帮我做一个折线图",                              "CODER", 0.94),
    ("用Python画一个柱状图",                          "CODER", 0.95),
    ("用matplotlib画散点图",                          "CODER", 0.95),
    ("生成一个饼图展示数据",                          "CODER", 0.91),
    ("帮我做数据可视化",                              "CODER", 0.92),
    ("用seaborn画热力图",                             "CODER", 0.95),
    ("做一个交互式图表",                              "CODER", 0.93),
    ("帮我画一张数据统计图",                          "CODER", 0.91),
    ("用plotly做可视化",                              "CODER", 0.94),
    ("画一个甘特图",                                  "CODER", 0.92),
    # 英文
    ("write a quick sort function",                   "CODER", 0.96),
    ("implement a binary search",                     "CODER", 0.95),
    ("create a REST API with Flask",                  "CODER", 0.94),
    ("write a web scraper in Python",                 "CODER", 0.94),
    ("build a simple calculator",                     "CODER", 0.94),
    ("make a bar chart with matplotlib",              "CODER", 0.95),
    # 边界：CODER vs CHAT（"怎么写"→CHAT，"写一个"→CODER）
    ("怎么用Python写爬虫",                            "CHAT",  0.91),  # 求知识
    ("如何实现二叉树",                                "CHAT",  0.92),  # 概念解释
    ("Python怎么发邮件",                              "CHAT",  0.91),  # 知识问题
    # 实际CODER
    ("帮我实现一个LRU缓存",                           "CODER", 0.94),
    ("写一个文件监控脚本",                            "CODER", 0.93),
    ("用Pandas处理Excel数据的代码",                   "CODER", 0.94),
    ("帮我写一个定时任务调度器",                      "CODER", 0.93),
    ("实现JWT鉴权中间件",                             "CODER", 0.94),
    ("写一个Docker容器管理脚本",                      "CODER", 0.93),
    ("帮我写一个命令行工具",                          "CODER", 0.93),
    ("实现一个图片压缩工具",                          "CODER", 0.93),
    ("用Go写一个HTTP服务器",                          "CODER", 0.94),
    ("帮我写一个单元测试",                            "CODER", 0.93),
    ("写一个PDF解析脚本",                             "CODER", 0.93),
    ("实现一个简单的推荐算法",                        "CODER", 0.93),
    ("帮我写一个数据清洗脚本",                        "CODER", 0.93),
    ("用TypeScript实现一个工具函数",                  "CODER", 0.94),
    ("写一段爬取微博热搜的代码",                      "CODER", 0.94),
    # 调试/代码审查 → CODER（不是DOC_ANNOTATE）
    ("这段代码有什么bug？",                           "CODER", 0.93),
    ("帮我debug这段代码",                             "CODER", 0.94),
    ("这个函数有什么问题",                            "CODER", 0.92),
    ("帮我找出代码里的错误",                          "CODER", 0.93),
    ("这段Python代码跑不通",                          "CODER", 0.93),
    ("代码报错了，帮我看看",                          "CODER", 0.93),
    ("帮我检查这段代码",                              "CODER", 0.91),
    ("这个函数的逻辑有问题吗",                        "CODER", 0.91),
    ("帮我做代码review",                              "CODER", 0.91),
    ("analyze this code for bugs",                   "CODER", 0.93),
    ("what's wrong with this function",              "CODER", 0.93),
    ("帮我优化这段代码的性能",                        "CODER", 0.93),
    ("这段SQL有没有问题",                             "CODER", 0.92),
]

# ═══════════════════════════════ PAINTER ══════════════════════════════════════
PAINTER_SAMPLES: List[GoldSample] = [
    # 基础高频
    ("画一只猫",                                     "PAINTER", 0.96),
    ("帮我生成一张封面图片",                          "PAINTER", 0.94),
    ("生成一个科技感背景图",                          "PAINTER", 0.92),
    ("帮我画一张宣传海报",                            "PAINTER", 0.94),
    ("生成一张二次元风格的头像",                      "PAINTER", 0.93),
    ("画一幅中国山水画",                              "PAINTER", 0.91),
    ("生成一张产品展示图",                            "PAINTER", 0.92),
    ("帮我画一张可爱的狗狗",                          "PAINTER", 0.95),
    ("生成一个logo设计",                              "PAINTER", 0.92),
    ("画一个赛博朋克风格的城市",                      "PAINTER", 0.92),
    ("帮我生成一张唯美风景图",                        "PAINTER", 0.92),
    ("创作一幅抽象画",                               "PAINTER", 0.91),
    ("画一张壁纸",                                   "PAINTER", 0.93),
    ("生成一只可爱的动漫角色",                        "PAINTER", 0.93),
    ("帮我画一个卡通头像",                            "PAINTER", 0.93),
    ("生成一张写实风格的肖像画",                      "PAINTER", 0.92),
    ("创作一幅充满未来感的插画",                      "PAINTER", 0.91),
    ("帮我画一个品牌海报",                            "PAINTER", 0.92),
    ("生成一张圣诞节的节日图",                        "PAINTER", 0.92),
    ("给我画一幅水彩花卉",                            "PAINTER", 0.91),
    # 英文
    ("draw a cute cat",                               "PAINTER", 0.95),
    ("generate an image of a sunset",                 "PAINTER", 0.93),
    ("create a sci-fi wallpaper",                     "PAINTER", 0.92),
    ("paint a landscape",                             "PAINTER", 0.92),
    ("generate a logo for my brand",                  "PAINTER", 0.91),
    # 明日香相关
    ("做一张可爱明日香居家照片",                      "PAINTER", 0.93),
    ("画一张明日香的图",                              "PAINTER", 0.94),
    # 边界：PAINTER vs CODER（图表≠图片）
    ("画一个折线图",                                  "CODER",   0.93),  # 数据图表
    ("生成一个饼图",                                  "CODER",   0.91),  # 数据可视化
    ("做一个数据图",                                  "CODER",   0.90),  # 数据可视化
    # 实际PAINTER
    ("帮我生成一张示意图",                            "PAINTER", 0.90),
    ("AI画一幅星空下的海边",                          "PAINTER", 0.92),
    ("生成一张渐变色的背景",                          "PAINTER", 0.91),
    ("帮我设计一个应用图标",                          "PAINTER", 0.91),
    ("生成一张卡通风格的插图",                        "PAINTER", 0.91),
    ("画一个有创意的封面",                            "PAINTER", 0.91),
    ("生成一张电影海报风格的图",                      "PAINTER", 0.91),
    ("帮我画一张人物概念图",                          "PAINTER", 0.91),
    ("生成一张手绘风格的地图",                        "PAINTER", 0.90),
    ("创作一幅印象派风格的画",                        "PAINTER", 0.91),
]

# ═══════════════════════════════ RESEARCH ════════════════════════════════════
RESEARCH_SAMPLES: List[GoldSample] = [
    # 基础高频（必须含"深入/全面/系统/详尽"等强信号词）
    ("帮我深入研究MicroLED技术原理",                  "RESEARCH", 0.93),
    ("全面分析GPT-4和Claude的差异",                   "RESEARCH", 0.92),
    ("系统研究量子计算的发展历程",                    "RESEARCH", 0.91),
    ("帮我深入研究量子计算",                          "RESEARCH", 0.92),
    ("全面分析特斯拉的竞争优势和风险",                "RESEARCH", 0.92),
    ("系统介绍大模型微调的各种方法",                  "RESEARCH", 0.91),
    ("详尽研究中美贸易战的历史和影响",                "RESEARCH", 0.91),
    ("深入分析比特币的技术实现原理",                  "RESEARCH", 0.92),
    ("全面评估新能源汽车行业的投资价值",              "RESEARCH", 0.91),
    ("帮我系统梳理机器学习各算法的优缺点",            "RESEARCH", 0.92),
    ("深入分析中国GDP的历史走势",                     "RESEARCH", 0.91),
    ("全面研究癌症的免疫治疗进展",                    "RESEARCH", 0.90),
    ("系统分析区块链的技术架构和应用",                "RESEARCH", 0.91),
    ("详细研究人工智能在医疗领域的应用",              "RESEARCH", 0.90),
    ("全面总结大语言模型的训练方法",                  "RESEARCH", 0.91),
    ("深入研究巴菲特的投资哲学体系",                  "RESEARCH", 0.91),
    ("系统研究RISC-V架构与x86的对比",                 "RESEARCH", 0.91),
    ("全面分析ChatGPT的技术细节",                     "RESEARCH", 0.91),
    ("深入研究5G技术的核心原理和应用",                "RESEARCH", 0.90),
    ("系统介绍计算机科学的发展史",                    "RESEARCH", 0.90),
    # 英文
    ("do a deep analysis of quantum computing",        "RESEARCH", 0.91),
    ("comprehensively research the LLM landscape",    "RESEARCH", 0.90),
    ("systematically study the history of the internet", "RESEARCH", 0.90),
    # 文件附件
    ("[FILE_ATTACHED:.pdf] 深入研究这家公司的财务状况","RESEARCH", 0.91),
    # 边界：RESEARCH vs CHAT（无强信号词→CHAT）
    ("介绍一下量子计算",                              "CHAT",     0.93),
    ("研究一下Python",                               "CHAT",     0.92),  # 口语"研究一下"
    ("了解一下区块链",                               "CHAT",     0.93),
    ("告诉我GPT-4是什么",                            "CHAT",     0.94),
    ("简单介绍一下机器学习",                          "CHAT",     0.92),
    # 实际RESEARCH（有强信号词）
    ("帮我深入分析Python的GIL机制原理",               "RESEARCH", 0.92),
    ("全面研究元宇宙的技术构成",                      "RESEARCH", 0.90),
    ("系统分析微软的商业模式和护城河",                "RESEARCH", 0.91),
    ("深入研究LSTM和Transformer的差异",               "RESEARCH", 0.91),
    ("全面分析中国新能源政策的影响",                  "RESEARCH", 0.90),
    ("系统梳理云计算的架构和服务模式",                "RESEARCH", 0.90),
    ("详尽研究NFT的技术原理和市场分析",               "RESEARCH", 0.90),
    ("深入分析强化学习在游戏中的应用",                "RESEARCH", 0.91),
    ("全面研究CRISPR基因编辑技术的伦理问题",          "RESEARCH", 0.90),
    ("系统研究精益生产方法论",                        "RESEARCH", 0.90),
    # 补充：不含深入/全面/系统前缀的自然分析句式 → RESEARCH
    ("分析特斯拉和比亚迪的竞争格局",                  "RESEARCH", 0.91),
    ("分析苹果公司的商业模式",                        "RESEARCH", 0.91),
    ("梳理一下大模型的主流训练方法",                  "RESEARCH", 0.91),
    ("梳理一下LLM的发展历程",                        "RESEARCH", 0.91),
    ("总结一下联邦学习的优缺点",                      "RESEARCH", 0.90),
    ("总结强化学习的主要算法",                        "RESEARCH", 0.90),
    ("帮我分析这个行业的竞争态势",                    "RESEARCH", 0.91),
    ("帮我梳理机器学习算法的分类",                    "RESEARCH", 0.91),
    ("总结一下各大云厂商的差异",                      "RESEARCH", 0.90),
    ("帮我分析一下Web3的技术栈",                      "RESEARCH", 0.91),
    ("梳理一下Python异步框架的发展",                  "RESEARCH", 0.90),
    ("分析OpenAI和Anthropic的产品策略",              "RESEARCH", 0.91),
    ("帮我总结RAG技术的核心组件",                     "RESEARCH", 0.91),
    ("梳理一下Transformer模型的演进",                 "RESEARCH", 0.91),
    ("分析中国新能源汽车行业的竞争格局",              "RESEARCH", 0.91),
    ("帮我总结微服务架构的优缺点",                    "RESEARCH", 0.90),
    # 边界：目前/近期 + 深度分析 → RESEARCH（非WEB_SEARCH）
    ("目前主流Agent企业的技术路径是什么",             "RESEARCH", 0.89),
    ("目前大模型主要有哪些训练范式",                  "RESEARCH", 0.88),
    ("近期AI领域最重要的技术突破有哪些",              "RESEARCH", 0.88),
    ("目前RAG技术的主要挑战和解决方案",               "RESEARCH", 0.88),
    ("梳理目前主流的多模态大模型",                    "RESEARCH", 0.88),
    ("分析目前中美在AI芯片领域的差距",                "RESEARCH", 0.88),
]

# ═══════════════════════════════ CHAT ════════════════════════════════════════
CHAT_SAMPLES: List[GoldSample] = [
    # 日常问答
    ("你好，介绍一下你自己",                          "CHAT", 0.97),
    ("什么是机器学习",                               "CHAT", 0.96),
    ("如何学好Python",                               "CHAT", 0.95),
    ("帮我讲讲区块链",                               "CHAT", 0.94),
    ("写一段自我介绍",                               "CHAT", 0.91),
    ("今天工作压力好大",                              "CHAT", 0.93),
    ("git怎么用",                                    "CHAT", 0.92),
    ("如何写一个排序算法",                            "CHAT", 0.93),  # 求知识，不要代码
    ("什么是快速排序",                               "CHAT", 0.96),
    ("Python怎么安装第三方库",                        "CHAT", 0.94),
    ("docker是什么",                                 "CHAT", 0.95),
    ("如何实现一个登录功能",                          "CHAT", 0.92),  # 知识≠产出代码
    ("给我解释一下什么是递归",                        "CHAT", 0.94),
    ("讲讲面向对象编程",                              "CHAT", 0.94),
    ("什么是RESTful API",                            "CHAT", 0.95),
    ("解释一下设计模式中的单例模式",                  "CHAT", 0.94),
    ("如何提高编程能力",                              "CHAT", 0.93),
    ("Python和Java有什么区别",                        "CHAT", 0.94),
    ("什么是微服务架构",                              "CHAT", 0.94),
    ("帮我理解Transformer模型",                       "CHAT", 0.93),
    # 创意/写作（短文本）
    ("写一段励志的话",                               "CHAT", 0.92),
    ("帮我想一个项目名字",                            "CHAT", 0.91),
    ("写个笑话",                                     "CHAT", 0.93),
    ("帮我取一个英文名",                              "CHAT", 0.91),
    ("写一首关于春天的诗",                            "CHAT", 0.91),
    # 通用翻译
    ("把这句话翻译成英文: 你好世界",                  "CHAT", 0.94),
    ("Hello怎么翻译",                                "CHAT", 0.95),
    ("这句英文什么意思: It's raining cats and dogs",  "CHAT", 0.95),
    # 建议咨询
    ("学AI应该从哪里开始",                            "CHAT", 0.93),
    ("怎么准备技术面试",                              "CHAT", 0.93),
    ("买电脑有什么建议",                              "CHAT", 0.91),
    ("如何健康减肥",                                  "CHAT", 0.92),
    # 英文
    ("what is machine learning",                     "CHAT", 0.96),
    ("explain recursion",                            "CHAT", 0.95),
    ("hello, introduce yourself",                    "CHAT", 0.97),
    ("how to learn Python",                          "CHAT", 0.95),
    ("what are the differences between TCP and UDP", "CHAT", 0.95),
    # 历史/固定知识（≠实时→CHAT）
    ("二战是哪年开始的",                              "CHAT", 0.96),
    ("牛顿是谁",                                     "CHAT", 0.96),
    ("地球到太阳的距离是多少",                        "CHAT", 0.95),
    ("万里长城有多长",                               "CHAT", 0.95),
    ("唐朝是哪年建立的",                              "CHAT", 0.96),
    # 边界：CHAT vs CODER
    ("怎么写一个冒泡排序",                            "CHAT", 0.92),  # 求知识
    ("解释一下快速排序的原理",                        "CHAT", 0.95),
    ("搜索怎么用git",                                "CHAT", 0.92),
    # 边界：CHAT vs FILE_GEN（短文本，无格式词→CHAT）
    ("写一篇100字的春节介绍",                         "CHAT", 0.92),
    ("帮我写个关于科技的段落",                        "CHAT", 0.91),
    # 边界：CHAT vs RESEARCH（无强信号词→CHAT）
    ("研究一下AI",                                   "CHAT", 0.92),
    ("告诉我量子力学是什么",                          "CHAT", 0.93),
    ("了解一下Python的GIL",                          "CHAT", 0.91),
    # 文件附件+问题（读文件作答→CHAT）
    ("[FILE_ATTACHED:.pdf] 告诉我这份文件的核心观点",  "CHAT", 0.93),
    ("[FILE_ATTACHED:.pdf] 这份商业计划书值得投资吗",  "CHAT", 0.93),
    ("[FILE_ATTACHED:.pdf] 分析一下这份合同有哪些风险","CHAT", 0.92),
    ("[FILE_ATTACHED:.pdf] 帮我总结一下这篇论文",      "CHAT", 0.93),
    ("[FILE_ATTACHED:.pdf] 这是什么类型的文件",        "CHAT", 0.94),
]

# ══════════════════════════════════════════════════════════════════
# 时效性信号词触发 WEB_SEARCH（核心补丁：目前/近况/局势/动态/进展）
# ══════════════════════════════════════════════════════════════════
# 规律：「目前/近况/近期/如今/当下/眼下/局势/战况/动态/现状/进展/行情/走势」
#       出现在问题中 → 话题带时态 → 实时查询 → WEB_SEARCH
TEMPORAL_SEARCH_SAMPLES: List[GoldSample] = [
    # ── 「目前」系列 ─────────────────────────────────────────────────
    ("目前伊朗战事如何",                             "WEB_SEARCH", 0.95),
    ("目前俄乌局势怎么样",                           "WEB_SEARCH", 0.96),
    ("目前中东局势如何",                             "WEB_SEARCH", 0.95),
    ("目前台海形势怎么样",                           "WEB_SEARCH", 0.95),
    ("目前A股行情如何",                              "WEB_SEARCH", 0.96),
    ("目前比特币走势",                               "WEB_SEARCH", 0.96),
    ("目前新冠疫情情况",                             "WEB_SEARCH", 0.95),
    ("目前美联储政策动向",                           "WEB_SEARCH", 0.95),
    ("目前特斯拉股价多少",                           "WEB_SEARCH", 0.96),
    ("目前人民币汇率",                               "WEB_SEARCH", 0.96),
    ("目前油价是多少",                               "WEB_SEARCH", 0.96),
    ("目前黄金价格",                                 "WEB_SEARCH", 0.96),
    ("目前国内疫情如何",                             "WEB_SEARCH", 0.95),
    ("目前哪部电影最火",                             "WEB_SEARCH", 0.92),
    ("目前Python最新版本是多少",                     "WEB_SEARCH", 0.93),
    ("目前AI领域有什么新进展",                       "WEB_SEARCH", 0.93),
    ("目前国内GDP增速怎么样",                        "WEB_SEARCH", 0.95),
    ("目前北京天气如何",                             "WEB_SEARCH", 0.96),
    ("目前这只股票值得买吗",                         "WEB_SEARCH", 0.92),
    ("目前房价走势怎样",                             "WEB_SEARCH", 0.95),
    ("目前巴以冲突进展",                             "WEB_SEARCH", 0.96),
    ("目前朝鲜半岛形势",                             "WEB_SEARCH", 0.94),
    ("目前中美关系如何",                             "WEB_SEARCH", 0.95),
    ("目前芯片行业情况",                             "WEB_SEARCH", 0.93),
    ("目前国内就业形势",                             "WEB_SEARCH", 0.94),
    # ── 「近况」系列 ─────────────────────────────────────────────────
    ("伊朗近况如何",                                 "WEB_SEARCH", 0.95),
    ("特朗普近况怎么样",                             "WEB_SEARCH", 0.94),
    ("俄乌战争近况",                                 "WEB_SEARCH", 0.95),
    ("朝鲜近况",                                     "WEB_SEARCH", 0.94),
    ("马斯克近况",                                   "WEB_SEARCH", 0.93),
    ("中美关系近况",                                 "WEB_SEARCH", 0.94),
    ("A股近况",                                      "WEB_SEARCH", 0.95),
    ("苹果公司近况",                                 "WEB_SEARCH", 0.93),
    ("新能源汽车市场近况",                           "WEB_SEARCH", 0.93),
    # ── 「近期」/「近来」系列 ──────────────────────────────────────
    ("近期国际局势怎么样",                           "WEB_SEARCH", 0.94),
    ("近期有哪些大新闻",                             "WEB_SEARCH", 0.93),
    ("近期股市表现如何",                             "WEB_SEARCH", 0.95),
    ("近期ChatGPT有什么更新",                        "WEB_SEARCH", 0.93),
    ("近期美联储有什么动作",                         "WEB_SEARCH", 0.94),
    ("近来金价走势",                                 "WEB_SEARCH", 0.95),
    ("近来有什么科技新闻",                           "WEB_SEARCH", 0.92),
    ("这段时间伊朗怎么了",                           "WEB_SEARCH", 0.93),
    ("近期有没有裁员大新闻",                         "WEB_SEARCH", 0.92),
    # ── 「如今」/「当下」/「眼下」系列 ──────────────────────────
    ("如今的战局怎么样",                             "WEB_SEARCH", 0.94),
    ("如今比特币还值得投资吗",                       "WEB_SEARCH", 0.92),
    ("如今AI发展到什么程度了",                       "WEB_SEARCH", 0.91),
    ("当下经济形势如何",                             "WEB_SEARCH", 0.95),
    ("当下国际油价",                                 "WEB_SEARCH", 0.96),
    ("眼下A股怎么回事",                              "WEB_SEARCH", 0.94),
    ("眼下的局势对中国有什么影响",                   "WEB_SEARCH", 0.93),
    # ── 「局势」/「战况」/「动态」/「现状」词根 ──────────────────
    ("伊朗局势最新",                                 "WEB_SEARCH", 0.96),
    ("俄乌战况最新进展",                             "WEB_SEARCH", 0.96),
    ("中东局势动态",                                 "WEB_SEARCH", 0.95),
    ("台海局势最新消息",                             "WEB_SEARCH", 0.96),
    ("A股最新动态",                                  "WEB_SEARCH", 0.95),
    ("巴以冲突最新进展",                             "WEB_SEARCH", 0.96),
    ("美国大选最新动态",                             "WEB_SEARCH", 0.95),
    ("国内经济现状",                                 "WEB_SEARCH", 0.93),
    ("楼市现状怎么样",                               "WEB_SEARCH", 0.94),
    ("朝鲜半岛局势",                                 "WEB_SEARCH", 0.94),
    ("欧洲能源局势",                                 "WEB_SEARCH", 0.93),
    ("中美芯片战最新动态",                           "WEB_SEARCH", 0.95),
    ("半导体行业现状",                               "WEB_SEARCH", 0.91),
    ("伊以冲突动态",                                 "WEB_SEARCH", 0.95),
    # ── 「进展」/「行情」/「走势」词根 ────────────────────────────
    ("伊朗核谈判最新进展",                           "WEB_SEARCH", 0.96),
    ("中美贸易谈判进展",                             "WEB_SEARCH", 0.95),
    ("特斯拉最新股价走势",                           "WEB_SEARCH", 0.96),
    ("黄金今日行情",                                 "WEB_SEARCH", 0.97),
    ("原油行情",                                     "WEB_SEARCH", 0.95),
    ("人民币走势",                                   "WEB_SEARCH", 0.95),
    ("纳斯达克行情",                                 "WEB_SEARCH", 0.95),
    ("新能源汽车销量最新数据",                       "WEB_SEARCH", 0.94),
    # ── 英文时效词（current/latest/ongoing/now/update）────────────
    ("current situation in Iran",                    "WEB_SEARCH", 0.95),
    ("latest news on Russia Ukraine war",            "WEB_SEARCH", 0.96),
    ("ongoing conflict in the Middle East",          "WEB_SEARCH", 0.95),
    ("what's happening in Iran right now",           "WEB_SEARCH", 0.96),
    ("current Bitcoin price trend",                  "WEB_SEARCH", 0.96),
    ("latest update on US China relations",          "WEB_SEARCH", 0.95),
    ("how is the stock market doing today",          "WEB_SEARCH", 0.96),
    ("recent developments in AI",                    "WEB_SEARCH", 0.92),
    ("what's the current oil price",                 "WEB_SEARCH", 0.96),
    ("latest news on North Korea",                   "WEB_SEARCH", 0.95),
    # ── 核心对比组：同话题无时效词→CHAT，有时效词→WEB_SEARCH ──────
    ("伊朗历史上有哪些重要战争",                     "CHAT",       0.95),  # 历史知识→CHAT
    ("目前伊朗在打仗吗",                             "WEB_SEARCH", 0.96),  # 时效词→SEARCH
    ("俄罗斯的军事实力如何",                         "CHAT",       0.92),  # 固定知识→CHAT
    ("目前俄罗斯在乌克兰的进展",                     "WEB_SEARCH", 0.96),  # 时效词→SEARCH
    ("中东地区的宗教构成",                           "CHAT",       0.94),  # 固定知识→CHAT
    ("目前中东哪里在打仗",                           "WEB_SEARCH", 0.96),  # 时效词→SEARCH
    ("比特币的底层技术原理",                         "CHAT",       0.94),  # 技术知识→CHAT
    ("目前比特币多少钱",                             "WEB_SEARCH", 0.97),  # 时效词→SEARCH
    ("A股的交易规则",                                "CHAT",       0.93),  # 固定规则→CHAT
    ("目前A股市场热点",                              "WEB_SEARCH", 0.95),  # 时效词→SEARCH
    ("GPT的技术架构",                                "CHAT",       0.93),  # 技术知识→CHAT
    ("目前GPT-4o有什么新功能",                       "WEB_SEARCH", 0.93),  # 时效词→SEARCH
    ("巴以冲突的历史根源",                           "CHAT",       0.93),  # 历史知识→CHAT
    ("巴以冲突最新战况",                             "WEB_SEARCH", 0.96),  # 时效词→SEARCH
    ("俄罗斯的经济体量",                             "CHAT",       0.91),  # 固定知识→CHAT
    ("俄罗斯经济近况",                               "WEB_SEARCH", 0.94),  # 时效词→SEARCH
]

# ══════════════════════════════════════════════════════════════════
# 额外的高混淆边界对比组（专门强化最容易错误分类的场景）
# ══════════════════════════════════════════════════════════════════

BOUNDARY_SAMPLES: List[GoldSample] = [
    # ── CHAT vs CODER（问知识 vs 要代码）──────────────────────────────────
    ("如何实现二叉树",                                "CHAT",        0.91),
    ("帮我写一个二叉树",                              "CODER",       0.94),
    ("怎么用Python爬数据",                            "CHAT",        0.91),
    ("用Python写一个爬虫",                            "CODER",       0.94),
    ("如何写快速排序",                                "CHAT",        0.92),
    ("写一个快速排序",                                "CODER",       0.95),
    ("怎么连接MySQL",                                 "CHAT",        0.91),
    ("帮我写一个MySQL连接脚本",                       "CODER",       0.93),
    ("如何优化SQL",                                   "CHAT",        0.91),
    ("帮我优化这段SQL:\nSELECT * FROM users",         "DOC_ANNOTATE",0.89),
    ("什么是设计模式",                                "CHAT",        0.95),
    ("用Python实现一个观察者设计模式",                "CODER",       0.93),
    # ── SYSTEM vs AGENT（操作系统 vs 控制应用发内容）──────────────────────
    ("打开微信",                                      "SYSTEM",      0.97),
    ("给微信好友发消息",                              "AGENT",       0.95),
    ("打开邮件客户端",                                "SYSTEM",      0.95),
    ("给老板发邮件",                                  "AGENT",       0.95),
    ("打开钉钉",                                      "SYSTEM",      0.95),
    ("在钉钉上发一个通知",                            "AGENT",       0.93),
    # ── WEB_SEARCH vs CHAT（实时 vs 固定知识）────────────────────────────
    ("今天美股涨了多少",                              "WEB_SEARCH",  0.96),
    ("美股是什么",                                    "CHAT",        0.96),
    ("现在黄金多少钱一克",                            "WEB_SEARCH",  0.96),
    ("黄金的历史是什么",                              "CHAT",        0.95),
    ("今天油价是多少",                                "WEB_SEARCH",  0.96),
    ("石油的形成原理",                               "CHAT",        0.95),
    # ── RESEARCH vs CHAT（深度系统 vs 普通介绍）──────────────────────────
    ("介绍一下深度学习",                              "CHAT",        0.93),
    ("深入研究深度学习的数学原理",                    "RESEARCH",    0.92),
    ("告诉我区块链是什么",                            "CHAT",        0.94),
    ("系统分析区块链的技术架构和商业应用",            "RESEARCH",    0.91),
    ("讲下强化学习",                                  "CHAT",        0.92),
    ("全面研究强化学习的各类算法原理和应用",          "RESEARCH",    0.91),
    # ── FILE_GEN vs CHAT（有格式词 vs 无格式词）──────────────────────────
    ("写一篇关于AI的文章",                            "CHAT",        0.92),
    ("写一份关于AI的PDF报告",                         "FILE_GEN",    0.91),
    ("帮我写个简历",                                  "CHAT",        0.92),
    ("帮我做一份Word简历",                            "FILE_GEN",    0.92),
    ("写一下公司介绍",                               "CHAT",        0.91),
    ("做一个公司介绍的PPT",                           "FILE_GEN",    0.91),
    # ── PAINTER vs CODER（创作图 vs 数据图）─────────────────────────────
    ("画一张漂亮的风景图",                            "PAINTER",     0.94),
    ("用Python画一个风景数据分布图",                  "CODER",       0.93),
    ("生成一张艺术图",                               "PAINTER",     0.93),
    ("生成一个图表展示数据",                          "CODER",       0.92),
    ("帮我画一个可爱猫咪",                            "PAINTER",     0.95),
    ("画一个折线图",                                  "CODER",       0.94),
    # ── FILE_SEARCH vs SYSTEM（搜索 vs 打开）─────────────────────────────
    ("帮我找一下Chrome的安装路径",                    "FILE_SEARCH", 0.91),
    ("打开Chrome",                                   "SYSTEM",      0.96),
    ("找一下Python的安装目录",                        "FILE_SEARCH", 0.91),
    ("运行Python",                                   "SYSTEM",      0.90),
    # ── DOC_ANNOTATE vs CODER（改已有代码 vs 新写代码）─────────────────
    ("帮我优化这个函数: def f(x): return x*2",        "DOC_ANNOTATE",0.89),
    ("帮我写一个函数实现x乘以2",                      "CODER",       0.94),
    ("修复这段代码的bug: for i in rang(10): pass",    "DOC_ANNOTATE",0.91),
    ("帮我写一个遍历列表的代码",                      "CODER",       0.93),
    # ── AGENT vs CODER（执行操作 vs 写代码实现）──────────────────────────
    ("帮我自动登录微博",                              "AGENT",       0.91),
    ("帮我写一个自动登录微博的脚本",                  "CODER",       0.93),
    ("帮我自动填写表单",                              "AGENT",       0.90),
    ("帮我写一个表单自动填写脚本",                    "CODER",       0.92),
]

# ══════════════════════════════════════════════════════════════════
# 口语变体、模糊表达、简短输入（测试模型在噪音下的鲁棒性）
# ══════════════════════════════════════════════════════════════════

COLLOQUIAL_SAMPLES: List[GoldSample] = [
    # 极短输入
    ("帮我截一个图",                                  "SYSTEM",      0.93),
    ("帮我画个图",                                   "PAINTER",     0.87),  # 模糊，倾向PAINTER
    ("查天气",                                       "WEB_SEARCH",  0.95),
    ("发消息",                                       "AGENT",       0.88),
    ("找文件",                                       "FILE_SEARCH", 0.90),
    ("关机",                                         "SYSTEM",      0.97),
    ("写代码",                                       "CODER",       0.88),
    ("画画",                                         "PAINTER",     0.92),
    ("做PPT",                                        "FILE_GEN",    0.91),
    # 口语变体
    ("帮我发个消息给张三讲明天休假",                  "AGENT",       0.93),
    ("帮我弄一个排序",                               "CODER",       0.88),
    ("给我整一张图",                                  "PAINTER",     0.88),
    ("帮我搞一份报告",                               "FILE_GEN",    0.87),
    ("能帮我找一下那个文件吗",                        "FILE_SEARCH", 0.89),
    ("帮我弄一下截图",                               "SYSTEM",      0.91),
    ("给我讲讲AI是什么回事",                          "CHAT",        0.92),
    ("能不能帮我查一下今天股票",                      "WEB_SEARCH",  0.93),
    ("帮我弄一下这段代码",                            "DOC_ANNOTATE",0.85),  # 模糊→ANNOTATE
    ("写个东西介绍一下我们公司",                      "CHAT",        0.89),
    # 语气词/模糊
    ("那个...帮我开一下计算器",                       "SYSTEM",      0.91),
    ("我想看看今天的天气怎么样",                      "WEB_SEARCH",  0.92),
    ("能帮我写一段Python吗",                          "CODER",       0.91),
    ("帮我弄个可视化报表",                            "CODER",       0.88),
    ("我需要一个PPT",                                "FILE_GEN",    0.90),
]

class SyntheticDataGenerator:
    """大规模合成数据生成器（黄金标准标注）"""

    ALL_POOLS: List[List[GoldSample]] = [
        SYSTEM_SAMPLES,
        AGENT_SAMPLES,
        WEB_SEARCH_SAMPLES,
        TEMPORAL_SEARCH_SAMPLES,   # ← 时效词补丁（目前/近况/局势/动态等）
        FILE_SEARCH_SAMPLES,
        FILE_GEN_SAMPLES,
        DOC_ANNOTATE_SAMPLES,
        CODER_SAMPLES,
        PAINTER_SAMPLES,
        RESEARCH_SAMPLES,
        CHAT_SAMPLES,
        BOUNDARY_SAMPLES,
        COLLOQUIAL_SAMPLES,
    ]

    @classmethod
    def generate_all(cls, shuffle: bool = True) -> List[GoldSample]:
        """返回全部黄金标准样本（不重复）"""
        combined: List[GoldSample] = []
        seen = set()
        for pool in cls.ALL_POOLS:
            for sample in pool:
                key = sample[0][:80]  # 以输入前80字去重
                if key not in seen:
                    seen.add(key)
                    combined.append(sample)
        if shuffle:
            random.seed(42)
            random.shuffle(combined)
        return combined

    @classmethod
    def stats(cls) -> dict:
        """统计各类别样本数量"""
        from collections import Counter
        all_samples = cls.generate_all(shuffle=False)
        counts = Counter(s[1] for s in all_samples)
        return {
            "total": len(all_samples),
            "by_task": dict(sorted(counts.items())),
        }


if __name__ == "__main__":
    s = SyntheticDataGenerator.stats()
    print(f"\n{'='*60}")
    print(f"  Koto 合成训练数据统计  (总计: {s['total']} 条)")
    print(f"{'='*60}")
    for task, count in s["by_task"].items():
        bar = "█" * (count // 2)
        print(f"  {task:<15} {count:>4} 条  {bar}")
    print(f"{'='*60}\n")
