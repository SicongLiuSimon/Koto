"""
Phase 5b: Alerting System

Email and webhook notifications for monitoring events.
Supports customizable alert rules and severity thresholds.
"""

import json
import logging
import smtplib
import threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class AlertChannel(Enum):
    """Alert delivery channels."""

    EMAIL = "email"
    WEBHOOK = "webhook"
    LOG = "log"


class AlertRule:
    """
    Rule for triggering alerts based on events.
    """

    def __init__(
        self,
        name: str,
        event_types: List[str],
        min_severity: str = "medium",
        channels: Optional[List[AlertChannel]] = None,
    ):
        """
        Initialize alert rule.

        Args:
            name: Rule name
            event_types: Event types to match (e.g., ['cpu_high', 'memory_high'])
            min_severity: Minimum severity to trigger alert (low, medium, high)
            channels: Alert delivery channels
        """
        self.name = name
        self.event_types = event_types
        self.min_severity = min_severity
        self.channels = channels or [AlertChannel.LOG]
        self.enabled = True

    def matches(self, event: Dict[str, Any]) -> bool:
        """Check if event matches this rule."""
        if not self.enabled:
            return False

        # Check event type
        if event.get("event_type") not in self.event_types:
            return False

        # Check severity
        severity_order = {"low": 0, "medium": 1, "high": 2}
        event_severity = severity_order.get(event.get("severity", "low"), 0)
        rule_severity = severity_order.get(self.min_severity, 1)

        return event_severity >= rule_severity

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "name": self.name,
            "event_types": self.event_types,
            "min_severity": self.min_severity,
            "channels": [ch.value for ch in self.channels],
            "enabled": self.enabled,
        }


class AlertManager:
    """
    Manages alert rules and sends notifications.
    """

    _MAX_ALERT_HISTORY = 10000

    def __init__(self):
        """Initialize alert manager."""
        self.rules: Dict[str, AlertRule] = {}
        self.email_config: Optional[Dict[str, str]] = None
        self.webhook_urls: Dict[str, str] = {}  # channel_name -> url
        self.alert_history: List[Dict[str, Any]] = []
        self.handlers: Dict[AlertChannel, Callable] = {
            AlertChannel.EMAIL: self._send_email_alert,
            AlertChannel.WEBHOOK: self._send_webhook_alert,
            AlertChannel.LOG: self._send_log_alert,
        }

    def configure_email(
        self,
        smtp_server: str,
        smtp_port: int,
        sender_email: str,
        sender_password: str,
        recipients: List[str],
    ) -> bool:
        """
        Configure email alerting.

        Args:
            smtp_server: SMTP server address
            smtp_port: SMTP port
            sender_email: From email address
            sender_password: Email password/token
            recipients: List of recipient emails

        Returns:
            True if configuration successful
        """
        self.email_config = {
            "smtp_server": smtp_server,
            "smtp_port": smtp_port,
            "sender_email": sender_email,
            "sender_password": sender_password,
            "recipients": recipients,
        }
        logger.info(f"Email alerting configured with {len(recipients)} recipients")
        return True

    def add_webhook(self, name: str, url: str) -> bool:
        """
        Add webhook endpoint for alerts.

        Args:
            name: Webhook name (e.g., 'slack', 'teams')
            url: Webhook URL

        Returns:
            True if added successfully
        """
        self.webhook_urls[name] = url
        logger.info(f"Webhook '{name}' registered: {url}")
        return True

    def add_rule(self, rule: AlertRule) -> bool:
        """
        Add alert rule.

        Args:
            rule: AlertRule instance

        Returns:
            True if added
        """
        self.rules[rule.name] = rule
        logger.info(f"Alert rule '{rule.name}' added")
        return True

    def process_event(self, event: Dict[str, Any]) -> List[str]:
        """
        Check event against rules and send alerts.

        Args:
            event: Event dict from monitoring

        Returns:
            List of alert IDs sent
        """
        alert_ids = []

        for rule_name, rule in self.rules.items():
            if rule.matches(event):
                alert_id = self._send_alerts(rule, event)
                if alert_id:
                    alert_ids.append(alert_id)

        return alert_ids

    def _send_alerts(self, rule: AlertRule, event: Dict[str, Any]) -> Optional[str]:
        """Send alerts via configured channels for a rule."""
        alert_id = f"{event.get('event_type')}_{int(datetime.now().timestamp())}"

        try:
            for channel in rule.channels:
                if channel in self.handlers:
                    try:
                        self.handlers[channel](rule, event)
                    except Exception as e:
                        logger.error(f"Error sending {channel.value} alert: {e}")

            # Record in history
            self.alert_history.append(
                {
                    "id": alert_id,
                    "rule": rule.name,
                    "event_type": event.get("event_type"),
                    "severity": event.get("severity"),
                    "timestamp": datetime.now().isoformat(),
                    "channels": [ch.value for ch in rule.channels],
                }
            )

            if len(self.alert_history) > self._MAX_ALERT_HISTORY:
                self.alert_history = self.alert_history[-self._MAX_ALERT_HISTORY:]

            return alert_id
        except Exception as e:
            logger.error(f"Error processing alerts: {e}")
            return None

    def _send_email_alert(self, rule: AlertRule, event: Dict[str, Any]) -> None:
        """Send email alert."""
        if not self.email_config:
            logger.warning("Email alerting not configured")
            return

        # Build email
        subject = f"[{event.get('severity').upper()}] {event.get('event_type')}: {event.get('description')}"

        body = f"""
System Monitoring Alert

Alert Rule: {rule.name}
Event Type: {event.get('event_type')}
Severity: {event.get('severity')}
Timestamp: {event.get('timestamp')}

Description:
{event.get('description')}

Metric: {event.get('metric_name')}
Current Value: {event.get('metric_value')}
Threshold: {event.get('threshold')}

Please log in to the monitoring dashboard for more details.
        """

        # Send in background thread
        threading.Thread(
            target=self._send_email_async, args=(subject, body), daemon=True
        ).start()

    def _send_email_async(self, subject: str, body: str) -> None:
        """Send email asynchronously."""
        try:
            config = self.email_config

            msg = MIMEMultipart()
            msg["From"] = config["sender_email"]
            msg["To"] = ", ".join(config["recipients"])
            msg["Subject"] = subject

            msg.attach(MIMEText(body, "plain"))

            # Connect and send
            server = smtplib.SMTP(config["smtp_server"], config["smtp_port"])
            server.starttls()
            server.login(config["sender_email"], config["sender_password"])
            server.send_message(msg)
            server.quit()

            logger.info(f"Email alert sent: {subject}")
        except Exception as e:
            logger.error(f"Error sending email: {e}")

    def _send_webhook_alert(self, rule: AlertRule, event: Dict[str, Any]) -> None:
        """Send webhook alert."""
        if not self.webhook_urls:
            logger.warning("No webhooks configured")
            return

        payload = {
            "rule": rule.name,
            "event": event,
            "timestamp": datetime.now().isoformat(),
        }

        # Send to all webhooks in background
        for name, url in self.webhook_urls.items():
            threading.Thread(
                target=self._send_webhook_async, args=(name, url, payload), daemon=True
            ).start()

    def _send_webhook_async(self, name: str, url: str, payload: Dict[str, Any]) -> None:
        """Send webhook asynchronously."""
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=10,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code < 400:
                logger.info(f"Webhook '{name}' alert sent successfully")
            else:
                logger.warning(f"Webhook '{name}' returned {response.status_code}")
        except Exception as e:
            logger.error(f"Error sending webhook '{name}': {e}")

    def _send_log_alert(self, rule: AlertRule, event: Dict[str, Any]) -> None:
        """Log alert."""
        logger.warning(
            f"[ALERT: {rule.name}] {event.get('event_type')} - "
            f"{event.get('severity')}: {event.get('description')}"
        )

    def get_alert_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent alerts."""
        return self.alert_history[-limit:]

    def get_rules(self) -> Dict[str, Dict[str, Any]]:
        """Get all alert rules."""
        return {name: rule.to_dict() for name, rule in self.rules.items()}


# Global instance
_alert_manager: Optional[AlertManager] = None
_alert_lock = threading.Lock()


def get_alert_manager() -> AlertManager:
    """Get or create the singleton AlertManager instance."""
    global _alert_manager

    if _alert_manager is None:
        with _alert_lock:
            if _alert_manager is None:
                _alert_manager = AlertManager()

                # Add default rules
                _alert_manager.add_rule(
                    AlertRule(
                        "cpu_critical", ["cpu_spike", "cpu_high"], min_severity="high"
                    )
                )
                _alert_manager.add_rule(
                    AlertRule("memory_warning", ["memory_high"], min_severity="medium")
                )
                _alert_manager.add_rule(
                    AlertRule(
                        "disk_critical", ["disk_full", "disk_high"], min_severity="high"
                    )
                )

    return _alert_manager
