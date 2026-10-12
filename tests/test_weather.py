"""Today's weather in the morning digest, with a fake Open-Meteo and an invented forecast."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import yaml

from services.weather.forecast import OpenMeteo, WeatherUnavailable, weather_line, wet_hours

TZ = ZoneInfo("America/New_York")


def hours(chances):
    """Hourly rain chances for 2026-10-12, from midnight."""
    return [(f"2026-10-12T{h:02d}:00", c) for h, c in enumerate(chances)]


DRY = hours([0] * 24)
AFTERNOON_RAIN = hours([10] * 15 + [60, 70, 55, 55] + [20] * 5)  # 3pm-6pm at or above 50%

OPEN_METEO = {
    "daily": {"time": ["2026-10-12"], "weather_code": [61], "temperature_2m_max": [67.6],
              "temperature_2m_min": [50.8], "precipitation_probability_max": [70], "wind_speed_10m_max": [12.3]},
    "hourly": {"time": [t for t, _ in AFTERNOON_RAIN], "precipitation_probability": [c for _, c in AFTERNOON_RAIN]},
}


def forecast(**changes):
    return {"code": 2, "high": 67.6, "low": 50.8, "rain_chance": 0, "wind": 8, "hours": DRY,
            "units": "°F", "wind_units": "mph", **changes}


# The client

def test_client_sends_only_a_rounded_location_and_reads_the_forecast():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=OPEN_METEO)

    client = OpenMeteo("12.345678", "-98.765432", "America/New_York",
                       http=httpx.Client(transport=httpx.MockTransport(handler)))
    today = client.today()
    assert seen[0]["latitude"] == "12.35" and seen[0]["longitude"] == "-98.77"
    assert seen[0]["temperature_unit"] == "fahrenheit" and seen[0]["wind_speed_unit"] == "mph"
    assert today["code"] == 61 and today["high"] == 67.6 and today["rain_chance"] == 70
    assert today["hours"][15] == ("2026-10-12T15:00", 60)


@pytest.mark.parametrize("response", [
    httpx.Response(500),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"daily": {}}),
])
def test_bad_or_missing_answers_are_unavailable(response):
    client = OpenMeteo(1, 2, "UTC", http=httpx.Client(transport=httpx.MockTransport(lambda r: response)))
    with pytest.raises(WeatherUnavailable):
        client.today()


# The line

def test_dry_day():
    assert weather_line(forecast()) == "- Weather: Partly cloudy, high 68°F, low 51°F."


def test_rain_names_the_hours_and_peak_chance():
    line = weather_line(forecast(code=61, rain_chance=70, hours=AFTERNOON_RAIN))
    assert line == "- Weather: Light rain, high 68°F, low 51°F. Rain likely 3pm-7pm (70%)."


def test_small_chance_is_given_as_a_percentage_and_wind_when_strong():
    line = weather_line(forecast(rain_chance=30, wind=24.6))
    assert line.endswith("30% chance of rain. Windy, up to 25 mph.")


def test_wet_hours_ignores_the_night_and_splits_spans():
    chances = [90] * 6 + [0] * 3 + [80] + [0] * 8 + [60, 60] + [0] * 2 + [90, 90]  # 12am-5am, 9am, 6pm-7pm, 10pm-11pm
    assert wet_hours(hours(chances), 50) == ("9am-10am, 6pm-8pm, 10pm-11pm", 90)


# In the digest

class Chat:
    def complete(self, system, user, schema=None, temperature=None):
        return json.dumps({"category": "skip", "summary": "x", "due": None})


class FakeWeather:
    def __init__(self, up=True):
        self.up = up

    def today(self):
        if not self.up:
            raise WeatherUnavailable("down")
        return forecast(code=61, rain_chance=70, hours=AFTERNOON_RAIN)


def build_digest(tmp_path, weather):
    from services.digest.build import build
    from services.ingestion.store import Store

    config = yaml.safe_load(open("config/digest.yaml", encoding="utf-8"))
    return build(Store(tmp_path / "s.db"), Chat(), config, datetime(2026, 10, 12, 7, 0, tzinfo=TZ), TZ,
                 log=lambda _: None, weather=weather, weather_config={"rain_threshold": 50})


def test_forecast_leads_the_today_section(tmp_path):
    text = build_digest(tmp_path, FakeWeather())
    assert "**Today**\n- Weather: Light rain, high 68°F, low 51°F. Rain likely 3pm-7pm (70%)." in text


def test_forecast_outage_is_noted_and_no_weather_is_quiet(tmp_path):
    down = build_digest(tmp_path, FakeWeather(up=False))
    assert "Weather:" not in down and "Weather forecast unavailable" in down
    (tmp_path / "off").mkdir()
    assert "Weather" not in build_digest(tmp_path / "off", None)


def test_no_location_means_no_weather(monkeypatch):
    from services.weather.forecast import client_from_settings

    monkeypatch.delenv("WEATHER_LATITUDE", raising=False)
    monkeypatch.delenv("WEATHER_LONGITUDE", raising=False)
    assert client_from_settings() is None
    monkeypatch.setenv("WEATHER_LATITUDE", "12.3")
    monkeypatch.setenv("WEATHER_LONGITUDE", "-45.6")
    assert isinstance(client_from_settings(), OpenMeteo)
