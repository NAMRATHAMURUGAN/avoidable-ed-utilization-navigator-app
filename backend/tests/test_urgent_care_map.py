"""Isolated tests for the live urgent-care map API boundary."""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

import requests

from backend.app import create_app
from backend.services import urgent_care_map_service as map_service


class UrgentCareMapRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    @patch("backend.routes.navigation.discover_urgent_care_facilities")
    def test_facility_search_returns_normalized_urgent_care_results(self, discover: Mock) -> None:
        discover.return_value = {
            "origin": {"latitude": 41.88, "longitude": -87.63},
            "radiusMeters": 5000,
            "radiusCapped": False,
            "facilities": [
                {
                    "id": "node/7",
                    "name": "Downtown Urgent Care",
                    "type": "urgent_care",
                    "latitude": 41.89,
                    "longitude": -87.64,
                    "distanceMeters": 1200,
                }
            ],
        }
        response = self.client.get(
            "/api/navigation/urgent-care/facilities?latitude=41.88&longitude=-87.63"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["facilities"][0]["type"], "urgent_care")
        discover.assert_called_once_with("41.88", "-87.63", None)

    def test_invalid_coordinates_are_rejected(self) -> None:
        response = self.client.get(
            "/api/navigation/urgent-care/facilities?latitude=91&longitude=-87.63"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Latitude", response.get_json()["error"])

    def test_missing_coordinates_are_rejected(self) -> None:
        response = self.client.get("/api/navigation/urgent-care/facilities?latitude=41.88")
        self.assertEqual(response.status_code, 400)

    def test_radius_is_safely_capped(self) -> None:
        radius, capped = map_service.validated_radius(999_999)
        self.assertEqual(radius, map_service.MAX_RADIUS_METERS)
        self.assertTrue(capped)

    @patch("backend.services.urgent_care_map_service.requests.post")
    def test_search_filters_non_urgent_clinics_and_returns_empty_when_needed(self, post: Mock) -> None:
        upstream = Mock()
        upstream.json.return_value = {
            "elements": [
                {"type": "node", "id": 1, "lat": 41.9, "lon": -87.6, "tags": {"name": "Clinic"}},
                {
                    "type": "node",
                    "id": 2,
                    "lat": 41.91,
                    "lon": -87.61,
                    "tags": {"name": "Downtown Urgent Care", "opening_hours": "Mo-Fr 09:00-17:00"},
                },
            ]
        }
        post.return_value = upstream
        result = map_service.discover_urgent_care_facilities(41.88, -87.63)
        self.assertEqual([facility["id"] for facility in result["facilities"]], ["node/2"])
        self.assertEqual(result["facilities"][0]["openingHours"], "Mo-Fr 09:00-17:00")

    @patch("backend.services.urgent_care_map_service.requests.post")
    def test_overpass_failure_is_safe(self, post: Mock) -> None:
        post.side_effect = requests.Timeout()
        with self.assertRaisesRegex(map_service.UrgentCareMapError, "temporarily unavailable"):
            map_service.discover_urgent_care_facilities(41.88, -87.63)

    @patch("backend.routes.navigation.calculate_urgent_care_route")
    def test_route_returns_normalized_ors_response_without_key(self, route: Mock) -> None:
        route.return_value = {
            "distanceMeters": 1800,
            "durationSeconds": 420,
            "geometry": {"type": "LineString", "coordinates": [[-87.63, 41.88], [-87.64, 41.89]]},
        }
        response = self.client.post(
            "/api/navigation/urgent-care/route",
            json={
                "origin": {"latitude": 41.88, "longitude": -87.63},
                "destination": {"latitude": 41.89, "longitude": -87.64},
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("OPENROUTESERVICE_API_KEY", response.get_data(as_text=True))

    @patch("backend.routes.navigation.calculate_urgent_care_route")
    def test_route_failure_and_rate_limit_are_safe(self, route: Mock) -> None:
        payload = {
            "origin": {"latitude": 41.88, "longitude": -87.63},
            "destination": {"latitude": 41.89, "longitude": -87.64},
        }
        route.side_effect = map_service.UrgentCareMapError("Routing service is temporarily unavailable.")
        unavailable = self.client.post("/api/navigation/urgent-care/route", json=payload)
        self.assertEqual(unavailable.status_code, 503)
        route.side_effect = map_service.UrgentCareMapError(
            "Routing service is busy. Please try again shortly.", 429
        )
        limited = self.client.post("/api/navigation/urgent-care/route", json=payload)
        self.assertEqual(limited.status_code, 429)

    @patch("backend.services.urgent_care_map_service.requests.post")
    def test_ors_request_keeps_key_server_side(self, post: Mock) -> None:
        upstream = Mock(status_code=200)
        upstream.json.return_value = {
            "features": [
                {
                    "properties": {"summary": {"distance": 1800, "duration": 420}},
                    "geometry": {"type": "LineString", "coordinates": [[-87.63, 41.88], [-87.64, 41.89]]},
                }
            ]
        }
        post.return_value = upstream
        with patch.dict(os.environ, {"OPENROUTESERVICE_API_KEY": "test-secret"}, clear=False):
            result = map_service.calculate_urgent_care_route(
                {"latitude": 41.88, "longitude": -87.63},
                {"latitude": 41.89, "longitude": -87.64},
            )
        self.assertEqual(result["distanceMeters"], 1800)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "test-secret")


if __name__ == "__main__":
    unittest.main()
