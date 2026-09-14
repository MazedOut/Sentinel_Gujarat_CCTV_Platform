"""
Routing / GIS service.

Provides road route inference between two geographic points.

CRITICAL LABEL DISTINCTION:
    CONFIRMED_OBSERVATION — camera actually detected the vehicle
    INFERRED_POSSIBLE_ROUTE — road route estimated between observations

These are ALWAYS different and NEVER conflated in data or UI.

Provider abstraction:
    RoutingService (interface)
        GoogleMapsRoutingService — uses Google Maps Directions API
        FallbackRoutingService   — straight-line distance (no API key needed)

Configuration:
    GOOGLE_MAPS_API_KEY in .env enables Google Maps provider.
    Without it, FallbackRoutingService is used automatically.
"""
from __future__ import annotations

import math
from typing import Optional
from backend.app.core.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Route result
# ---------------------------------------------------------------------------

class RouteResult:
    """
    Result of a route inference request.
    
    ALWAYS labelled as INFERRED_POSSIBLE_ROUTE.
    """
    
    def __init__(
        self,
        origin_camera: str,
        dest_camera: str,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        distance_m: float,
        duration_s: Optional[float],
        geometry: Optional[str],   # GeoJSON LineString or encoded polyline
        provider: str,
        route_type: str = "INFERRED_POSSIBLE_ROUTE",
        alternatives: Optional[list] = None,
    ):
        self.origin_camera = origin_camera
        self.dest_camera = dest_camera
        self.origin_lat = origin_lat
        self.origin_lon = origin_lon
        self.dest_lat = dest_lat
        self.dest_lon = dest_lon
        self.distance_m = distance_m
        self.duration_s = duration_s
        self.geometry = geometry
        self.provider = provider
        self.route_type = route_type  # ALWAYS "INFERRED_POSSIBLE_ROUTE"
        self.alternatives = alternatives or []

    def to_dict(self) -> dict:
        return {
            "route_type": self.route_type,
            "label": "INFERRED POSSIBLE ROUTE — not confirmed vehicle movement",
            "origin_camera": self.origin_camera,
            "destination_camera": self.dest_camera,
            "origin": {"lat": self.origin_lat, "lng": self.origin_lon},
            "destination": {"lat": self.dest_lat, "lng": self.dest_lon},
            "distance_meters": round(self.distance_m),
            "distance_km": round(self.distance_m / 1000, 2),
            "duration_seconds": self.duration_s,
            "duration_minutes": round(self.duration_s / 60, 1) if self.duration_s else None,
            "geometry": self.geometry,
            "provider": self.provider,
            "alternatives_count": len(self.alternatives),
        }


# ---------------------------------------------------------------------------
# Routing service interface
# ---------------------------------------------------------------------------

class RoutingService:
    """Abstract routing interface."""

    def infer_route(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        origin_camera: str = "",
        dest_camera: str = "",
    ) -> RouteResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Fallback: straight-line distance (always available)
# ---------------------------------------------------------------------------

class FallbackRoutingService(RoutingService):
    """
    Uses Haversine formula for straight-line distance.
    No API key required.
    Result is a direct line — NOT a road route.
    """

    def infer_route(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        origin_camera: str = "",
        dest_camera: str = "",
    ) -> RouteResult:
        dist_m = _haversine(origin_lat, origin_lon, dest_lat, dest_lon)

        # Create a simple 2-point GeoJSON LineString
        geometry = {
            "type": "LineString",
            "coordinates": [
                [origin_lon, origin_lat],
                [dest_lon, dest_lat],
            ]
        }
        import json
        geom_str = json.dumps(geometry)

        # Rough time estimate: assume 40 km/h average urban speed
        estimated_duration_s = (dist_m / 1000) / 40 * 3600

        return RouteResult(
            origin_camera=origin_camera,
            dest_camera=dest_camera,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            dest_lat=dest_lat,
            dest_lon=dest_lon,
            distance_m=dist_m,
            duration_s=estimated_duration_s,
            geometry=geom_str,
            provider="straight_line_fallback",
            route_type="INFERRED_POSSIBLE_ROUTE",
        )


# ---------------------------------------------------------------------------
# Google Maps provider
# ---------------------------------------------------------------------------

class GoogleMapsRoutingService(RoutingService):
    """
    Uses Google Maps Directions API for road routing.
    Requires GOOGLE_MAPS_API_KEY in .env.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key

    def infer_route(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        origin_camera: str = "",
        dest_camera: str = "",
    ) -> RouteResult:
        try:
            import httpx
            url = "https://maps.googleapis.com/maps/api/directions/json"
            params = {
                "origin": f"{origin_lat},{origin_lon}",
                "destination": f"{dest_lat},{dest_lon}",
                "key": self.api_key,
                "alternatives": "true",
            }
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            if data.get("status") != "OK":
                logger.warning(
                    "Google Maps Directions API returned status: %s. "
                    "Falling back to straight-line.",
                    data.get("status"),
                )
                return FallbackRoutingService().infer_route(
                    origin_lat, origin_lon, dest_lat, dest_lon,
                    origin_camera, dest_camera,
                )

            route = data["routes"][0]
            leg = route["legs"][0]
            dist_m = leg["distance"]["value"]
            dur_s = leg["duration"]["value"]
            polyline = route["overview_polyline"]["encoded"]

            # Build GeoJSON from encoded polyline
            points = _decode_polyline(polyline)
            geom = {
                "type": "LineString",
                "coordinates": [[lng, lat] for lat, lng in points]
            }
            import json
            geom_str = json.dumps(geom)

            # Collect alternatives
            alts = []
            for alt_route in data.get("routes", [])[1:]:
                alt_leg = alt_route["legs"][0]
                alts.append({
                    "distance_m": alt_leg["distance"]["value"],
                    "duration_s": alt_leg["duration"]["value"],
                    "summary": alt_route.get("summary", ""),
                })

            return RouteResult(
                origin_camera=origin_camera,
                dest_camera=dest_camera,
                origin_lat=origin_lat,
                origin_lon=origin_lon,
                dest_lat=dest_lat,
                dest_lon=dest_lon,
                distance_m=dist_m,
                duration_s=dur_s,
                geometry=geom_str,
                provider="google_maps",
                route_type="INFERRED_POSSIBLE_ROUTE",
                alternatives=alts,
            )

        except Exception as exc:
            logger.error("Google Maps routing failed: %s. Using fallback.", exc)
            return FallbackRoutingService().infer_route(
                origin_lat, origin_lon, dest_lat, dest_lon,
                origin_camera, dest_camera,
            )


# ---------------------------------------------------------------------------
# OSRM (Open Source Routing Machine) road routing provider
# ---------------------------------------------------------------------------

class OSRMRoutingService(RoutingService):
    """
    Uses Open Source Routing Machine (OSRM) driving API for real road corridors.
    Standard open routing provider for OpenStreetMap / Leaflet applications.
    Does not require a commercial API key.
    Falls back gracefully to FallbackRoutingService on timeout or failure.
    """

    def infer_route(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        origin_camera: str = "",
        dest_camera: str = "",
    ) -> RouteResult:
        try:
            import httpx
            import json
            # OSRM expects coordinates in {longitude},{latitude} format
            url = f"https://router.project-osrm.org/route/v1/driving/{origin_lon},{origin_lat};{dest_lon},{dest_lat}?overview=full&geometries=geojson"
            with httpx.Client(timeout=4.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == "Ok" and data.get("routes"):
                        route = data["routes"][0]
                        dist_m = float(route.get("distance", 0.0))
                        dur_s = float(route.get("duration", 0.0))
                        geom = route.get("geometry")
                        geom_str = json.dumps(geom) if isinstance(geom, dict) else str(geom)

                        return RouteResult(
                            origin_camera=origin_camera,
                            dest_camera=dest_camera,
                            origin_lat=origin_lat,
                            origin_lon=origin_lon,
                            dest_lat=dest_lat,
                            dest_lon=dest_lon,
                            distance_m=dist_m,
                            duration_s=dur_s,
                            geometry=geom_str,
                            provider="osrm_road_routing",
                            route_type="INFERRED_POSSIBLE_ROUTE",
                            alternatives=[],
                        )
        except Exception as exc:
            logger.warning("OSRM road routing unavailable or timed out: %s. Falling back to straight-line.", exc)

        return FallbackRoutingService().infer_route(
            origin_lat, origin_lon, dest_lat, dest_lon,
            origin_camera, dest_camera,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_routing_service: Optional[RoutingService] = None


def get_routing_service() -> RoutingService:
    """Returns the best available routing service."""
    global _routing_service
    if _routing_service is not None:
        return _routing_service

    from backend.app.core.config import settings
    if settings.google_maps_api_key:
        _routing_service = GoogleMapsRoutingService(settings.google_maps_api_key)
        logger.info("Routing: Google Maps provider active.")
    else:
        _routing_service = OSRMRoutingService()
        logger.info("Routing: OpenStreetMap / OSRM road routing active with Haversine fallback.")
    return _routing_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine formula — returns distance in meters."""
    R = 6371000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _decode_polyline(polyline: str) -> list[tuple[float, float]]:
    """Decode a Google Maps encoded polyline to list of (lat, lng) tuples."""
    points = []
    index = 0
    lat = 0
    lng = 0
    while index < len(polyline):
        result, shift = 0, 0
        while True:
            b = ord(polyline[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if result & 1 else result >> 1
        lat += dlat
        result, shift = 0, 0
        while True:
            b = ord(polyline[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if result & 1 else result >> 1
        lng += dlng
        points.append((lat / 1e5, lng / 1e5))
    return points
