"""
Adaptive Card formatter for GTI alerts.
"""

_PRIORITY_EMOJI = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}
_ALERTS_UI_BASE = "https://proactive.virustotal.com/alerts"


def _strip_enum(value: str | None, *prefixes: str) -> str | None:
    if not value:
        return None
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def alert_id(alert: dict) -> str:
    name = alert.get("name", "")
    return name.rsplit("/", 1)[-1] if name else "(unknown)"


def _alert_url(alert: dict, project: str) -> str | None:
    aid = alert_id(alert)
    if not project:
        parts = alert.get("name", "").split("/")
        project = parts[1] if len(parts) >= 2 and parts[0] == "projects" else ""
    if aid == "(unknown)" or not project:
        return None
    return f"{_ALERTS_UI_BASE}/{aid}?project=projects/{project}"


def build_alert_card(alert: dict, project: str) -> dict:
    """Build a Teams-compatible Adaptive Card v1.4 for a GTI alert."""
    severity = _strip_enum(alert.get("severityAnalysis", {}).get("severityLevel"), "SEVERITY_LEVEL_")
    priority = _strip_enum(alert.get("priorityAnalysis", {}).get("priorityLevel"), "PRIORITY_LEVEL_")
    relevance = _strip_enum(alert.get("relevanceAnalysis", {}).get("relevanceLevel"), "RELEVANCE_LEVEL_")
    state = _strip_enum(alert.get("state"), "ALERT_STATE_", "STATE_")
    detail_type = alert.get("detail", {}).get("detailType")
    audit = alert.get("audit", {})
    aid = alert_id(alert)
    finding_count = alert.get("findingCount")

    emoji = _PRIORITY_EMOJI.get((priority or "").upper(), "⚪")
    title = alert.get("displayName") or detail_type or f"GTI Alert {aid}"

    body = [
        {"type": "TextBlock", "text": f"{emoji} GTI Alert: {title}", "weight": "Bolder", "size": "Large", "wrap": True}
    ]

    ai_summary = alert.get("aiSummary")
    if ai_summary:
        body.append({"type": "TextBlock", "text": ai_summary[:3000], "wrap": True, "spacing": "Medium"})

    facts = []
    for label, val in (
        ("Severity", severity), ("Priority", priority), ("Relevance", relevance),
        ("State", state), ("Type", detail_type),
        ("Findings", str(finding_count) if finding_count is not None else None),
        ("Updated", audit.get("updateTime")), ("Created", audit.get("createTime")),
        ("Alert ID", aid),
    ):
        if val:
            facts.append({"title": label, "value": str(val)})

    if facts:
        body.append({"type": "FactSet", "facts": facts, "spacing": "Medium"})

    actions = []
    url = _alert_url(alert, project)
    if url:
        actions.append({"type": "Action.OpenUrl", "title": "View in GTI", "url": url})

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
        "actions": actions,
    }
