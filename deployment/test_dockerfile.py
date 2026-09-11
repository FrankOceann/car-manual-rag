"""Regression checks for dependency artifacts used by the backend image."""
import unittest
from pathlib import Path


class BackendDockerfileTests(unittest.TestCase):
    def test_uses_the_published_linux_cpu_wheel_for_python311(self):
        dockerfile = (Path(__file__).parents[1] / "backend" / "Dockerfile").read_text()
        self.assertIn(
            "torch-2.14.0%2Bcpu-cp311-cp311-manylinux_2_28_x86_64.whl",
            dockerfile,
        )

    def test_allows_an_explicit_pypi_index_override(self):
        dockerfile = (Path(__file__).parents[1] / "backend" / "Dockerfile").read_text()
        self.assertIn("ARG PIP_INDEX_URL=https://pypi.org/simple", dockerfile)
        self.assertIn('--index-url "${PIP_INDEX_URL}"', dockerfile)


if __name__ == "__main__":
    unittest.main()
