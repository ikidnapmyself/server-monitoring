"""Email notification driver."""

import json
import logging
import smtplib
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage

logger = logging.getLogger(__name__)


class EmailNotifyDriver(BaseNotifyDriver):
    """Driver for sending email notifications."""

    name = "email"

    def validate_config(self, config: dict[str, Any]) -> bool:
        required_keys = {"smtp_host", "from_address"}
        return all(key in config for key in required_keys)

    def _resolve_to_addresses(
        self, message: NotificationMessage, config: dict[str, Any]
    ) -> list[str]:
        """Resolve the list of recipient addresses from config and message channel."""
        to_addresses = config.get("to_addresses", [])
        if not to_addresses and message.channel != "default":
            to_addresses = [message.channel]
        elif not to_addresses:
            to_addresses = [config["from_address"]]
        return to_addresses

    def _build_email(self, message: NotificationMessage, config: dict[str, Any]) -> MIMEMultipart:
        email = MIMEMultipart("alternative")

        email["Subject"] = f"[{message.severity.upper()}] {message.title}"
        email["From"] = config["from_address"]

        to_addresses = self._resolve_to_addresses(message, config)
        email["To"] = ", ".join(to_addresses)
        email["X-Priority"] = self.PRIORITY_MAP.get(message.severity, "3")

        prepared = self._prepare_notification(message, config)

        text_body = prepared.get("text")
        if not text_body:
            raise ValueError("Email text template required but not rendered")
        email.attach(MIMEText(text_body, "plain"))

        html_body = prepared.get("html")
        if html_body:
            email.attach(MIMEText(html_body, "html"))

        try:
            incident_json = json.dumps(
                prepared.get("incident") or {}, default=str, ensure_ascii=False
            )
            if incident_json:
                email.attach(MIMEText(incident_json, "plain"))
        except Exception:
            pass

        return email

    def send(self, message: NotificationMessage, config: dict[str, Any]) -> dict[str, Any]:
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
            email = self._build_email(message, config)
            message_id = str(uuid.uuid4())
            email["Message-ID"] = f"<{message_id}@{smtp_host}>"

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

                to_addresses = self._resolve_to_addresses(message, config)

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
                try:
                    server.quit()
                except smtplib.SMTPException:
                    pass

        except smtplib.SMTPAuthenticationError as e:
            return self._handle_exception(e, "Email", "authenticate SMTP")
        except smtplib.SMTPException as e:
            return self._handle_exception(e, "Email", "send SMTP")
        except Exception as e:
            return self._handle_exception(e, "Email", "send email")
