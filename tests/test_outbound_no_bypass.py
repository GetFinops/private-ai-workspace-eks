"""Architecture guard: M13 integration code must not bypass the outbound guard.

The only sanctioned outbound HTTP path for personal-information integrations is
``app.control_plane.outbound`` (``validate_outbound_url`` + ``guarded_open``).
This test scans the M13 integration source files and fails if any of them import
a raw HTTP / socket primitive directly instead of routing through the guard.

The integration modules land in later PRs (harness wiring, loopback fixture);
until then the scan list is mostly empty, so the test also verifies the scanner
itself by running it against crafted good/bad samples. That keeps the invariant
enforced from the moment the first integration file appears.
"""
import ast
import pathlib
import unittest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Raw egress primitives an integration must never import directly. They must go
# through app.control_plane.outbound instead.
_FORBIDDEN_IMPORTS = frozenset({
    "http.client",
    "urllib.request",
    "requests",
    "httpx",
    "aiohttp",
    "socket",
})

# M13 integration source files, relative to the repo root. Listed explicitly so
# adding a new integration module is a deliberate act that opts into this guard.
_M13_INTEGRATION_SOURCES = (
    "app/control_plane/integrations.py",
    "app/control_plane/integration_secrets.py",
    "app/integration_fixtures",  # directory, scanned recursively if present
)


def _forbidden_in_source(source: str) -> set[str]:
    """Return any forbidden top-level egress imports found in ``source``."""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _FORBIDDEN_IMPORTS:
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module in _FORBIDDEN_IMPORTS:
                found.add(node.module)
    return found


def _iter_integration_files():
    for rel in _M13_INTEGRATION_SOURCES:
        path = _REPO_ROOT / rel
        if path.is_dir():
            yield from path.rglob("*.py")
        elif path.is_file():
            yield path


class TestScannerItself(unittest.TestCase):
    """Prove the scanner detects violations, so the guard has teeth pre-PR3."""

    def test_flags_forbidden_import(self):
        bad = "import socket\nimport http.client\n\ndef f():\n    return 1\n"
        self.assertEqual(_forbidden_in_source(bad), {"socket", "http.client"})

    def test_flags_from_import(self):
        bad = "from urllib.request import urlopen\n"
        self.assertIn("urllib.request", _forbidden_in_source(bad))

    def test_clean_source_passes(self):
        good = (
            "from app.control_plane.outbound import validate_outbound_url, guarded_open\n"
            "def call(url, hosts):\n"
            "    return guarded_open(validate_outbound_url(url, allowed_hosts=hosts))\n"
        )
        self.assertEqual(_forbidden_in_source(good), set())


class TestNoBypassInIntegrationSources(unittest.TestCase):
    def test_no_integration_file_bypasses_the_guard(self):
        for path in _iter_integration_files():
            with self.subTest(path=str(path.relative_to(_REPO_ROOT))):
                forbidden = _forbidden_in_source(path.read_text())
                self.assertEqual(
                    forbidden,
                    set(),
                    f"{path.name} imports raw egress {sorted(forbidden)}; "
                    "route outbound calls through app.control_plane.outbound instead.",
                )


class TestGuardChokepointExists(unittest.TestCase):
    def test_guard_exposes_the_chokepoint(self):
        from app.control_plane import outbound

        self.assertTrue(hasattr(outbound, "validate_outbound_url"))
        self.assertTrue(hasattr(outbound, "guarded_open"))


if __name__ == "__main__":
    unittest.main()
