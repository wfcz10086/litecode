"""容器终端执行 python -m unittest checks.py。"""
import unittest
from app import add


class ArithmeticChecks(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)

    def test_negative(self):
        self.assertEqual(add(-2, 3), 1)

    def test_zero(self):
        self.assertEqual(add(0, 0), 0)


if __name__ == '__main__':
    unittest.main()
