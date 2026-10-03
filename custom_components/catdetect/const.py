"""Константы интеграции CatDetect."""

DOMAIN = "catdetect"
CONF_URL = "url"
CONF_TOKEN = "token"

PLATFORMS = ["binary_sensor", "sensor", "event", "image"]

EVENT_KINDS = ("seen", "arrived", "left")
SIGNAL_EVENT = f"{DOMAIN}_event_{{}}"
SIGNAL_SNAPSHOT = f"{DOMAIN}_snapshot_{{}}"
