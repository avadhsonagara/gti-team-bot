"""
Minimal stand-in for the Teams SDK's ActivityContext (ctx), backed by plain
synchronous Bot Framework Connector API calls (app/teams/bot_client.py)
instead of the microsoft-teams-apps SDK. app/teams/handlers.py and
app/utils/helpers.py only ever used ctx.activity, ctx.send(), and
ctx.api.conversations.activities(id).update()/.delete() — so this is the
entire surface they need, letting that code stay unchanged beyond dropping
async/await.
"""
from types import SimpleNamespace
from typing import Union

from app.teams.bot_client import delete_activity, send_activity, update_activity


def _normalize_activity(activity: Union[str, dict]) -> dict:
    """A bare string is shorthand for a plain text message activity — same convenience the SDK's ctx.send() offered."""
    if isinstance(activity, str):
        return {"type": "message", "text": activity}
    return activity


class _Activities:
    def __init__(self, service_url: str, conversation_id: str) -> None:
        self._service_url = service_url
        self._conversation_id = conversation_id

    def update(self, activity_id: str, activity: Union[str, dict]) -> None:
        update_activity(self._service_url, self._conversation_id, activity_id, _normalize_activity(activity))

    def delete(self, activity_id: str) -> None:
        delete_activity(self._service_url, self._conversation_id, activity_id)


class _Conversations:
    def __init__(self, service_url: str) -> None:
        self._service_url = service_url

    def activities(self, conversation_id: str) -> _Activities:
        return _Activities(self._service_url, conversation_id)


class _Api:
    def __init__(self, service_url: str) -> None:
        self.conversations = _Conversations(service_url)


class Ctx:
    """Everything handlers.py/helpers.py need from an inbound message: ctx.activity, ctx.send(), ctx.api.conversations."""

    def __init__(self, activity: SimpleNamespace) -> None:
        self.activity = activity
        self.api = _Api(activity.service_url)

    def send(self, activity: Union[str, dict]) -> SimpleNamespace:
        result = send_activity(self.activity.service_url, self.activity.conversation.id, _normalize_activity(activity))
        return SimpleNamespace(id=result.get("id"))
