import unittest

from tests.external_stubs import install

install()

from lib.validation import validate_schema


class ValidationError(Exception):
    pass


SCHEMA = {
    "profile": (str, ""),
    "news": (list, []),
    "score": ((int, float), 0),
}


class ValidateSchemaTests(unittest.TestCase):
    def test_fills_missing_keys_with_defaults(self):
        data = validate_schema({}, SCHEMA, ValidationError, "TestAgent")
        self.assertEqual(data, {"profile": "", "news": [], "score": 0})

    def test_fills_none_values_with_defaults(self):
        data = validate_schema({"profile": None}, SCHEMA, ValidationError, "TestAgent")
        self.assertEqual(data["profile"], "")

    def test_passes_through_correctly_typed_values(self):
        data = validate_schema(
            {"profile": "Acme is a widget maker.", "news": ["raised Series A"], "score": 8},
            SCHEMA,
            ValidationError,
            "TestAgent",
        )
        self.assertEqual(data["profile"], "Acme is a widget maker.")
        self.assertEqual(data["news"], ["raised Series A"])
        self.assertEqual(data["score"], 8)

    def test_raises_on_type_mismatch(self):
        with self.assertRaisesRegex(ValidationError, "news"):
            validate_schema(
                {"news": "not a list"}, SCHEMA, ValidationError, "TestAgent"
            )

    def test_accepts_tuple_of_types(self):
        data = validate_schema({"score": 7.5}, SCHEMA, ValidationError, "TestAgent")
        self.assertEqual(data["score"], 7.5)


if __name__ == "__main__":
    unittest.main()
