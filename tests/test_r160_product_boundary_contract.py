try:
    import tomllib
except ImportError:  # pragma: no cover
    # IMPORTANT: keep the Python 3.10 fallback; WSL pre-push still validates on 3.10.
    import tomli as tomllib
import unittest
from pathlib import Path

from services.product_boundary import get_product_boundary_contract

ROOT = Path(__file__).resolve().parents[1]
ADR_PATH = ROOT / "docs" / "adr" / "ADR-0002-product-boundary-and-packaging-contract.md"
README_PATH = ROOT / "README.md"
CONNECTOR_DOC_PATH = ROOT / "docs" / "connector.md"


class TestR160ProductBoundaryContract(unittest.TestCase):
    def test_contract_terms_and_topologies_are_stable(self):
        contract = get_product_boundary_contract()

        self.assertEqual(contract["package_name"], "comfyui-openclaw")
        self.assertEqual(
            contract["primary_distribution"]["id"],
            "comfyui_custom_node_pack",
        )
        self.assertEqual(
            [item["label"] for item in contract["supported_identities"]],
            [
                "ComfyUI custom node pack",
                "embedded operator platform",
                "connector-capable control surface",
            ],
        )
        self.assertEqual(
            [item["id"] for item in contract["supported_topologies"]],
            [
                "embedded_local",
                "embedded_split_control_plane",
                "embedded_with_connector_sidecar",
            ],
        )
        self.assertEqual(
            [item["id"] for item in contract["attached_subsystems"]],
            ["connector_sidecar"],
        )

    def test_contract_entrypoints_exist_in_repo(self):
        contract = get_product_boundary_contract()

        for section in ("core_subsystems", "attached_subsystems"):
            for subsystem in contract[section]:
                for rel_path in subsystem["entrypoints"]:
                    self.assertTrue(
                        (ROOT / rel_path).exists(),
                        f"Missing contract entrypoint: {rel_path}",
                    )

    def test_contract_matches_pyproject_package_name(self):
        with (ROOT / "pyproject.toml").open("rb") as fh:
            pyproject = tomllib.load(fh)

        contract = get_product_boundary_contract()
        self.assertEqual(contract["package_name"], pyproject["project"]["name"])

    def test_docs_reference_boundary_terms(self):
        adr_text = ADR_PATH.read_text(encoding="utf-8")
        readme_text = README_PATH.read_text(encoding="utf-8")
        connector_text = CONNECTOR_DOC_PATH.read_text(encoding="utf-8")

        for phrase in (
            "ComfyUI custom node pack",
            "embedded operator platform",
            "connector-capable control surface",
        ):
            self.assertIn(phrase, adr_text)
            self.assertIn(phrase, readme_text)

        self.assertIn("optional attached subsystem", adr_text)
        self.assertIn("optional attached subsystem", connector_text)


if __name__ == "__main__":
    unittest.main()
