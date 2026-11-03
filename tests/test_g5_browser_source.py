"""No-network, no-Railway, no-Docker contract tests for G5 packaging candidate."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "g5"))
import fictional_web_entry as entry

BASE = "docker.io/library/python@sha256:70bda1e795ded2a684ef9815955e05829b14ee5bb5c3eb2a940648e88f10fce9"
DOCKER = (ROOT / "g5/Dockerfile.g5-browser-source").read_text()
PINS = json.loads((ROOT / "g5/CONTEXT-PINS.json").read_text())


class G5BrowserSourceContract(unittest.TestCase):
    def test_pinned_base_twice(self):
        self.assertEqual([x.strip() for x in DOCKER.splitlines() if x.startswith("FROM ")],
                         ["FROM " + BASE + " AS wheel_fetch", "FROM " + BASE])

    def test_no_base_argument_or_floating_images(self):
        self.assertNotIn("ARG PYTHON_BASE", DOCKER)
        self.assertNotIn("COPY . .", DOCKER)

    def test_exactly_one_wheel_fetch_step(self):
        self.assertEqual(DOCKER.count("python -m pip download"), 1)
        for value in ("requirements-python313.lock", "--no-deps", "--require-hashes", "--only-binary=:all:"):
            self.assertIn(value, DOCKER)

    def test_final_install_offline(self):
        for value in ("--no-index", "--find-links=/wheels", "python -m pip check"):
            self.assertIn(value, DOCKER)

    def test_only_reviewed_app_copied(self):
        for value in ("COPY app /app/app", "COPY config /app/config", "COPY tools/pilot /app/tools/pilot", "COPY g5 /opt/g5"):
            self.assertIn(value, DOCKER)

    def test_nonroot_and_locked_factory(self):
        self.assertIn("USER 10001:10001", DOCKER)
        self.assertIn("app.core_pilot:create_pilot_app --factory", DOCKER)

    def test_184_source_and_35_wheel_pins(self):
        self.assertEqual((len(PINS["source_sha256"]), len(PINS["wheel_sha256"])), (184, 35))

    def test_no_persisted_key_in_image(self):
        self.assertNotIn("fixture-app.key", DOCKER)
        self.assertNotIn("CROWNOPS_SECRET_KEY=", DOCKER)

    def test_fictional_scope_accept_and_refuse(self):
        valid = {**entry.G5_SCOPE, "CROWNOPS_PUBLIC_URL": "https://g5.example.invalid",
                 "CROWNOPS_DB": "/data/private/pilot.sqlite3"}
        entry.validate_setup(valid, uid=10001, gid=10001)
        for name in entry.G5_SCOPE:
            altered = dict(valid)
            altered.pop(name)
            with self.assertRaises(ValueError):
                entry.validate_setup(altered, uid=10001, gid=10001)
        with self.assertRaises(PermissionError):
            entry.validate_setup(valid, uid=0, gid=0)
        with self.assertRaises(ValueError):
            entry.validate_setup({**valid, "CROWNOPS_DB": "/data/other.sqlite3"}, uid=10001, gid=10001)
        entry.validate_setup({**valid, "CROWNOPS_DB": "/data/private/g5-recovered.sqlite3"}, uid=10001, gid=10001)
        with self.assertRaises(ValueError):
            entry.validate_setup({**valid, "CROWNOPS_SECRET_KEY": "bad"}, uid=10001, gid=10001)

    def test_file_key_rejected_on_invalid_mode_or_link(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fixture.key"
            p.write_text("z" * 44)
            with self.assertRaises(ValueError):
                entry.read_private_key(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
