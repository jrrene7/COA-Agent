import logging
import sys
import unittest

from lib.logging_config import RedactingFilter, RedactingFormatter, redact_text


class LoggingConfigTests(unittest.TestCase):
    def test_redact_text_removes_common_secret_shapes(self):
        text = (
            "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz "
            "TAVILY_API_KEY=tvly-abcdefghijklmnopqrstuvwxyz "
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz "
            "password=supersecret"
        )

        redacted = redact_text(text)

        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertNotIn("tvly-abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", redacted)
        self.assertNotIn("supersecret", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_filter_redacts_record_args_before_formatting(self):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="api_key=%s",
            args=("sk-abcdefghijklmnopqrstuvwxyz",),
            exc_info=None,
        )

        RedactingFilter().filter(record)

        self.assertEqual(record.args, ())
        self.assertEqual(record.getMessage(), "api_key=[REDACTED]")

    def test_formatter_redacts_exception_text(self):
        try:
            raise ValueError("token=supersecret")
        except ValueError:
            record = logging.getLogger("test").makeRecord(
                name="test",
                level=logging.ERROR,
                fn=__file__,
                lno=1,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )

        formatted = RedactingFormatter("%(message)s").format(record)

        self.assertNotIn("supersecret", formatted)
        self.assertIn("token=[REDACTED]", formatted)


if __name__ == "__main__":
    unittest.main()
