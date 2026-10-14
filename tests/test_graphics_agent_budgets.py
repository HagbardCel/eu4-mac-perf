import sqlite3
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.graphics_agent.budgets import BudgetExceeded, check_and_increment
from reverse_engineering.graphics_agent.research_store import connect, ensure_budget_tables


class BudgetConcurrencyTests(unittest.TestCase):
    def test_two_connections_last_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "research.sqlite"
            c1 = connect(db)
            c2 = sqlite3.connect(db)
            ensure_budget_tables(c1)
            check_and_increment(c1, "inv", "ghidra_queries", 1)
            with self.assertRaises(BudgetExceeded):
                check_and_increment(c2, "inv", "ghidra_queries", 1)


if __name__ == "__main__":
    unittest.main()
