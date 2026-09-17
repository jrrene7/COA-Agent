import stat
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

    def test_written_report_is_owner_read_write_only(self):
        """Reports carry lead contact data, so the file must not be group/world readable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            with patch.object(file_tools, "OUTPUT_DIR", output_dir):
                result = file_tools.write_report(filename="acme", content="# Acme\n")

            mode = stat.S_IMODE(Path(result["path"]).stat().st_mode)
            self.assertEqual(mode, stat.S_IRUSR | stat.S_IWUSR)

    def test_reports_byte_count_of_utf8_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            content = "# Acme — café\n"

            with patch.object(file_tools, "OUTPUT_DIR", output_dir):
                result = file_tools.write_report(filename="acme", content=content)

            self.assertEqual(result["bytes"], len(content.encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
