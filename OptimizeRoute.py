import json
import os
from urllib import error, request

API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
API_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def compute_route(
    api_key: str,
    origin: tuple[float, float],
    destination: tuple[float, float],
    stops: list[tuple[float, float]] = None,
    travel_mode: str = "DRIVE",
    optimize: bool = True,
) -> dict:
    if not api_key:
        raise ValueError(
            "Set GOOGLE_MAPS_API_KEY in your environment before running this script."
        )

    # Google requires at least 2 stops to perform waypoint optimization
    should_optimize = optimize and stops is not None and len(stops) >= 2

    # TRAFFIC_AWARE_OPTIMAL is incompatible with optimizeWaypointOrder
    routing_pref = "TRAFFIC_AWARE" if should_optimize else "TRAFFIC_AWARE_OPTIMAL"

    payload = {
        "origin": {
            "location": {
                "latLng": {
                    "latitude": origin[0],
                    "longitude": origin[1],
                }
            }
        },
        "destination": {
            "location": {
                "latLng": {
                    "latitude": destination[0],
                    "longitude": destination[1],
                }
            }
        },
        "travelMode": travel_mode,
        "routingPreference": routing_pref,
    }

    if stops:
        payload["intermediates"] = [
            {"location": {"latLng": {"latitude": lat, "longitude": lng}}}
            for lat, lng in stops
        ]
        if should_optimize:
            payload["optimizeWaypointOrder"] = True

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "routes.duration,routes.distanceMeters,"
            "routes.polyline.encodedPolyline,"
            "routes.optimizedIntermediateWaypointIndex"
        ),
    }

    req = request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Google Routes API request failed ({exc.code}):\n{body}"
        ) from exc


def get_route_duration(route_data: dict) -> float:
    """Extracts total duration in seconds from the route data."""
    try:
        duration_str = route_data["routes"][0]["duration"]  # E.g., "708s"
        return float(duration_str.rstrip("s"))
    except (KeyError, IndexError, ValueError, AttributeError) as exc:
        raise ValueError(
            "Invalid route data format: 'duration' not found or invalid."
        ) from exc


def convert_duration_to_minutes(duration_seconds: float) -> int:
    """Converts duration from seconds to minutes."""
    return int(duration_seconds // 60)


def convert_duration_to_hours(duration_minutes: float) -> int:
    """Converts duration from minutes to hours."""
    return int(duration_minutes // 60)


def get_route_distance(route_data: dict) -> float:
    """Extracts total distance in meters from the route data."""
    try:
        return float(route_data["routes"][0]["distanceMeters"])
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(
            "Invalid route data format: 'distanceMeters' not found."
        ) from exc


def get_optimized_stops_order(
    route_data: dict, original_stops: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Reorders original_stops into the optimal order returned by Google."""
    if not original_stops:
        return []
    try:
        order = route_data["routes"][0].get(
            "optimizedIntermediateWaypointIndex", []
        )
        if not order:
            return original_stops
        return [original_stops[i] for i in order]
    except (KeyError, IndexError):
        return original_stops


def optimize_route(origin, stops=None, api_key=None):
    """
    Computes the optimized route given an origin and a list of stops.
    Returns the route data including distance, duration, and optimized order of stops.
    """
    # Fetch dynamically to avoid binding empty strings on load
    key = api_key or os.getenv("GOOGLE_MAPS_API_KEY", "")

    try:
        route_data = compute_route(
            api_key=key,
            origin=origin,
            destination=origin,  # Round trip
            stops=stops,
            optimize=True,
        )
        distance = get_route_distance(route_data)
        duration = get_route_duration(route_data)
        duration_minutes = convert_duration_to_minutes(duration)
        duration_hours = 0
        if duration_minutes >= 60:
            duration_hours = convert_duration_to_hours(duration_minutes)
        duration_seconds = int(duration % 60)
        
        duration_string = f"{duration_hours} hours, {duration_minutes % 60} minutes, {duration_seconds} seconds"
        print(f"Total Round Trip Distance: {distance:.0f} meters ({distance/1000:.2f} km)")
        print(f"Total Duration: {duration_string}")
        
        ordered_stops = get_optimized_stops_order(route_data, stops or [])
        print("\nOptimal Sequence of Visited Stops:")
        for rank, stop in enumerate(ordered_stops, start=1):
            print(f"  Stop {rank}: {stop}")
            
        return route_data, ordered_stops
    except Exception as exc:
        print(f"Error optimizing route: {exc}")
        return None, None