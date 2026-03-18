#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
浏览器自动化模块
支持 Selenium WebDriver，可自动化浏览器操作
"""

import logging
import os
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    _SELENIUM_AVAILABLE = True
except ImportError:
    _SELENIUM_AVAILABLE = False


class BrowserAutomation:
    """浏览器自动化控制器"""

    def __init__(self, headless: bool = False):
        """
        初始化浏览器自动化

        Args:
            headless: 是否使用无头模式 (不显示浏览器窗口)
        """
        self.driver = None
        self.headless = headless
        self._init_driver()

    def _init_driver(self):
        """初始化 WebDriver"""
        try:
            options = Options()

            if self.headless:
                options.add_argument("--headless")

            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")

            # 尝试使用系统 Chrome
            self.driver = webdriver.Chrome(options=options)
            logger.info("[浏览器] WebDriver 已初始化")

        except Exception as e:
            logger.info(f"[浏览器] WebDriver 初始化失败: {e}")
            logger.info("[浏览器] 请确保已安装 Chrome 和 ChromeDriver")

    def open_url(self, url: str, wait_time: int = 3) -> bool:
        """
        打开 URL

        Args:
            url: 网址
            wait_time: 等待加载时间 (秒)
        """
        if not self.driver:
            return False

        try:
            self.driver.get(url)
            time.sleep(wait_time)
            logger.info(f"[浏览器] 已打开: {url}")
            return True
        except Exception as e:
            logger.info(f"[浏览器] 打开 URL 失败: {e}")
            return False

    def find_element(self, selector: str, by: str = "css") -> Optional[any]:
        """
        查找页面元素

        Args:
            selector: 选择器
            by: 查找方式 ('css', 'xpath', 'id', 'class', 'tag')
        """
        if not self.driver:
            return None

        try:
            by_map = {
                "css": By.CSS_SELECTOR,
                "xpath": By.XPATH,
                "id": By.ID,
                "class": By.CLASS_NAME,
                "tag": By.TAG_NAME,
            }

            element = self.driver.find_element(
                by_map.get(by, By.CSS_SELECTOR), selector
            )
            return element
        except Exception as e:
            logger.info(f"[浏览器] 元素查找失败: {e}")
            return None

    def find_elements(self, selector: str, by: str = "css") -> List:
        """
        查找多个页面元素

        Args:
            selector: 选择器
            by: 查找方式
        """
        if not self.driver:
            return []

        try:
            by_map = {
                "css": By.CSS_SELECTOR,
                "xpath": By.XPATH,
                "id": By.ID,
                "class": By.CLASS_NAME,
                "tag": By.TAG_NAME,
            }

            elements = self.driver.find_elements(
                by_map.get(by, By.CSS_SELECTOR), selector
            )
            return elements
        except Exception as e:
            logger.info(f"[浏览器] 元素查找失败: {e}")
            return []

    def click(self, selector: str, by: str = "css") -> bool:
        """点击元素"""
        element = self.find_element(selector, by)
        if element:
            try:
                element.click()
                logger.info(f"[浏览器] 已点击: {selector}")
                return True
            except Exception as e:
                logger.info(f"[浏览器] 点击失败: {e}")
        return False

    def input_text(
        self, selector: str, text: str, by: str = "css", clear_first: bool = True
    ) -> bool:
        """
        输入文本

        Args:
            selector: 选择器
            text: 要输入的文本
            by: 查找方式
            clear_first: 是否先清空输入框
        """
        element = self.find_element(selector, by)
        if element:
            try:
                if clear_first:
                    element.clear()
                element.send_keys(text)
                logger.info(f"[浏览器] 已输入文本: {selector}")
                return True
            except Exception as e:
                logger.info(f"[浏览器] 输入文本失败: {e}")
        return False

    def get_text(self, selector: str, by: str = "css") -> Optional[str]:
        """获取元素文本"""
        element = self.find_element(selector, by)
        if element:
            return element.text
        return None

    def get_attribute(
        self, selector: str, attribute: str, by: str = "css"
    ) -> Optional[str]:
        """获取元素属性"""
        element = self.find_element(selector, by)
        if element:
            return element.get_attribute(attribute)
        return None

    def wait_for_element(
        self, selector: str, by: str = "css", timeout: int = 10
    ) -> Optional[any]:
        """
        等待元素出现

        Args:
            selector: 选择器
            by: 查找方式
            timeout: 超时时间 (秒)
        """
        if not self.driver:
            return None

        try:
            by_map = {
                "css": By.CSS_SELECTOR,
                "xpath": By.XPATH,
                "id": By.ID,
                "class": By.CLASS_NAME,
                "tag": By.TAG_NAME,
            }

            element = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located(
                    (by_map.get(by, By.CSS_SELECTOR), selector)
                )
            )
            return element
        except Exception as e:
            logger.info(f"[浏览器] 等待元素超时: {e}")
            return None

    def execute_script(self, script: str) -> any:
        """
        执行 JavaScript 代码

        Args:
            script: JavaScript 代码
        """
        if not self.driver:
            return None

        try:
            result = self.driver.execute_script(script)
            return result
        except Exception as e:
            logger.info(f"[浏览器] 脚本执行失败: {e}")
            return None

    def scroll_to_bottom(self):
        """滚动到页面底部"""
        self.execute_script("window.scrollTo(0, document.body.scrollHeight);")

    def scroll_to_top(self):
        """滚动到页面顶部"""
        self.execute_script("window.scrollTo(0, 0);")

    def take_screenshot(self, file_path: str) -> bool:
        """
        截图

        Args:
            file_path: 保存路径
        """
        if not self.driver:
            return False

        try:
            self.driver.save_screenshot(file_path)
            logger.info(f"[浏览器] 截图已保存: {file_path}")
            return True
        except Exception as e:
            logger.info(f"[浏览器] 截图失败: {e}")
            return False

    def get_page_source(self) -> Optional[str]:
        """获取页面源代码"""
        if self.driver:
            return self.driver.page_source
        return None

    def get_current_url(self) -> Optional[str]:
        """获取当前 URL"""
        if self.driver:
            return self.driver.current_url
        return None

    def back(self):
        """后退"""
        if self.driver:
            self.driver.back()

    def forward(self):
        """前进"""
        if self.driver:
            self.driver.forward()

    def refresh(self):
        """刷新页面"""
        if self.driver:
            self.driver.refresh()

    def close(self):
        """关闭当前窗口"""
        if self.driver:
            self.driver.close()

    def quit(self):
        """退出浏览器"""
        if self.driver:
            self.driver.quit()
            self.driver = None
            logger.info("[浏览器] 已退出")

    # === 高级功能 ===

    def search_google(self, query: str) -> List[Dict]:
        """
        在 Google 上搜索

        Args:
            query: 搜索关键词

        Returns:
            搜索结果列表 [{'title': ..., 'url': ..., 'snippet': ...}, ...]
        """
        try:
            self.open_url(f"https://www.google.com/search?q={query}")
            time.sleep(2)

            results = []

            # 查找搜索结果
            search_results = self.find_elements("div.g", "css")

            for result in search_results[:10]:
                try:
                    title_elem = result.find_element(By.CSS_SELECTOR, "h3")
                    link_elem = result.find_element(By.CSS_SELECTOR, "a")

                    title = title_elem.text if title_elem else ""
                    url = link_elem.get_attribute("href") if link_elem else ""

                    if title and url:
                        results.append({"title": title, "url": url})
                except Exception as e:
                    logger.debug("Failed to extract search result element: %s", e)
                    continue

            logger.info(f"[浏览器] Google 搜索完成: {len(results)} 个结果")
            return results

        except Exception as e:
            logger.info(f"[浏览器] Google 搜索失败: {e}")
            return []

    def fill_form(self, form_data: Dict[str, str]):
        """
        填写表单

        Args:
            form_data: 表单数据 {selector: value, ...}
        """
        for selector, value in form_data.items():
            self.input_text(selector, value)
            time.sleep(0.5)


# 全局实例
_browser_automation = None


def get_browser_automation(headless: bool = False) -> "BrowserAutomation":
    """获取全局浏览器自动化单例。若 selenium 未安装则抛出 RuntimeError。"""
    if not _SELENIUM_AVAILABLE:
        raise RuntimeError(
            "浏览器自动化功能不可用：请先安装 selenium 包（pip install selenium）"
        )
    global _browser_automation
    if _browser_automation is None:
        _browser_automation = BrowserAutomation(headless=headless)
    return _browser_automation
