import unittest

from cryptgit_studio.evidence import create_evidence


class EvidenceTests(unittest.TestCase):
    def test_creates_evidence(self) -> None:
        evidence = create_evidence("scanner", "A source file was found", 0.75)

        self.assertEqual("scanner", evidence.source)
        self.assertEqual(0.75, evidence.confidence)

    def test_validates_fields(self) -> None:
        with self.assertRaises(ValueError):
            create_evidence("", "description")
        with self.assertRaises(ValueError):
            create_evidence("source", "description", 1.1)


if __name__ == "__main__":
    unittest.main()
