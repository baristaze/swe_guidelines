"""LaunchDarkly, the vendor the scaffold names, as an OpenFeature provider.
Its SDK key is a process credential, injected at start: it reaches the
SDK's config here and nowhere else, and no line this process writes names
it. Each org is a context of kind `org` keyed by its id, so an org is
targeted by key and a person by a rule on `user_id`."""

from datetime import timedelta

from ld_openfeature import LaunchDarklyProvider
from ldclient.config import Config, HTTPConfig

from acme.infra.flags.openfeature import FlagsOpenFeatureImpl


def launchdarkly_config(sdk_key: str, timeout: timedelta) -> Config:
    """The deployed config: the SDK streams its rules and evaluates in this
    process. `timeout` bounds each connect and read; the stream keeps the
    SDK's own longer read bound, since it idles between changes."""
    seconds = timeout.total_seconds()
    return Config(sdk_key=sdk_key, http=HTTPConfig(connect_timeout=seconds, read_timeout=seconds))


def launchdarkly_flags(config: Config, start_wait: timedelta) -> FlagsOpenFeatureImpl:
    """Boot waits at most `start_wait` for the first rules, then runs on the
    code's defaults until they arrive."""
    seconds = start_wait.total_seconds()
    return FlagsOpenFeatureImpl("launchdarkly", lambda: LaunchDarklyProvider(config, seconds))
