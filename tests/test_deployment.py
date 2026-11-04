"""Static deployment guardrails for the self-hosted single-user hub."""
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_compose_requires_secret_and_binds_loopback_only(self):
        compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
        hub = compose["services"]["hub"]
        self.assertIn("AGENT_TEAM_ACCESS_TOKEN", hub["environment"])
        self.assertIn(":?", hub["environment"]["AGENT_TEAM_ACCESS_TOKEN"])
        self.assertTrue(hub["read_only"])
        self.assertEqual(hub["cap_drop"], ["ALL"])
        self.assertTrue(all(str(port).startswith("127.0.0.1:") for port in hub["ports"]))
        self.assertEqual(hub["volumes"], ["agentteam_data:/data"])

    def test_dockerfile_runs_as_non_root_and_does_not_copy_secrets(self):
        df = (ROOT / "Dockerfile").read_text()
        ignored = (ROOT / ".dockerignore").read_text()
        self.assertIn("USER 10001:10001", df)
        self.assertIn("HEALTHCHECK", df)
        self.assertIn(".env", ignored)
        self.assertIn("encryption.key", ignored)
        self.assertIn("*.sqlite3", ignored)


if __name__ == "__main__":
    unittest.main()
