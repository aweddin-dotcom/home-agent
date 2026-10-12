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


# In chat

from datetime import date  # noqa: E402

from services.retrieval.ask import answer_prompt, format_sources, gather  # noqa: E402
from services.retrieval.router import validate  # noqa: E402
from services.weather.forecast import PlaceNotFound  # noqa: E402

TODAY = date(2026, 10, 12)


class RouterChat:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user, schema=None, temperature=None):
        return json.dumps(self.reply)


class NoSearch:
    def embed_query(self, text):
        raise AssertionError("email was searched")


def route(sources=("weather",), start=None, end=None, place=None):
    return {"sources": list(sources), "start_date": start, "end_date": end, "calendar_keywords": [],
            "weather_place": place}


class FakeForecasts:
    """Ten days of invented forecasts; records what was asked."""

    def __init__(self, up=True, places=("Springfield",)):
        self.up, self.places, self.calls = up, places, []

    def days(self, count=1, latitude=None, longitude=None, timezone=None):
        self.calls.append(("days", count, latitude))
        if not self.up:
            raise WeatherUnavailable("down")
        return [forecast(date=date(2026, 10, 12 + i), high=60 + i, code=2) for i in range(min(count, 10))]

    def locate(self, place):
        self.calls.append(("locate", place))
        if place not in self.places:
            raise PlaceNotFound(place)
        return 39.8, -89.6, "America/Chicago", f"{place}, Illinois, United States"


def ask(question, reply, weather):
    return gather(question, NoSearch(), None, RouterChat(reply), 5, [], TODAY, TZ, weather=weather)


def test_weather_question_gets_today_and_tomorrow_at_home():
    weather = FakeForecasts()
    context = ask("what's the weather like?", route(), weather)
    assert weather.calls == [("days", 2, None)]
    assert context.sections == ["Weather forecast from Open-Meteo for the user's home:\n"
                                "- Mon Oct 12: Partly cloudy, high 60°F, low 51°F.\n"
                                "- Tue Oct 13: Partly cloudy, high 61°F, low 51°F."]
    assert "Weather: Open-Meteo forecast for your home, Oct 12 to Oct 13" in format_sources(context, TZ)
    system, _ = answer_prompt("q", context, TODAY)
    assert "use its figures exactly as given" in system and "don't add any like [1]" in system


def test_dates_asked_about_are_used():
    context = ask("will it rain this weekend?", route(start="2026-10-17", end="2026-10-18"), FakeForecasts())
    lines = context.sections[0].splitlines()[1:]
    assert [line[:12] for line in lines] == ["- Sat Oct 17", "- Sun Oct 18"]


def test_a_named_place_is_looked_up():
    weather = FakeForecasts()
    context = ask("weather in Springfield tomorrow?", route(start="2026-10-13", end="2026-10-13",
                                                           place="Springfield"), weather)
    assert weather.calls == [("locate", "Springfield"), ("days", 2, 39.8)]
    assert context.sections[0].startswith("Weather forecast from Open-Meteo for Springfield, Illinois, United States:")
    assert context.sections[0].count("\n- ") == 1  # just tomorrow


def test_unknown_place_and_outage_are_said_plainly():
    assert "doesn't know a place called" in ask("weather in Nowhereville?", route(place="Nowhereville"),
                                                FakeForecasts()).sections[0]
    down = ask("weather?", route(), FakeForecasts(up=False))
    assert "didn't answer" in down.sections[0] and "Weather: not available" in format_sources(down, TZ)


def test_no_home_location_says_how_to_set_it_up():
    context = ask("weather?", route(), None)
    assert "WEATHER_LATITUDE" in context.sections[0]


def test_weather_words_catch_a_route_to_the_web():
    weather = FakeForecasts()
    context = ask("do I need an umbrella today?", route(sources=["web"]), weather)
    assert context.route.sources == ["weather"] and weather.calls


def test_earnings_forecast_is_not_weather():
    context = gather("what's the earnings forecast for NWBX?", NoSearch(), None, RouterChat(route(sources=["general"])),
                     5, [], TODAY, TZ, weather=FakeForecasts())
    assert context.route.sources == ["general"]


def test_router_place_only_for_weather_and_never_with_web():
    assert validate(route(place="Springfield")).weather_place == "Springfield"
    assert validate(route(place="home")).weather_place is None
    assert validate(route(sources=["web"], place="Springfield")).weather_place is None
    assert validate(route(sources=["weather", "web"])).sources == ["weather"]


def test_client_finds_places_and_splits_days():
    def handler(request):
        if "geocoding" in request.url.host:
            name = request.url.params["name"]
            return httpx.Response(200, json={"results": [{"name": "Springfield", "admin1": "Illinois",
                                                          "country": "United States", "latitude": 39.8,
                                                          "longitude": -89.64, "timezone": "America/Chicago"}]}
                                  if name == "Springfield" else {})
        two_days = hours([0] * 24) + [(f"2026-10-13T{h:02d}:00", 80) for h in range(24)]
        return httpx.Response(200, json={
            "daily": {"time": ["2026-10-12", "2026-10-13"], "weather_code": [0, 63], "temperature_2m_max": [70, 60],
                      "temperature_2m_min": [50, 45], "precipitation_probability_max": [0, 80],
                      "wind_speed_10m_max": [5, 9]},
            "hourly": {"time": [t for t, _ in two_days], "precipitation_probability": [c for _, c in two_days]}})

    client = OpenMeteo(1, 2, "America/New_York", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.locate("Springfield, IL") == (39.8, -89.64, "America/Chicago", "Springfield, Illinois, United States")
    with pytest.raises(PlaceNotFound):
        client.locate("Nowhereville")
    days = client.days(2)
    assert [d["date"] for d in days] == [date(2026, 10, 12), date(2026, 10, 13)]
    assert len(days[1]["hours"]) == 24 and days[1]["hours"][0] == ("2026-10-13T00:00", 80)
