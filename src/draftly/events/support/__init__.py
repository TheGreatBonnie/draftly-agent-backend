"""Support event processors (plan §7.1)."""

from draftly.events.support.discord import DiscordProcessor
from draftly.events.support.slack import SlackProcessor

__all__ = ["DiscordProcessor", "SlackProcessor"]
