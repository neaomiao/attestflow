import unittest

from attestflow.dogfood_marker import dogfood_status


class AttestflowDogfoodTests(unittest.TestCase):
    def test_dogfood_status_is_stable(self) -> None:
        self.assertEqual(dogfood_status(), "attestflow-self-managed")


if __name__ == "__main__":
    unittest.main()
