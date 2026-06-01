import unittest

from attestflow.dogfood_marker import dogfood_status


class AttestflowDogfoodBehaviorTests(unittest.TestCase):
    def test_reports_self_managed_status(self) -> None:
        self.assertEqual(dogfood_status(), "attestflow-self-managed")


if __name__ == "__main__":
    unittest.main()
