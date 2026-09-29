"""The two Slack apps' manifests, one per deployed environment, hold what the
code expects: the bot scopes it asks for at install and nothing else, the
three URLs of that environment's API, the events the worker handles, HTTP
and not Socket Mode, and token rotation on."""

import json
from pathlib import Path
from typing import Any

import pytest

from acme.integrations.slack import BOT_SCOPES

REPO = Path(__file__).resolve().parents[2]
ENVIRONMENTS = json.loads((REPO / "deployment/cloud/environments.json").read_text())
EVENTS = {
    "app_home_opened",
    "app_mention",
    "app_uninstalled",
    "tokens_revoked",
    "member_joined_channel",
}


def manifest(environment: str) -> dict[str, Any]:
    return json.loads((REPO / f"deployment/slack/manifest.{environment}.json").read_text())


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_a_manifest_is_the_apps_whole_contract(environment: str) -> None:
    app = manifest(environment)
    api = ENVIRONMENTS["environments"][environment]["api_domain_name"]
    assert tuple(app["oauth_config"]["scopes"]["bot"]) == BOT_SCOPES
    assert "user" not in app["oauth_config"]["scopes"], "the app acts as its bot alone"
    assert app["oauth_config"]["redirect_urls"] == [f"https://{api}/webhooks/slack/oauth"]
    [command] = app["features"]["slash_commands"]
    assert command["command"] == "/acme"
    assert command["url"] == f"https://{api}/webhooks/slack/commands"
    events = app["settings"]["event_subscriptions"]
    assert events["request_url"] == f"https://{api}/webhooks/slack/events"
    assert set(events["bot_events"]) == EVENTS
    settings = app["settings"]
    assert settings["socket_mode_enabled"] is False
    assert settings["token_rotation_enabled"] is True
    assert settings["interactivity"] == {"is_enabled": False}
    assert 174 <= len(app["display_information"]["long_description"]) <= 4000


def test_the_usage_hint_names_the_commands_the_worker_knows() -> None:
    for environment in ("staging", "production"):
        [command] = manifest(environment)["features"]["slash_commands"]
        assert command["usage_hint"] == "[team | add <title> | connect | help]"
