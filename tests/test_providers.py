from copy import deepcopy
from datetime import timedelta

import httpx
import pytest

from all_sky_alert.providers import ConditionsService, OpenMeteo
from conftest import FakeProvider


def test_shared_cache_and_restart(store, config, weather, now):
    provider = FakeProvider(weather)
    service = ConditionsService(store, provider, config.observer, config.values["atmosphere"])
    service.refresh(now)
    for i in range(5):
        assert service.at(now + timedelta(hours=i), {}).status == "fresh"
    service.refresh(now + timedelta(minutes=5))
    restarted = ConditionsService(store, provider, config.observer, config.values["atmosphere"])
    restarted.refresh(now + timedelta(minutes=10))
    assert provider.calls == 1
    assert restarted.at(now, {}).metrics["cloud_total"].value == 5


def test_stale_backoff_and_expiration(store, config, weather, now):
    provider = FakeProvider(weather)
    service = ConditionsService(store, provider, config.observer, config.values["atmosphere"])
    service.refresh(now)
    provider.error = httpx.ConnectError("do not persist secret payload")
    later = now + timedelta(hours=2)
    service.refresh(later)
    assert service.at(later, {}).status == "stale"
    confidence = service.at(later, {}).metrics["cloud_total"].confidence
    assert confidence < 0.3
    service.refresh(later + timedelta(seconds=10))
    assert provider.calls == 2
    assert "secret" not in str(store.cache(service.key))
    service.refresh(now + timedelta(hours=7))
    assert service.at(now, {}).status == "unavailable"


def test_null_missing_horizon_and_observer_separation(store, config, weather, now):
    data = deepcopy(weather)
    data["hourly"]["visibility"] = [None] * len(data["hourly"]["time"])
    provider = FakeProvider(data)
    service = ConditionsService(store, provider, config.observer, config.values["atmosphere"])
    service.refresh(now)
    assert "visibility" not in service.at(now, {}).metrics
    assert service.at(now + timedelta(days=30), {}).status == "outside_forecast"
    from dataclasses import replace
    other = ConditionsService(store, provider, replace(config.observer, longitude=1), config.values["atmosphere"])
    other.refresh(now)
    assert provider.calls == 2


@pytest.mark.parametrize("mutation", ["range", "units", "nan", "axis", "shape"])
def test_external_data_validation(weather, mutation):
    data = deepcopy(weather)
    if mutation == "range":
        data["hourly"]["cloud_cover"][0] = 101
    elif mutation == "units":
        data["hourly_units"]["wind_speed_10m"] = "km/h"
    elif mutation == "nan":
        data["hourly"]["visibility"][0] = float('nan')
    elif mutation == "axis":
        data["hourly"]["time"][1] = data["hourly"]["time"][0]
    else:
        data["hourly"]["cloud_cover"] = []
    with pytest.raises(ValueError):
        OpenMeteo.validate(data)


def test_429_retry_after_is_persistent(store, config, now):
    request = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
    response = httpx.Response(429, headers={"Retry-After": "1800"}, request=request)
    provider = FakeProvider({})
    provider.error = httpx.HTTPStatusError("rate limited", request=request, response=response)
    service = ConditionsService(store, provider, config.observer, config.values["atmosphere"])
    service.refresh(now)
    restarted = ConditionsService(store, provider, config.observer, config.values["atmosphere"])
    restarted.refresh(now + timedelta(minutes=20))
    assert provider.calls == 1
    assert restarted.at(now, {}).status == "unavailable"


def test_actual_http_contract_with_mock_transport(config, weather):
    def handler(request):
        assert request.url.params["wind_speed_unit"] == "ms"
        assert request.url.params["timezone"] == "UTC"
        assert "cloud_cover_high" in request.url.params["hourly"]
        return httpx.Response(200, json=weather)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert OpenMeteo(client).fetch(config.observer, 7) == weather
