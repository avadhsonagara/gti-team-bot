"""
Activity context wrapper for Microsoft Teams messaging interactions.

Provides a unified interface (Ctx) for sending, editing, and deleting activities
within the current conversation.
"""
from types import SimpleNamespace
from typing import Union

from app.teams.bot_client import delete_activity, send_activity, update_activity


def _normalize_activity(activity: Union[str, dict]) -> dict:
    """
    Normalize a message string or activity dictionary into an Activity payload.

    Args:
        activity: Text message string or Activity dictionary.

    Returns:
        Formatted activity payload dictionary.
    """
    if isinstance(activity, str):
        return {"type": "message", "text": activity}
    return activity


class _Activities:
    """API client for updating and deleting activities in a conversation."""

    def __init__(self, service_url: str, conversation_id: str) -> None:
        self._service_url = service_url
        self._conversation_id = conversation_id

    def update(self, activity_id: str, activity: Union[str, dict]) -> None:
        """Update an existing activity in the conversation."""
        update_activity(self._service_url, self._conversation_id, activity_id, _normalize_activity(activity))

    def delete(self, activity_id: str) -> None:
        """Delete an existing activity from the conversation."""
        delete_activity(self._service_url, self._conversation_id, activity_id)


class _Conversations:
    """Conversation-level API endpoint router."""

    def __init__(self, service_url: str) -> None:
        self._service_url = service_url

    def activities(self, conversation_id: str) -> _Activities:
        """Return an activities API interface scoped to the conversation ID."""
        return _Activities(self._service_url, conversation_id)


class _Api:
    """Top-level Bot Framework API client wrapper."""

    def __init__(self, service_url: str) -> None:
        self.conversations = _Conversations(service_url)


class Ctx:
    """
    Context representing an active Teams activity interaction.

    Attributes:
        activity: Parsed SimpleNamespace representing the current inbound activity.
        api: Bot Framework API client scoped to the activity's service URL.
    """

    def __init__(self, activity: SimpleNamespace) -> None:
        self.activity = activity
        self.api = _Api(activity.service_url)

    def send(self, activity: Union[str, dict]) -> SimpleNamespace:
        """
        Send a reply activity to the current conversation.

        Args:
            activity: Plain text string or activity dictionary.

        Returns:
            SimpleNamespace containing the sent activity's ID.
        """
        result = send_activity(self.activity.service_url, self.activity.conversation.id, _normalize_activity(activity))
        return SimpleNamespace(id=result.get("id"))
