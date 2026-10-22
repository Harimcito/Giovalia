"""Smoke test del dashboard Streamlit del Radar sin ticket ni servicios externos."""
import os
import tempfile
import unittest
from pathlib import Path


class RadarAppSmokeTests(unittest.TestCase):
    def test_dashboard_starts_without_api_ticket(self):
        from streamlit.testing.v1 import AppTest

        root = Path(__file__).resolve().parents[1]
        app_path = root / "app.py"
        if not app_path.is_file():
            app_path = root / "radar_comercial" / "app.py"
        old_data_dir = os.environ.get("GIOVAL_DATA_DIR")
        old_radar_data_dir = os.environ.get("RADAR_DATA_DIR")
        old_access = os.environ.get("GIOVAL_REQUIRE_ACCESS")
        old_password = os.environ.get("GIOVAL_ACCESS_PASSWORD")
        old_ticket = os.environ.get("CHILECOMPRA_API_TICKET")

        with tempfile.TemporaryDirectory(prefix="gioval-radar-smoke-") as tmp:
            os.environ["GIOVAL_DATA_DIR"] = tmp
            os.environ["RADAR_DATA_DIR"] = tmp
            os.environ.pop("GIOVAL_REQUIRE_ACCESS", None)
            os.environ.pop("GIOVAL_ACCESS_PASSWORD", None)
            os.environ.pop("CHILECOMPRA_API_TICKET", None)
            try:
                app = AppTest.from_file(str(app_path), default_timeout=25).run()
                self.assertEqual(
                    len(app.exception),
                    0,
                    "\n".join(str(error.value) for error in app.exception),
                )
            finally:
                if old_data_dir is None:
                    os.environ.pop("GIOVAL_DATA_DIR", None)
                else:
                    os.environ["GIOVAL_DATA_DIR"] = old_data_dir
                if old_radar_data_dir is None:
                    os.environ.pop("RADAR_DATA_DIR", None)
                else:
                    os.environ["RADAR_DATA_DIR"] = old_radar_data_dir
                if old_access is None:
                    os.environ.pop("GIOVAL_REQUIRE_ACCESS", None)
                else:
                    os.environ["GIOVAL_REQUIRE_ACCESS"] = old_access
                if old_password is None:
                    os.environ.pop("GIOVAL_ACCESS_PASSWORD", None)
                else:
                    os.environ["GIOVAL_ACCESS_PASSWORD"] = old_password
                if old_ticket is None:
                    os.environ.pop("CHILECOMPRA_API_TICKET", None)
                else:
                    os.environ["CHILECOMPRA_API_TICKET"] = old_ticket


if __name__ == "__main__":
    unittest.main()
