from opswatch.config import Config, Target
from opswatch.formatting import incident_markup, repair_markup


def configured():
    return Config(
        telegram={"dashboard_url": "https://ops.example.com", "interactive_controls": False},
        targets=[Target(id="web", name="Web", kind="http", url="https://app.example.com")],
    )


def test_telegram_alert_is_a_notification_with_a_dashboard_link():
    config = configured()
    item = {"ref": "OW-0001", "target": "web", "evidence": {}, "kind": "security"}
    buttons = [button for row in incident_markup(item, config)["inline_keyboard"] for button in row]
    assert {"text": "Open incident", "url": "https://ops.example.com/incidents?ref=OW-0001"} in buttons
    assert not any("callback_data" in button for button in buttons)


def test_repair_notification_links_to_dashboard_and_reviewable_pr():
    config = configured()
    job = {
        "id": "RF-0001",
        "incident": "OW-0001",
        "status": "pr_open",
        "pr_url": "https://github.com/owner/app/pull/1",
    }
    buttons = [button for row in repair_markup(job, config)["inline_keyboard"] for button in row]
    assert any(button.get("url") == "https://ops.example.com/agents" for button in buttons)
    assert any(button.get("url") == job["pr_url"] for button in buttons)
    assert not any("callback_data" in button for button in buttons)


def test_untrusted_dashboard_url_cannot_be_published(monkeypatch):
    config = configured()
    config.telegram.dashboard_url = "https://user:secret@example.com"
    monkeypatch.delenv("OPSWATCH_RELAY_URL", raising=False)
    assert repair_markup({"id": "RF-0001", "incident": "OW-0001", "status": "coding"}, config) is None
