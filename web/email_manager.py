#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
邮件集成模块
支持读取、发送、搜索邮件，支持 SMTP 和 IMAP
"""
import os
import json
import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.header import decode_header
from datetime import datetime
from typing import List, Dict, Any, Optional
import logging


logger = logging.getLogger(__name__)

class EmailAccount:
    """邮箱账户配置"""
    
    def __init__(
        self,
        email_address: str,
        password: str,
        smtp_server: str,
        smtp_port: int = 587,
        imap_server: str = None,
        imap_port: int = 993,
        use_ssl: bool = True
    ):
        self.email_address = email_address
        self.password = password
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.imap_server = imap_server or smtp_server.replace('smtp', 'imap')
        self.imap_port = imap_port
        self.use_ssl = use_ssl


class Email:
    """邮件对象"""
    
    def __init__(
        self,
        from_addr: str,
        to_addrs: List[str],
        subject: str,
        body: str,
        cc_addrs: List[str] = None,
        bcc_addrs: List[str] = None,
        attachments: List[str] = None,
        html: bool = False
    ):
        self.from_addr = from_addr
        self.to_addrs = to_addrs if isinstance(to_addrs, list) else [to_addrs]
        self.subject = subject
        self.body = body
        self.cc_addrs = cc_addrs or []
        self.bcc_addrs = bcc_addrs or []
        self.attachments = attachments or []
        self.html = html
        self.date = datetime.now()


class EmailManager:
    """邮件管理器"""
    
    def __init__(self):
        self.accounts: Dict[str, EmailAccount] = {}
        self.default_account: Optional[str] = None
        
        # 配置文件
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        config_dir = os.path.join(project_root, 'config')
        os.makedirs(config_dir, exist_ok=True)
        self.config_file = os.path.join(config_dir, 'email_accounts.json')
        
        self._load_accounts()
    
    def _load_accounts(self):
        """加载邮箱账户配置"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                for email_addr, config in data.get('accounts', {}).items():
                    self.accounts[email_addr] = EmailAccount(
                        email_address=email_addr,
                        password=config['password'],
                        smtp_server=config['smtp_server'],
                        smtp_port=config.get('smtp_port', 587),
                        imap_server=config.get('imap_server'),
                        imap_port=config.get('imap_port', 993),
                        use_ssl=config.get('use_ssl', True)
                    )
                
                self.default_account = data.get('default_account')
                logger.info(f"[邮件] 已加载 {len(self.accounts)} 个邮箱账户")
            except Exception as e:
                logger.info(f"[邮件] 配置加载失败: {e}")
    
    def _save_accounts(self):
        """保存邮箱账户配置"""
        try:
            data = {
                'default_account': self.default_account,
                'accounts': {}
            }
            
            for email_addr, account in self.accounts.items():
                data['accounts'][email_addr] = {
                    'password': account.password,
                    'smtp_server': account.smtp_server,
                    'smtp_port': account.smtp_port,
                    'imap_server': account.imap_server,
                    'imap_port': account.imap_port,
                    'use_ssl': account.use_ssl
                }
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                
            logger.info("[邮件] 配置已保存")
        except Exception as e:
            logger.info(f"[邮件] 配置保存失败: {e}")
    
    def add_account(
        self,
        email_address: str,
        password: str,
        smtp_server: str,
        smtp_port: int = 587,
        imap_server: str = None,
        imap_port: int = 993,
        set_as_default: bool = False
    ) -> bool:
        """
        添加邮箱账户
        
        Args:
            email_address: 邮箱地址
            password: 密码或授权码
            smtp_server: SMTP 服务器
            smtp_port: SMTP 端口
            imap_server: IMAP 服务器
            imap_port: IMAP 端口
            set_as_default: 是否设为默认账户
        """
        try:
            account = EmailAccount(
                email_address=email_address,
                password=password,
                smtp_server=smtp_server,
                smtp_port=smtp_port,
                imap_server=imap_server,
                imap_port=imap_port
            )
            
            self.accounts[email_address] = account
            
            if set_as_default or not self.default_account:
                self.default_account = email_address
            
            self._save_accounts()
            logger.info(f"[邮件] 账户已添加: {email_address}")
            return True
        except Exception as e:
            logger.info(f"[邮件] 账户添加失败: {e}")
            return False
    
    def send_email(
        self,
        to_addrs: List[str],
        subject: str,
        body: str,
        from_account: str = None,
        cc_addrs: List[str] = None,
        bcc_addrs: List[str] = None,
        attachments: List[str] = None,
        html: bool = False
    ) -> bool:
        """
        发送邮件
        
        Args:
            to_addrs: 收件人列表
            subject: 主题
            body: 正文
            from_account: 发件账户 (None 则使用默认账户)
            cc_addrs: 抄送列表
            bcc_addrs: 密送列表
            attachments: 附件路径列表
            html: 是否为 HTML 格式
        """
        account_email = from_account or self.default_account
        
        if not account_email or account_email not in self.accounts:
            logger.info("[邮件] 未找到有效的发件账户")
            return False
        
        account = self.accounts[account_email]
        
        try:
            # 创建邮件
            msg = MIMEMultipart()
            msg['From'] = account.email_address
            msg['To'] = ', '.join(to_addrs)
            msg['Subject'] = subject
            
            if cc_addrs:
                msg['Cc'] = ', '.join(cc_addrs)
            
            # 添加正文
            if html:
                msg.attach(MIMEText(body, 'html', 'utf-8'))
            else:
                msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            # 添加附件
            if attachments:
                for file_path in attachments:
                    if os.path.exists(file_path):
                        with open(file_path, 'rb') as f:
                            attachment = MIMEApplication(f.read())
                            attachment.add_header(
                                'Content-Disposition',
                                'attachment',
                                filename=os.path.basename(file_path)
                            )
                            msg.attach(attachment)
            
            # 发送邮件
            all_recipients = to_addrs + (cc_addrs or []) + (bcc_addrs or [])
            
            with smtplib.SMTP(account.smtp_server, account.smtp_port) as server:
                server.starttls()
                server.login(account.email_address, account.password)
                server.sendmail(account.email_address, all_recipients, msg.as_string())
            
            logger.info(f"[邮件] 已发送: {subject} -> {', '.join(to_addrs)}")
            return True
            
        except Exception as e:
            logger.info(f"[邮件] 发送失败: {e}")
            return False
    
    def fetch_emails(
        self,
        account_email: str = None,
        folder: str = 'INBOX',
        limit: int = 20,
        unread_only: bool = False
    ) -> List[Dict]:
        """
        获取邮件列表
        
        Args:
            account_email: 邮箱账户 (None 则使用默认账户)
            folder: 邮箱文件夹 (INBOX, Sent, Drafts 等)
            limit: 最大数量
            unread_only: 是否只获取未读邮件
        """
        account_email = account_email or self.default_account
        
        if not account_email or account_email not in self.accounts:
            logger.info("[邮件] 未找到有效的账户")
            return []
        
        account = self.accounts[account_email]
        
        try:
            # 连接 IMAP
            imap = imaplib.IMAP4_SSL(account.imap_server, account.imap_port)
            imap.login(account.email_address, account.password)
            imap.select(folder)
            
            # 搜索邮件
            search_criteria = 'UNSEEN' if unread_only else 'ALL'
            status, messages = imap.search(None, search_criteria)
            
            email_ids = messages[0].split()
            email_ids = email_ids[-limit:]  # 获取最新的 N 封
            
            emails = []
            
            for email_id in reversed(email_ids):
                status, msg_data = imap.fetch(email_id, '(RFC822)')
                
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        
                        # 解析主题
                        subject = self._decode_header(msg['Subject'])
                        
                        # 解析发件人
                        from_addr = self._decode_header(msg['From'])
                        
                        # 解析日期
                        date_str = msg['Date']
                        
                        # 解析正文
                        body = self._get_email_body(msg)
                        
                        emails.append({
                            'id': email_id.decode(),
                            'subject': subject,
                            'from': from_addr,
                            'date': date_str,
                            'body': body[:500] + '...' if len(body) > 500 else body
                        })
            
            imap.close()
            imap.logout()
            
            logger.info(f"[邮件] 已获取 {len(emails)} 封邮件")
            return emails
            
        except Exception as e:
            logger.info(f"[邮件] 获取失败: {e}")
            return []
    
    def _decode_header(self, header_value: str) -> str:
        """解码邮件头"""
        if not header_value:
            return ""
        
        decoded_parts = decode_header(header_value)
        result = []
        
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(encoding or 'utf-8', errors='ignore'))
            else:
                result.append(part)
        
        return ''.join(result)
    
    def _get_email_body(self, msg) -> str:
        """提取邮件正文"""
        body = ""
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == 'text/plain':
                    try:
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        break
                    except:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
            except:
                pass
        
        return body
    
    def search_emails(
        self,
        keyword: str,
        account_email: str = None,
        folder: str = 'INBOX'
    ) -> List[Dict]:
        """
        搜索邮件
        
        Args:
            keyword: 搜索关键词
            account_email: 邮箱账户
            folder: 邮箱文件夹
        """
        emails = self.fetch_emails(account_email, folder, limit=100)
        
        results = []
        for email_data in emails:
            if (keyword.lower() in email_data['subject'].lower() or
                keyword.lower() in email_data['body'].lower() or
                keyword.lower() in email_data['from'].lower()):
                results.append(email_data)
        
        return results


# 全局实例
_email_manager = None


def get_email_manager() -> EmailManager:
    """获取全局邮件管理器单例"""
    global _email_manager
    if _email_manager is None:
        _email_manager = EmailManager()
    return _email_manager
