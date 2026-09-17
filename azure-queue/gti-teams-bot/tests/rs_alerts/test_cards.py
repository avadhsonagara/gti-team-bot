"""Regression tests for the GTI alert -> Adaptive Card formatting."""
from app.cards import alert_id, build_alert_card


def test_alert_id_extracts_trailing_segment():
    assert alert_id({"name": "projects/p/alerts/abc123"}) == "abc123"


def test_alert_id_missing_name_returns_placeholder():
    assert alert_id({}) == "(unknown)"


def test_card_basic_shape_and_view_in_gti_action():
    alert = {
        "name": "projects/p/alerts/abc123",
        "displayName": "Suspicious IAB listing",
        "severityAnalysis": {"severityLevel": "SEVERITY_LEVEL_HIGH"},
        "priorityAnalysis": {"priorityLevel": "PRIORITY_LEVEL_CRITICAL"},
        "relevanceAnalysis": {"relevanceLevel": "RELEVANCE_LEVEL_HIGH", "confidence": "CONFIDENCE_LEVEL_MEDIUM"},
        "state": "NEW",
        "audit": {"updateTime": "2026-09-17T10:00:00Z", "createTime": "2026-09-17T09:00:00Z"},
        "findingCount": 3,
    }
    card = build_alert_card(alert, "p")

    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.4"
    assert card["msteams"] == {"width": "full"}

    header = card["body"][0]
    assert "🔴" in header["text"]  # CRITICAL priority emoji
    assert "Suspicious IAB listing" in header["text"]

    fact_titles = {f["title"]: f["value"] for block in card["body"] if block["type"] == "FactSet" for f in block["facts"]}
    assert fact_titles["Severity"] == "HIGH"
    assert fact_titles["Priority"] == "CRITICAL"
    assert fact_titles["Relevance"] == "HIGH (Confidence: MEDIUM)"
    assert fact_titles["State"] == "NEW"
    assert fact_titles["Findings"] == "3"
    assert fact_titles["Alert ID"] == "abc123"

    assert card["actions"] == [
        {"type": "Action.OpenUrl", "title": "View in GTI", "url": "https://proactive.virustotal.com/alerts/abc123?project=projects/p"}
    ]


def test_target_technology_detail_facts():
    alert = {
        "name": "projects/p/alerts/xyz",
        "detail": {
            "detailType": "target_technology",
            "targetTechnology": {
                "vulnerabilityMatch": {
                    "cveId": "CVE-2026-12345",
                    "cvss3Score": 9.83,
                    "riskRating": "CRITICAL",
                    "exploitationState": "EXPLOITATION_STATE_WIDESPREAD",
                    "publiclyAvailableExploit": True,
                    "associations": [
                        {"id": "APT99", "type": "THREAT_INTEL_OBJECT_TYPE_THREAT_ACTOR"},
                        {"id": "SomeMalware", "type": "THREAT_INTEL_OBJECT_TYPE_MALWARE"},
                    ],
                    "technologies": ["Widget OS"],
                }
            },
        },
    }
    card = build_alert_card(alert, "p")
    fact_titles = {f["title"]: f["value"] for block in card["body"] if block["type"] == "FactSet" for f in block["facts"]}

    assert fact_titles["CVE"] == "CVE-2026-12345"
    assert fact_titles["CVSS Score"] == "9.8"
    assert fact_titles["Risk Rating"] == "CRITICAL"
    assert fact_titles["Exploitation State"] == "WIDESPREAD"
    assert fact_titles["Publicly Available Exploit"] == "Yes"
    assert fact_titles["Associated Threat Actor"] == "APT99"
    assert fact_titles["Associated Malware"] == "SomeMalware"
    assert fact_titles["Affected Technologies"] == "Widget OS"


def test_iab_detail_facts_use_document_count_not_raw_paths():
    alert = {
        "name": "projects/p/alerts/iab1",
        "detail": {
            "detailType": "initial_access_broker",
            "initialAccessBroker": {
                "severity": "HIGH",
                "discoveryDocumentIds": ["projects/p/alerts/iab1/documents/d1", "projects/p/alerts/iab1/documents/d2"],
            },
        },
    }
    card = build_alert_card(alert, "p")
    fact_titles = {f["title"]: f["value"] for block in card["body"] if block["type"] == "FactSet" for f in block["facts"]}
    assert fact_titles["IAB Severity"] == "HIGH"
    assert fact_titles["Discovery Documents"] == "2"


def test_no_url_when_project_and_alert_id_unresolvable():
    card = build_alert_card({}, "")
    assert card["actions"] == []
