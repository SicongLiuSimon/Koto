#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
代码生成器 - 场景2：帮助用户完成不会的编程任务
支持多种编程语言、框架、代码模板生成
"""

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CodeTemplate:
    """代码模板"""

    def __init__(self, name: str, language: str, template: str, description: str = ""):
        self.name = name
        self.language = language
        self.template = template
        self.description = description

    def render(self, **kwargs) -> str:
        """渲染模板"""
        return self.template.format(**kwargs)


class CodeGenerator:
    """智能代码生成器"""

    def __init__(self):
        self.templates = self._init_templates()

    def _init_templates(self) -> Dict[str, CodeTemplate]:
        """初始化代码模板库"""
        templates = {}

        # C语言模板
        templates["c_hello"] = CodeTemplate(
            name="c_hello",
            language="c",
            template="""#include <stdio.h>
#include <stdlib.h>

int main(int argc, char *argv[]) {{
    printf("Hello, World!\\n");
    return 0;
}}
""",
            description="C语言 Hello World 程序",
        )

        templates["c_file_io"] = CodeTemplate(
            name="c_file_io",
            language="c",
            template="""#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char *argv[]) {{
    FILE *fp;
    char buffer[1024];
    
    // 写入文件
    fp = fopen("{filename}", "w");
    if (fp == NULL) {{
        fprintf(stderr, "无法打开文件\\n");
        return 1;
    }}
    fprintf(fp, "{content}");
    fclose(fp);
    
    // 读取文件
    fp = fopen("{filename}", "r");
    if (fp == NULL) {{
        fprintf(stderr, "无法打开文件\\n");
        return 1;
    }}
    while (fgets(buffer, sizeof(buffer), fp) != NULL) {{
        printf("%s", buffer);
    }}
    fclose(fp);
    
    return 0;
}}
""",
            description="C语言文件读写操作",
        )

        templates["c_linked_list"] = CodeTemplate(
            name="c_linked_list",
            language="c",
            template="""#include <stdio.h>
#include <stdlib.h>

// 链表节点结构
typedef struct Node {{
    int data;
    struct Node* next;
}} Node;

// 创建新节点
Node* createNode(int data) {{
    Node* newNode = (Node*)malloc(sizeof(Node));
    newNode->data = data;
    newNode->next = NULL;
    return newNode;
}}

// 在链表末尾插入节点
void append(Node** head, int data) {{
    Node* newNode = createNode(data);
    if (*head == NULL) {{
        *head = newNode;
        return;
    }}
    Node* temp = *head;
    while (temp->next != NULL) {{
        temp = temp->next;
    }}
    temp->next = newNode;
}}

// 打印链表
void printList(Node* head) {{
    Node* temp = head;
    while (temp != NULL) {{
        printf("%d -> ", temp->data);
        temp = temp->next;
    }}
    printf("NULL\\n");
}}

// 释放链表内存
void freeList(Node* head) {{
    Node* temp;
    while (head != NULL) {{
        temp = head;
        head = head->next;
        free(temp);
    }}
}}

int main() {{
    Node* head = NULL;
    
    // 添加元素
    append(&head, 1);
    append(&head, 2);
    append(&head, 3);
    
    // 打印链表
    printList(head);
    
    // 释放内存
    freeList(head);
    
    return 0;
}}
""",
            description="C语言链表实现",
        )

        # Python模板
        templates["python_web_scraper"] = CodeTemplate(
            name="python_web_scraper",
            language="python",
            template="""#!/usr/bin/env python
# -*- coding: utf-8 -*-
\"\"\"网页爬虫 - {description}\"\"\"

import requests
from bs4 import BeautifulSoup
import json
from typing import List, Dict

def scrape_page(url: str) -> Dict:
    \"\"\"爬取网页数据\"\"\"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 提取数据
        data = {{
            'title': soup.find('title').text if soup.find('title') else '',
            'content': soup.get_text(),
            'links': [a.get('href') for a in soup.find_all('a', href=True)]
        }}
        
        return data
    except Exception as e:
        return {{'error': str(e)}}

if __name__ == '__main__':
    url = '{url}'
    result = scrape_page(url)
    logger.info(json.dumps(result, ensure_ascii=False, indent=2))
""",
            description="Python网页爬虫",
        )

        # JavaScript模板
        templates["js_api_client"] = CodeTemplate(
            name="js_api_client",
            language="javascript",
            template="""// API 客户端 - {description}

class APIClient {{
    constructor(baseURL) {{
        this.baseURL = baseURL;
    }}
    
    async get(endpoint) {{
        try {{
            const response = await fetch(`${{this.baseURL}}${{endpoint}}`);
            if (!response.ok) {{
                throw new Error(`HTTP error! status: ${{response.status}}`);
            }}
            return await response.json();
        }} catch (error) {{
            console.error('GET request failed:', error);
            throw error;
        }}
    }}
    
    async post(endpoint, data) {{
        try {{
            const response = await fetch(`${{this.baseURL}}${{endpoint}}`, {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json'
                }},
                body: JSON.stringify(data)
            }});
            if (!response.ok) {{
                throw new Error(`HTTP error! status: ${{response.status}}`);
            }}
            return await response.json();
        }} catch (error) {{
            console.error('POST request failed:', error);
            throw error;
        }}
    }}
}}

// 使用示例
const client = new APIClient('{api_base}');
client.get('{endpoint}')
    .then(data => console.log(data))
    .catch(error => console.error(error));
""",
            description="JavaScript API客户端",
        )

        return templates

    def generate(
        self, template_name: str, output_path: Optional[str] = None, **kwargs
    ) -> Dict[str, Any]:
        """
        生成代码

        Args:
            template_name: 模板名称
            output_path: 输出文件路径（可选）
            **kwargs: 模板参数

        Returns:
            生成结果
        """
        try:
            if template_name not in self.templates:
                return {"success": False, "error": f"模板不存在: {template_name}"}

            template = self.templates[template_name]
            code = template.render(**kwargs)

            result = {
                "success": True,
                "code": code,
                "language": template.language,
                "description": template.description,
            }

            # 如果指定了输出路径，保存文件
            if output_path:
                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(code)
                result["output_path"] = output_path

            return result

        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_templates(self, language: Optional[str] = None) -> List[Dict[str, str]]:
        """列出可用模板"""
        templates_info = []
        for name, template in self.templates.items():
            if language is None or template.language == language:
                templates_info.append(
                    {
                        "name": name,
                        "language": template.language,
                        "description": template.description,
                    }
                )
        return templates_info

    def generate_from_description(
        self, description: str, language: str = "python", ai_model: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        根据自然语言描述生成代码（需要AI模型）

        Args:
            description: 代码功能描述
            language: 编程语言
            ai_model: AI模型实例

        Returns:
            生成的代码
        """
        if ai_model is None:
            return {"success": False, "error": "需要提供AI模型实例"}

        # 构造提示词
        prompt = f"""请生成{language}代码来实现以下功能：

{description}

要求：
1. 代码要完整可运行
2. 包含必要的注释
3. 遵循最佳实践
4. 包含错误处理

请只返回代码，不要其他解释。"""

        try:
            # 这里应该调用AI模型生成代码
            # response = ai_model.generate(prompt)
            # code = response.text

            return {
                "success": True,
                "code": "# AI生成的代码将在这里显示",
                "language": language,
                "description": description,
                "note": "需要集成AI模型才能使用此功能",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


# ================= 测试 =================

if __name__ == "__main__":
    generator = CodeGenerator()

    # 列出所有模板
    logger.info("可用模板：")
    for template in generator.list_templates():
        logger.info(
            f"  - {template['name']} ({template['language']}): {template['description']}"
        )

    # 生成C语言链表代码
    result = generator.generate(
        "c_linked_list", output_path="workspace/code/linked_list.c"
    )

    if result["success"]:
        logger.info(f"\n✅ 代码已生成到: {result.get('output_path', '内存中')}")
        logger.info(f"语言: {result['language']}")
        logger.info(f"说明: {result['description']}")
    else:
        logger.error(f"\n❌ 生成失败: {result['error']}")
