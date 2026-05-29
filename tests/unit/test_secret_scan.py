from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from attestflow.secrets import secret_scan


class SecretScanTests(unittest.TestCase):
    def test_flags_probable_secret_without_printing_value(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "app.py"
            fake_secret = "12345678" + "90abcdef"
            app.write_text(f"api_key = '{fake_secret}'\n", encoding="utf-8")

            findings = secret_scan(root)

            self.assertEqual(len(findings), 1)
            self.assertIn("app.py:1", findings[0])
            self.assertNotIn(fake_secret, findings[0])

    def test_ignores_env_example_placeholder(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / ".env.example"
            env.write_text("API_KEY=change-me\n", encoding="utf-8")

            self.assertEqual(secret_scan(root), [])


if __name__ == "__main__":
    unittest.main()
