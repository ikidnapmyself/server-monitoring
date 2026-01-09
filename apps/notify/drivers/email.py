"""Email notification driver."""

import logging
import smtplib
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage

logger = logging.getLogger(__name__)


class EmailNotifyDriver(BaseNotifyDriver):
    """
    Driver for sending email notifications.

    Configuration:
    {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "from_address": "alerts@example.com",
        "to_addresses": ["ops@example.com"],
        "use_tls": True,
        "use_ssl": False,
        "username": "user@example.com",
        "password": "secret",
        "timeout": 30
    }
    """

    name = "email"

    # Severity to email priority mapping
    PRIORITY_MAP = {
        "critical": "1",  # Highest
        "warning": "2",  # High
        "info": "3",  # Normal
        "success": "3",  # Normal
    }

    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate email configuration."""
        required_keys = {"smtp_host", "from_address"}
        return all(key in config for key in required_keys)

    def _build_email(self, message: NotificationMessage, config: dict[str, Any]) -> MIMEMultipart:
        """Build the email message."""
        email = MIMEMultipart("alternative")

        # Headers
        email["Subject"] = f"[{message.severity.upper()}] {message.title}"
        email["From"] = config["from_address"]

        # Determine recipients
        to_addresses = config.get("to_addresses", [])
        if not to_addresses and message.channel != "default":
            to_addresses = [message.channel]
        elif not to_addresses:
            to_addresses = [config["from_address"]]  # Fallback to sender

        email["To"] = ", ".join(to_addresses)

        # Priority header based on severity
        email["X-Priority"] = self.PRIORITY_MAP.get(message.severity, "3")

        # Build plain text body
        text_body = self._build_text_body(message)
        email.attach(MIMEText(text_body, "plain"))

        # Build HTML body
        html_body = self._build_html_body(message)
        email.attach(MIMEText(html_body, "html"))

        return email

    def _build_text_body(self, message: NotificationMessage) -> str:
        """Build plain text email body."""
        lines = [
            f"Alert: {message.title}",
            f"Severity: {message.severity.upper()}",
            "",
            message.message,
            "",
        ]

        if message.tags:
            lines.append("Tags:")
            for key, value in message.tags.items():
                lines.append(f"  {key}: {value}")
            lines.append("")

        if message.context:
            lines.append("Context:")
            for key, value in message.context.items():
                lines.append(f"  {key}: {value}")

        return "\n".join(lines)

    def _build_html_body(self, message: NotificationMessage) -> str:
        """Build HTML email body."""
        severity_colors = {
            "critical": "#dc3545",
            "warning": "#ffc107",
            "info": "#17a2b8",
            "success": "#28a745",
        }
        color = severity_colors.get(message.severity, "#6c757d")

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; margin: 0; padding: 20px;">
            <div style="border-left: 4px solid {color}; padding-left: 15px;">
                <h2 style="margin: 0 0 10px 0; color: {color};">{message.title}</h2>
                <span style="background-color: {color}; color: white; padding: 2px 8px; border-radius: 3px; font-size: 12px;">
                    {message.severity.upper()}
                </span>
            </div>
            <p style="margin-top: 20px; line-height: 1.6;">{message.message}</p>
        """

        if message.tags:
            html += '<div style="margin-top: 20px;"><strong>Tags:</strong><ul>'
            for key, value in message.tags.items():
                html += f"<li><code>{key}</code>: {value}</li>"
            html += "</ul></div>"

        if message.context:
            html += '<div style="margin-top: 20px;"><strong>Context:</strong><ul>'
            for key, value in message.context.items():
                html += f"<li><code>{key}</code>: {value}</li>"
            html += "</ul></div>"

        html += """
        </body>
        </html>
        """
        return html

    def send(self, message: NotificationMessage, config: dict[str, Any]) -> dict[str, Any]:
        """Send an email notification.

        Args:
            message: The notification message
            config: Email configuration

        Returns:
            Result dictionary with success status
        """
        if not self.validate_config(config):
            return {
                "success": False,
                "error": "Invalid email configuration (smtp_host and from_address required)",
            }

        smtp_host = config["smtp_host"]
        smtp_port = config.get("smtp_port", 587)
        use_tls = config.get("use_tls", True)
        use_ssl = config.get("use_ssl", False)
        username = config.get("username")
        password = config.get("password")
        timeout = config.get("timeout", 30)

        try:
            # Build email message
            email = self._build_email(message, config)
            message_id = str(uuid.uuid4())
            email["Message-ID"] = f"<{message_id}@{smtp_host}>"

            # Connect and send
            server: smtplib.SMTP | smtplib.SMTP_SSL
            if use_ssl:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=timeout)

            try:
                if use_tls and not use_ssl:
                    server.starttls()

                if username and password:
                    server.login(username, password)

                # Send the email
                to_addresses = config.get("to_addresses", [])
                if not to_addresses and message.channel != "default":
                    to_addresses = [message.channel]
                elif not to_addresses:
                    to_addresses = [config["from_address"]]

                server.sendmail(
                    config["from_address"],
                    to_addresses,
                    email.as_string(),
                )

                logger.info(f"Email sent successfully: {message_id}")

                return {
                    "success": True,
                    "message_id": message_id,
                    "metadata": {
                        "to": to_addresses,
                        "from": config["from_address"],
                        "subject": email["Subject"],
                    },
                }

            finally:
                server.quit()

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP authentication failed: {e}")
            return {
                "success": False,
                "error": f"SMTP authentication failed: {e}",
            }
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            return {
                "success": False,
                "error": f"SMTP error: {e}",
            }
        except Exception as e:
            logger.exception(f"Failed to send email: {e}")
            return {
                "success": False,
                "error": f"Failed to send email: {e}",
            }
