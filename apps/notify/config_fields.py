"""The settings a notify driver reads from its channel config, declared for the console form."""

from config.config_fields import ConfigField


def timeout_field() -> ConfigField:
    return ConfigField("timeout", "Timeout (seconds)", "int", default=30)


COMMON_FIELDS = (
    ConfigField(
        "channel",
        "Default destination",
        "text",
        help="Where messages go when the alert names none.",
    ),
    ConfigField("template", "Message template", "template"),
    ConfigField("html_template", "HTML template", "template"),
    ConfigField(
        "payload_template",
        "Payload template",
        "template",
        help="Used as the message text when no message template is set. "
        "A webhook or PagerDuty sends it as the JSON body.",
    ),
)
