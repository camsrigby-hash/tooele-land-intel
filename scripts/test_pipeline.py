"""
test_pipeline.py — Unit tests for gp_pdf_extract.py (Phase 18b-2 v2).

Run with:
  python scripts/test_pipeline.py
  python -m pytest scripts/test_pipeline.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure scripts/ is importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).parent))

import gp_pdf_extract as gp


# ---------------------------------------------------------------------------
# Schema / constants
# ---------------------------------------------------------------------------

class TestLayerTypeConstants(unittest.TestCase):

    def test_future_layer_types_contains_expected(self):
        expected = {"flu", "annexation_proposed", "annexation_existing_future",
                    "annexation_existing_city", "mpc_overlay", "other"}
        self.assertEqual(gp.FUTURE_LAYER_TYPES, expected)

    def test_annexation_layer_types_subset(self):
        self.assertTrue(gp.ANNEXATION_LAYER_TYPES.issubset(gp.FUTURE_LAYER_TYPES))

    def test_layer_type_to_future_layer_type_flu(self):
        self.assertEqual(gp._layer_type_to_future_layer_type("flu"), "flu")

    def test_layer_type_to_future_layer_type_annexation(self):
        self.assertEqual(
            gp._layer_type_to_future_layer_type("annexation"), "annexation_proposed"
        )

    def test_layer_type_to_future_layer_type_mpc(self):
        self.assertEqual(gp._layer_type_to_future_layer_type("mpc"), "mpc_overlay")

    def test_layer_type_to_future_layer_type_unknown(self):
        self.assertEqual(gp._layer_type_to_future_layer_type("gibberish"), "other")


# ---------------------------------------------------------------------------
# Schema validation helpers
# ---------------------------------------------------------------------------

def _make_flu_feature(city_slug="erda") -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        "properties": {
            "city_slug": city_slug,
            "city_name": "Erda",
            "future_layer_type": "flu",
            "layer_subtype": None,
            "gp_zone_code": "R-1",
            "gp_zone_description": "Low Density Residential",
            "gp_zone_normalized": "future_low_density_residential",
            "acreage": None,
            "jurisdiction": "tooele_county_ut",
            "source_pdf_url": "https://example.com/gp.pdf",
            "source_page_id": "page_0",
            "extraction_method": "anthropic_vision_claude_opus_4_7_georeferenced",
            "confidence": "anchored_approximation",
            "transform_residual_ft": 45.0,
            "n_control_points": 6,
            "extraction_date": "2026-05-14",
        },
    }


def _make_annexation_feature() -> dict:
    f = _make_flu_feature()
    f["properties"]["future_layer_type"] = "annexation_proposed"
    f["properties"]["layer_subtype"] = "2030 Future Annexation Boundary"
    f["properties"]["gp_zone_code"] = None
    f["properties"]["gp_zone_description"] = None
    f["properties"]["gp_zone_normalized"] = None
    return f


class TestSchemaValidation(unittest.TestCase):
    """Validate that feature properties conform to the v2 schema."""

    REQUIRED_FIELDS = {
        "city_slug", "city_name", "future_layer_type", "layer_subtype",
        "gp_zone_code", "gp_zone_description", "gp_zone_normalized",
        "acreage", "jurisdiction", "source_pdf_url", "source_page_id",
        "extraction_method", "confidence", "transform_residual_ft",
        "n_control_points", "extraction_date",
    }

    def _validate_feature(self, feature: dict) -> None:
        props = feature["properties"]
        missing = self.REQUIRED_FIELDS - set(props.keys())
        self.assertFalse(missing, f"Missing schema fields: {missing}")
        self.assertIn(
            props["future_layer_type"], gp.FUTURE_LAYER_TYPES,
            f"Invalid future_layer_type: {props['future_layer_type']}"
        )

    def test_flu_feature_has_zone_fields(self):
        f = _make_flu_feature()
        self._validate_feature(f)
        self.assertIsNotNone(f["properties"]["gp_zone_code"])
        self.assertIsNotNone(f["properties"]["gp_zone_description"])
        self.assertIsNotNone(f["properties"]["gp_zone_normalized"])

    def test_annexation_feature_zone_fields_nullable(self):
        f = _make_annexation_feature()
        self._validate_feature(f)
        self.assertIsNone(f["properties"]["gp_zone_code"])
        self.assertIsNone(f["properties"]["gp_zone_description"])
        self.assertIsNone(f["properties"]["gp_zone_normalized"])
        self.assertIn(
            f["properties"]["future_layer_type"], gp.ANNEXATION_LAYER_TYPES
        )

    def test_flu_zone_normalized_in_known_classes(self):
        f = _make_flu_feature()
        self.assertIn(
            f["properties"]["gp_zone_normalized"], gp.GP_ZONE_NORMALIZED_CLASSES
        )


# ---------------------------------------------------------------------------
# Multi-PDF aggregation
# ---------------------------------------------------------------------------

class TestMultiPdfAggregation(unittest.TestCase):
    """
    Validate that run_pipeline correctly aggregates features from multiple PDFs
    into a single output GeoJSON without data loss or cross-contamination.

    Uses mocked stage functions so no real PDFs or API calls are needed.
    """

    def _make_projected_record(self, zone_code: str, source_url: str, page_idx: int) -> dict:
        from shapely.geometry import Polygon as ShapelyPolygon
        coords = [(0.0, 0.0), (0.001, 0.0), (0.001, 0.001), (0.0, 0.0)]
        return {
            "zone_code": zone_code,
            "zone_description": f"{zone_code} zone",
            "px_polygon": [[0, 0], [10, 0], [10, 10]],
            "projected_coords": coords,
            "shapely_polygon": ShapelyPolygon(coords),
            "_source_pdf_url": source_url,
            "_source_page_id": f"page_{page_idx}",
            "_future_layer_type": "flu",
            "_layer_subtype": None,
        }

    @patch("gp_pdf_extract.stage1_rasterize")
    @patch("gp_pdf_extract.find_map_page_hints")
    @patch("gp_pdf_extract.stage2_identify_control_points")
    @patch("gp_pdf_extract.stage3_ground_truth_lookup")
    @patch("gp_pdf_extract.stage4_fit_affine")
    @patch("gp_pdf_extract.stage5_validate")
    @patch("gp_pdf_extract.stage6_extract_polygons")
    @patch("gp_pdf_extract.stage7_project_polygons")
    @patch("gp_pdf_extract.download_pdf")
    @patch("gp_pdf_extract.dump_api_log")
    @patch("gp_pdf_extract.write_transform_validation")
    def test_two_pdfs_aggregated_to_single_geojson(
        self,
        mock_write_val, mock_dump_log, mock_download,
        mock_stage7, mock_stage6, mock_stage5, mock_stage4,
        mock_stage3, mock_stage2, mock_hints, mock_stage1,
    ):
        import numpy as np

        # Stubs
        mock_stage1.return_value = [MagicMock(stem="_page_000", stat=lambda: MagicMock(st_size=500_000))]
        mock_hints.return_value = []
        mock_stage2.return_value = ([], 0)
        mock_stage3.return_value = [
            {"px_x": 100.0, "px_y": 100.0, "street_a": "Main", "street_b": "Center",
             "conf": "high", "gt_lat": 40.60, "gt_lon": -112.45},
            {"px_x": 200.0, "px_y": 100.0, "street_a": "State", "street_b": "1st",
             "conf": "high", "gt_lat": 40.61, "gt_lon": -112.44},
            {"px_x": 100.0, "px_y": 200.0, "street_a": "Oak", "street_b": "2nd",
             "conf": "high", "gt_lat": 40.59, "gt_lon": -112.46},
            {"px_x": 200.0, "px_y": 200.0, "street_a": "Elm", "street_b": "3rd",
             "conf": "high", "gt_lat": 40.58, "gt_lon": -112.43},
        ]
        A = np.array([[1e-5, 0, -112.45], [0, -1e-5, 40.60]])
        mock_stage4.return_value = A
        mock_stage5.return_value = (30.0, mock_stage3.return_value)
        mock_stage6.return_value = (
            [{"code": "R", "description": "Residential"}],
            [{"zone_code": "R", "zone_description": "Residential", "px_polygon": [[0,0],[10,0],[10,10]]}],
        )
        mock_write_val.return_value = Path("/tmp/val.md")
        mock_dump_log.return_value = Path("/tmp/log.jsonl")
        mock_download.side_effect = lambda url, _dir: Path(f"/tmp/{url.split('/')[-1]}")

        # Two PDFs → two separate batches of projected records
        pdf_urls = ["https://example.com/gp_section1.pdf", "https://example.com/gp_section2.pdf"]
        rec_pdf1 = self._make_projected_record("R", pdf_urls[0], 2)
        rec_pdf2 = self._make_projected_record("C", pdf_urls[1], 1)
        mock_stage7.side_effect = [[rec_pdf1], [rec_pdf2]]

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            result = gp.run_pipeline(
                city_slug="grantsville",
                pdf_source=None,
                out_dir=out_dir,
                api_key="test-key",
                pdf_sources=pdf_urls,
                layer_type="flu",
            )

            # Pipeline should report both features
            self.assertEqual(result["n_features"], 2)
            self.assertEqual(result["pdf_count"], 2)

            # Output GeoJSON should exist and contain 2 features
            geojson_path = Path(result["geojson_path"])
            self.assertTrue(geojson_path.exists(), "GeoJSON output file not written")
            fc = json.loads(geojson_path.read_text())
            self.assertEqual(fc["type"], "FeatureCollection")
            self.assertEqual(len(fc["features"]), 2, "Expected 2 features in aggregated output")

            # Each feature should carry its source PDF URL
            source_urls = {f["properties"]["source_pdf_url"] for f in fc["features"]}
            self.assertEqual(source_urls, set(pdf_urls))

    @patch("gp_pdf_extract.stage1_rasterize")
    @patch("gp_pdf_extract.find_map_page_hints")
    @patch("gp_pdf_extract.stage2_identify_control_points")
    @patch("gp_pdf_extract.stage3_ground_truth_lookup")
    @patch("gp_pdf_extract.stage4_fit_affine")
    @patch("gp_pdf_extract.stage5_validate")
    @patch("gp_pdf_extract.stage6_extract_polygons")
    @patch("gp_pdf_extract.stage7_project_polygons")
    @patch("gp_pdf_extract.download_pdf")
    @patch("gp_pdf_extract.dump_api_log")
    @patch("gp_pdf_extract.write_transform_validation")
    def test_single_pdf_backward_compat(
        self,
        mock_write_val, mock_dump_log, mock_download,
        mock_stage7, mock_stage6, mock_stage5, mock_stage4,
        mock_stage3, mock_stage2, mock_hints, mock_stage1,
    ):
        """Single-PDF path (--layer-type flu) must produce identical schema to old output."""
        import numpy as np

        cps = [
            {"px_x": 100.0, "px_y": 100.0, "street_a": "Main", "street_b": "Center",
             "conf": "high", "gt_lat": 40.60, "gt_lon": -112.45},
            {"px_x": 200.0, "px_y": 100.0, "street_a": "State", "street_b": "1st",
             "conf": "high", "gt_lat": 40.61, "gt_lon": -112.44},
            {"px_x": 100.0, "px_y": 200.0, "street_a": "Oak", "street_b": "2nd",
             "conf": "high", "gt_lat": 40.59, "gt_lon": -112.46},
            {"px_x": 200.0, "px_y": 200.0, "street_a": "Elm", "street_b": "3rd",
             "conf": "high", "gt_lat": 40.58, "gt_lon": -112.43},
        ]
        mock_stage1.return_value = [MagicMock(stem="_page_000", stat=lambda: MagicMock(st_size=500_000))]
        mock_hints.return_value = []
        mock_stage2.return_value = (cps, 0)
        mock_stage3.return_value = cps
        A = np.array([[1e-5, 0, -112.45], [0, -1e-5, 40.60]])
        mock_stage4.return_value = A
        mock_stage5.return_value = (30.0, cps)
        mock_stage6.return_value = (
            [{"code": "AG", "description": "Agriculture"}],
            [{"zone_code": "AG", "zone_description": "Agriculture", "px_polygon": [[0,0],[10,0],[10,10]]}],
        )
        mock_write_val.return_value = Path("/tmp/val.md")
        mock_dump_log.return_value = Path("/tmp/log.jsonl")
        mock_download.return_value = Path("/tmp/erda_gp.pdf")

        pdf_url = "https://erda.gov/wp-content/uploads/2022/08/Erda-General-Plan_2022-06-23.pdf"
        from shapely.geometry import Polygon as ShapelyPolygon
        coords = [(0.0, 0.0), (0.001, 0.0), (0.001, 0.001), (0.0, 0.0)]
        rec = {
            "zone_code": "AG", "zone_description": "Agriculture",
            "px_polygon": [[0,0],[10,0],[10,10]],
            "projected_coords": coords,
            "shapely_polygon": ShapelyPolygon(coords),
            "_source_pdf_url": pdf_url,
            "_source_page_id": "page_0",
            "_future_layer_type": "flu",
            "_layer_subtype": None,
        }
        mock_stage7.return_value = [rec]

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            result = gp.run_pipeline(
                city_slug="erda",
                pdf_source=pdf_url,
                out_dir=out_dir,
                api_key="test-key",
                layer_type="flu",
            )

            self.assertEqual(result["pdf_count"], 1)
            geojson_path = Path(result["geojson_path"])
            fc = json.loads(geojson_path.read_text())
            self.assertEqual(len(fc["features"]), 1)
            props = fc["features"][0]["properties"]

            # New required fields present
            self.assertIn("future_layer_type", props)
            self.assertEqual(props["future_layer_type"], "flu")
            self.assertIn("layer_subtype", props)
            self.assertIn("acreage", props)
            self.assertIn("source_pdf_url", props)

            # Zone code fields populated for FLU
            self.assertIsNotNone(props["gp_zone_code"])
            self.assertIsNotNone(props["gp_zone_normalized"])


if __name__ == "__main__":
    unittest.main()
