import os

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()
mcp = FastMCP("Weather MCP Server")
OPEN_WEATHER_API_KEY = os.getenv("OPEN_WEATHER_API_KEY")


def _error_response(response):
    try:
        data = response.json()
    except ValueError:
        data = {"message": response.text or "Weather provider returned invalid JSON"}
    return {"error": data.get("message", "Weather provider request failed"), "status": response.status_code}


@mcp.tool()
def get_current_weather(city: str):
    if not OPEN_WEATHER_API_KEY:
        return {"error": "OPEN_WEATHER_API_KEY is not configured"}
    try:
        response = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": OPEN_WEATHER_API_KEY, "units": "metric"},
            timeout=15,
        )
        if response.status_code != 200:
            return _error_response(response)
        data = response.json()
    except requests.RequestException as exc:
        return {"error": f"Weather provider request failed: {exc}"}
    except ValueError as exc:
        return {"error": f"Weather provider returned invalid JSON: {exc}"}
    return {
        "city": data["name"],
        "temperature_c": data["main"]["temp"],
        "feels_like_c": data["main"]["feels_like"],
        "humidity": data["main"]["humidity"],
        "condition": data["weather"][0]["description"],
        "wind_speed": data["wind"]["speed"],
    }


@mcp.tool()
def get_forecast(city: str):
    if not OPEN_WEATHER_API_KEY:
        return {"error": "OPEN_WEATHER_API_KEY is not configured"}
    try:
        response = requests.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={"q": city, "appid": OPEN_WEATHER_API_KEY, "units": "metric"},
            timeout=15,
        )
        if response.status_code != 200:
            return _error_response(response)
        data = response.json()
    except requests.RequestException as exc:
        return {"error": f"Forecast provider request failed: {exc}"}
    except ValueError as exc:
        return {"error": f"Forecast provider returned invalid JSON: {exc}"}
    return {
        "city": city,
        "forecast": [
            {
                "datetime": item["dt_txt"],
                "temperature": item["main"]["temp"],
                "weather": item["weather"][0]["description"],
            }
            for item in data.get("list", [])[:5]
        ],
    }


if __name__ == "__main__":
    mcp.run()
