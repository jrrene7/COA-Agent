import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import file_tools


class FileToolsTests(unittest.TestCase):
    def test_write_report_sanitizes_filename_and_stays_in_output_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            with patch.object(file_tools, "OUTPUT_DIR", output_dir):
                result = file_tools.write_report(
                    filename="../../Acme Report",
                    content="# Acme\n",
                )

            path = Path(result["path"])
            self.assertEqual(result["status"], "written")
            self.assertEqual(path.parent, output_dir.resolve())
            self.assertTrue(path.name.endswith(".md"))
            self.assertNotIn("/", path.name)
            self.assertEqual(path.read_text(encoding="utf-8"), "# Acme\n")


if __name__ == "__main__":
    unittest.main()
