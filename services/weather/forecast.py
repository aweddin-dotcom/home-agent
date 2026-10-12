"""Weather forecasts from Open-Meteo, for the morning digest and chat.

Open-Meteo is free and needs no account or key. For home it receives the
location (WEATHER_LATITUDE and WEATHER_LONGITUDE in .env, rounded to about
a kilometre); for a place named in a question ("weather in Boston?"), the
place name, looked up with its geocoding service. Nothing else is sent.
Forecast lines are written in code, not by a model, so their numbers are
exactly the forecast's.
"""

import os
from datetime import date

import httpx


class WeatherUnavailable(Exception):
    """Open-Meteo didn't answer, or answered with something unusable."""


class PlaceNotFound(Exception):
    """Open-Meteo's geocoding has no place by that name."""


# WMO weather codes, as Open-Meteo reports them.
CONDITIONS = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy", 45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Rain showers", 81: "Rain showers", 82: "Heavy rain showers", 85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorms", 96: "Thunderstorms with hail", 99: "Thunderstorms with hail",
}
LOCATION_DECIMALS = 2  # about a kilometre
MAX_DAYS = 16  # how far ahead Open-Meteo forecasts
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


class OpenMeteo:
    def __init__(self, latitude, longitude, timezone, url="https://api.open-meteo.com/v1/forecast",
                 units="fahrenheit", timeout=15, http=None, geocoding_url=GEOCODING_URL):
        self.latitude = round(float(latitude), LOCATION_DECIMALS)
        self.longitude = round(float(longitude), LOCATION_DECIMALS)
        self.timezone, self.url, self.units, self.geocoding_url = timezone, url, units, geocoding_url
        self.http = http or httpx.Client(timeout=timeout)

    def _get(self, url, params):
        try:
            response = self.http.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise WeatherUnavailable(f"Open-Meteo didn't answer ({type(error).__name__})") from error

    def days(self, count=1, latitude=None, longitude=None, timezone=None):
        """The forecast for `count` days from today, one dict per day: date,
        conditions code, high, low, chance of rain, wind, and the hourly
        chance of rain. Home unless a location is given."""
        imperial = self.units == "fahrenheit"
        data = self._get(self.url, {
            "latitude": self.latitude if latitude is None else latitude,
            "longitude": self.longitude if longitude is None else longitude,
            "timezone": timezone or self.timezone, "forecast_days": max(1, min(count, MAX_DAYS)),
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "wind_speed_10m_max",
            "hourly": "precipitation_probability",
            "temperature_unit": self.units, "wind_speed_unit": "mph" if imperial else "kmh",
        })
        try:
            daily, hourly = data["daily"], data["hourly"]
            hours = list(zip(hourly["time"], hourly["precipitation_probability"]))
            return [{
                "date": date.fromisoformat(day),
                "code": daily["weather_code"][i],
                "high": daily["temperature_2m_max"][i],
                "low": daily["temperature_2m_min"][i],
                "rain_chance": daily["precipitation_probability_max"][i],
                "wind": daily["wind_speed_10m_max"][i],
                "hours": [(t, c) for t, c in hours if t.startswith(day)],
                "units": "°F" if imperial else "°C",
                "wind_units": "mph" if imperial else "km/h",
            } for i, day in enumerate(daily["time"])]
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
            raise WeatherUnavailable(f"Open-Meteo's forecast was unusable ({type(error).__name__})") from error

    def today(self):
        """Today's forecast at home."""
        days = self.days(1)
        if not days:
            raise WeatherUnavailable("Open-Meteo's forecast had no days")
        return days[0]

    def locate(self, place):
        """(latitude, longitude, timezone, "Boston, Massachusetts, United States")
        for a place name. "Boston, MA" is looked up as "Boston" if the full
        name isn't found."""
        names = [place] + ([place.split(",")[0]] if "," in place else [])
        for name in names:
            data = self._get(self.geocoding_url, {"name": name.strip(), "count": 1, "language": "en",
                                                  "format": "json"})
            results = data.get("results") if isinstance(data, dict) else None
            if results:
                r = results[0]
                label = ", ".join(p for p in (r.get("name"), r.get("admin1"), r.get("country")) if p)
                return r["latitude"], r["longitude"], r.get("timezone") or "auto", label
        raise PlaceNotFound(place)


def _hour(hour):
    """15 -> "3pm"."""
    return f"{hour % 12 or 12}{'am' if hour % 24 < 12 else 'pm'}"


def wet_hours(hours, threshold, first=7, last=22):
    """The waking hours when rain is at least `threshold`% likely, as
    "3pm-7pm" spans, and the highest chance among them."""
    spans, peak = [], 0  # [start hour, end hour (exclusive)]
    for stamp, chance in hours:
        hour = int(stamp[11:13])
        if first <= hour <= last and chance is not None and chance >= threshold:
            peak = max(peak, chance)
            if spans and spans[-1][1] == hour:
                spans[-1][1] = hour + 1
            else:
                spans.append([hour, hour + 1])
    return ", ".join(f"{_hour(a)}-{_hour(b)}" for a, b in spans), peak


def describe(forecast, rain_threshold=50, windy_above=20):
    """A day's forecast in words, e.g. "Partly cloudy, high 68°F, low 51°F. Rain likely 3pm-7pm (70%)."."""
    def num(value):
        return f"{round(value)}"

    u = forecast["units"]
    parts = [f"{CONDITIONS.get(forecast['code'], 'Mixed')}, high {num(forecast['high'])}{u}, "
             f"low {num(forecast['low'])}{u}."]
    when, peak = wet_hours(forecast["hours"], rain_threshold)
    if when:
        parts.append(f"Rain likely {when} ({peak}%).")
    elif forecast["rain_chance"]:
        parts.append(f"{forecast['rain_chance']}% chance of rain.")
    if forecast["wind"] is not None and forecast["wind"] >= windy_above:
        parts.append(f"Windy, up to {num(forecast['wind'])} {forecast['wind_units']}.")
    return " ".join(parts)


def weather_line(forecast, rain_threshold=50, windy_above=20):
    """The digest's line: "- Weather: Partly cloudy, high 68°F, ..."."""
    return "- Weather: " + describe(forecast, rain_threshold, windy_above)


def client_from_settings():
    """A client per config/weather.yaml, or None when it's off or no location is set in .env."""
    from services.common import settings

    config = settings.load_config("weather.yaml")
    latitude, longitude = os.environ.get("WEATHER_LATITUDE"), os.environ.get("WEATHER_LONGITUDE")
    if not config.get("enabled") or not latitude or not longitude:
        return None
    return OpenMeteo(latitude, longitude, settings.TIMEZONE.key, config["url"], config.get("units", "fahrenheit"),
                     config.get("timeout_seconds", 15), geocoding_url=config.get("geocoding_url", GEOCODING_URL))
