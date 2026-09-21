"""
Adaptive Card formatter for GTI alerts.
"""

_PRIORITY_EMOJI = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}
# proactive.virustotal.com/alerts/{id}?project=... used to be the deep link
# here, but it now 302s to a proactive-portal-specific login wall before
# ever reaching the alert (confirmed live) — this is GTI's actual web GUI
# alert-view route, which loads directly with no extra login hop.
_ALERTS_UI_BASE = "https://www.virustotal.com/gui/alerts"


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


def _alert_url(alert: dict) -> str | None:
    aid = alert_id(alert)
    if aid == "(unknown)":
        return None
    return f"{_ALERTS_UI_BASE}/{aid}"


# Maps the server-set `detailType` string to (a) the key the populated
# union sub-object actually lives under, and (b) a short display label.
_DETAIL_TYPE_INFO = {
    "initial_access_broker": ("initialAccessBroker", "IAB"),
    "data_leak": ("dataLeak", "Data Leak"),
    "insider_threat": ("insiderThreat", "Insider Threat"),
}


def _detail_facts(detail: dict) -> list[tuple[str, str]]:
    """
    Extract label/value facts from an alert's `detail` union, branching on
    `detailType`. Per the actual GTI Alert API schema, AlertDetail is a
    strict one-of: initialAccessBroker / dataLeak / insiderThreat (each
    just a `severity` + `discoveryDocumentIds`) or targetTechnology (a much
    richer `vulnerabilityMatch`). There is no generic actor/malware/
    indicator field on this object under any detailType.
    """
    detail_type = detail.get("detailType")
    facts: list[tuple[str, str]] = []

    if detail_type in _DETAIL_TYPE_INFO:
        field_name, label = _DETAIL_TYPE_INFO[detail_type]
        sub = detail.get(field_name) or {}
        if sub.get("severity"):
            facts.append((f"{label} Severity", sub["severity"]))
        doc_ids = sub.get("discoveryDocumentIds") or []
        if doc_ids:
            # These are full resource paths (projects/.../alerts/.../documents/<id>),
            # not short human-readable names — a count is useful in a card;
            # dumping the raw paths themselves would not be.
            facts.append(("Discovery Documents", str(len(doc_ids))))
        return facts

    if detail_type == "target_technology":
        vm = (detail.get("targetTechnology") or {}).get("vulnerabilityMatch") or {}
        if vm.get("cveId"):
            facts.append(("CVE", vm["cveId"]))
        if vm.get("cvss3Score") is not None:
            facts.append(("CVSS Score", str(round(vm["cvss3Score"], 1))))
        if vm.get("riskRating"):
            facts.append(("Risk Rating", vm["riskRating"]))
        exploitation_state = _strip_enum(vm.get("exploitationState"), "EXPLOITATION_STATE_")
        if exploitation_state:
            facts.append(("Exploitation State", exploitation_state))
        if vm.get("publiclyAvailableExploit"):
            facts.append(("Publicly Available Exploit", "Yes"))

        associations = vm.get("associations") or []
        actor_ids = [
            a["id"] for a in associations
            if a.get("id") and _strip_enum(a.get("type"), "THREAT_INTEL_OBJECT_TYPE_") == "THREAT_ACTOR"
        ]
        malware_ids = [
            a["id"] for a in associations
            if a.get("id") and _strip_enum(a.get("type"), "THREAT_INTEL_OBJECT_TYPE_") == "MALWARE"
        ]
        if actor_ids:
            facts.append(("Associated Threat Actor", ", ".join(actor_ids[:5])))
        if malware_ids:
            facts.append(("Associated Malware", ", ".join(malware_ids[:5])))

        technologies = vm.get("technologies") or []
        if technologies:
            facts.append(("Affected Technologies", ", ".join(technologies[:5])))

    return facts


def build_alert_card(alert: dict) -> dict:
    """Build a Teams-compatible Adaptive Card v1.4 for a GTI alert."""
    severity = _strip_enum(alert.get("severityAnalysis", {}).get("severityLevel"), "SEVERITY_LEVEL_")
    priority = _strip_enum(alert.get("priorityAnalysis", {}).get("priorityLevel"), "PRIORITY_LEVEL_")
    relevance = _strip_enum(alert.get("relevanceAnalysis", {}).get("relevanceLevel"), "RELEVANCE_LEVEL_")
    confidence = _strip_enum(alert.get("relevanceAnalysis", {}).get("confidence"), "CONFIDENCE_LEVEL_")
    state = _strip_enum(alert.get("state"), "ALERT_STATE_", "STATE_")
    detail = alert.get("detail", {})
    detail_type = detail.get("detailType")
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

    relevance_value = f"{relevance} (Confidence: {confidence})" if relevance and confidence else relevance

    facts = []
    for label, val in (
        ("Severity", severity), ("Priority", priority), ("Relevance", relevance_value),
        ("State", state), ("Type", detail_type),
    ):
        if val:
            facts.append({"title": label, "value": str(val)})

    facts.extend({"title": label, "value": val} for label, val in _detail_facts(detail))

    for label, val in (
        ("Findings", str(finding_count) if finding_count is not None else None),
        ("Updated", audit.get("updateTime")), ("Created", audit.get("createTime")),
        ("Alert ID", aid),
    ):
        if val:
            facts.append({"title": label, "value": str(val)})

    if facts:
        body.append({"type": "FactSet", "facts": facts, "spacing": "Medium"})

    actions = []
    url = _alert_url(alert)
    if url:
        actions.append({"type": "Action.OpenUrl", "title": "View in GTI", "url": url})

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "msteams": {
            "width": "full",
        },
        "body": body,
        "actions": actions,
    }
