import unittest
from pathlib import Path
import dashboard

REQUIRED_DOCS = (
    "README.md", "ARCHITECTURE.md", "SECURITY.md", "API_DOCUMENTATION.md",
    "BENCHMARK_METHODOLOGY.md", "TESTING.md", "DEPLOYMENT.md", "LIMITATIONS.md", "CHANGELOG.md",
)

class TestFrontendClaimsAndDocumentation(unittest.TestCase):
    def test_all_nine_documentation_files_exist_and_non_empty(self) -> None:
        for doc in REQUIRED_DOCS:
            p = Path(doc)
            self.assertTrue(p.exists(), f"Missing doc: {doc}")
            text = p.read_text(encoding="utf-8").strip()
            self.assertGreater(len(text), 150)
            self.assertNotIn("plx_live_growth_9482", text)

    def test_dashboard_html_labels_demo_claims_and_four_systems(self) -> None:
        html = dashboard.HTML_PAGE
        self.assertNotIn("plx_live_growth_9482", html)
        self.assertNotIn("localhost:8050", html)
        self.assertIn("https://parallix-iq-927980553219.asia-south1.run.app/v1/audit", html)
        self.assertIn("Demo Tenant", html)
        self.assertIn("Illustrative Enterprise Pricing", html)
        self.assertIn("Internal Benchmark Results", html)
        for pillar in ("UNDERSTAND", "VERIFY", "PROTECT", "IMPROVE"):
            self.assertIn(pillar, html)

if __name__ == "__main__":
    unittest.main()
