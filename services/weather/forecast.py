"""Today's weather forecast for the morning digest, from Open-Meteo.

Open-Meteo is free and needs no account or key. What it receives is the
location (WEATHER_LATITUDE and WEATHER_LONGITUDE in .env, rounded to about
a kilometre) and nothing else. The forecast line is written in code, not by
a model, so its numbers are exactly the forecast's.
"""

import os

import httpx


class WeatherUnavailable(Exception):
    """Open-Meteo didn't answer, or answered with something unusable."""


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


class OpenMeteo:
    def __init__(self, latitude, longitude, timezone, url="https://api.open-meteo.com/v1/forecast",
                 units="fahrenheit", timeout=15, http=None):
        self.latitude = round(float(latitude), LOCATION_DECIMALS)
        self.longitude = round(float(longitude), LOCATION_DECIMALS)
        self.timezone, self.url, self.units = timezone, url, units
        self.http = http or httpx.Client(timeout=timeout)

    def today(self):
        """Today's forecast: the daily summary and the hourly chance of rain."""
        params = {
            "latitude": self.latitude, "longitude": self.longitude, "timezone": self.timezone, "forecast_days": 1,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "wind_speed_10m_max",
            "hourly": "precipitation_probability",
            "temperature_unit": self.units, "wind_speed_unit": "mph" if self.units == "fahrenheit" else "kmh",
        }
        try:
            response = self.http.get(self.url, params=params)
            response.raise_for_status()
            data = response.json()
            daily, hourly = data["daily"], data["hourly"]
            return {
                "code": daily["weather_code"][0],
                "high": daily["temperature_2m_max"][0],
                "low": daily["temperature_2m_min"][0],
                "rain_chance": daily["precipitation_probability_max"][0],
                "wind": daily["wind_speed_10m_max"][0],
                "hours": list(zip(hourly["time"], hourly["precipitation_probability"])),
                "units": "°F" if self.units == "fahrenheit" else "°C",
                "wind_units": "mph" if self.units == "fahrenheit" else "km/h",
            }
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            raise WeatherUnavailable(f"Open-Meteo didn't answer ({type(error).__name__})") from error


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


def weather_line(forecast, rain_threshold=50, windy_above=20):
    """One digest line, e.g. "Partly cloudy, high 68°F, low 51°F. Rain likely 3pm-7pm (70%)."."""
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
    return "- Weather: " + " ".join(parts)


def client_from_settings():
    """A client per config/weather.yaml, or None when it's off or no location is set in .env."""
    from services.common import settings

    config = settings.load_config("weather.yaml")
    latitude, longitude = os.environ.get("WEATHER_LATITUDE"), os.environ.get("WEATHER_LONGITUDE")
    if not config.get("enabled") or not latitude or not longitude:
        return None
    return OpenMeteo(latitude, longitude, settings.TIMEZONE.key, config["url"], config.get("units", "fahrenheit"),
                     config.get("timeout_seconds", 15))
