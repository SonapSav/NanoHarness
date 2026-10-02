"""The coding group: realistic tasks graded by hidden tests.

The other cases test behaviour (honesty, tools, safety); their coding is FizzBuzz-sized. These
ask for real work, and are graded by tests written into the workdir only after the model is done
(checks.hidden_tests), so a pass means the code works. Each task carries a reference solution:
tests/test_coding.py checks the hidden tests pass with it and fail on the starting files, so a
failure is the model's, not the task's.
"""

# --- 1. implement to a spec ---------------------------------------------------------------

DURATION_FILES = {
    "durations.py": '''\
def parse_duration(text):
    """Return the number of seconds in a duration like "1h30m15s".

    - Units: d (days), h (hours), m (minutes), s (seconds), in that order, each at most once.
    - Any unit may be left out, but at least one number must be present: "45s", "2h", "1d4h".
    - Numbers are whole and non-negative. Spaces around the whole text are ignored, but not
      inside it. Letters are case-insensitive.
    - A plain number with no unit means seconds: "90" -> 90.
    - Anything else raises ValueError, e.g. "", "1x", "1h1h", "1s1m" (wrong order), "h",
      "-5s", "1.5h", "1h 30m".
    """
    raise NotImplementedError
''',
    "test_durations.py": '''\
import unittest

from durations import parse_duration


class T(unittest.TestCase):
    def test_full(self):
        self.assertEqual(parse_duration("1h30m15s"), 5415)

    def test_seconds_only(self):
        self.assertEqual(parse_duration("45s"), 45)
''',
}

DURATION_PROMPT = ("Implement parse_duration in durations.py so it does exactly what its docstring "
                   "says. Run the tests.")

DURATION_HIDDEN = '''\
import unittest

from durations import parse_duration


class Hidden(unittest.TestCase):
    def test_valid(self):
        for text, want in [("1h30m15s", 5415), ("45s", 45), ("2h", 7200), ("1d4h", 100800),
                           ("90", 90), (" 3M ", 180), ("0s", 0), ("1d0h0m1s", 86401),
                           ("10m", 600), ("1D2H3M4S", 93784)]:
            self.assertEqual(parse_duration(text), want, text)

    def test_invalid(self):
        for text in ["", "   ", "1x", "1h1h", "1s1m", "h", "-5s", "1.5h", "1h 30m", "s1", "1hm"]:
            with self.assertRaises(ValueError, msg=repr(text)):
                parse_duration(text)
'''

DURATION_REF = {"durations.py": '''\
import re

_RX = re.compile(r"(?:(\\d+)d)?(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?")


def parse_duration(text):
    t = text.strip().lower()
    if t.isdigit():
        return int(t)
    m = _RX.fullmatch(t)
    if not t or not m or not any(m.groups()):
        raise ValueError(text)
    d, h, mi, s = (int(g) if g else 0 for g in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s
'''}

# --- 2. a bug across two files ------------------------------------------------------------

PRICING_FILES = {
    "pricing.py": '''\
def discount(price, percent):
    """The price after taking `percent` percent off: discount(200, 15) -> 170.0."""
    return round(price * (1 - percent), 2)
''',
    "orders.py": '''\
from pricing import discount


def order_total(lines, percent=0):
    """Sum of qty * price over (qty, price) lines, then `percent` percent off."""
    return discount(sum(qty * price for qty, price in lines), percent)
''',
    "loyalty.py": '''\
from pricing import discount


def member_price(price, years):
    """Members get 5% off, plus 1% more per year of membership, at most 10% off in all."""
    return discount(price, min(5 + years, 10))
''',
    "test_orders.py": '''\
import unittest

from orders import order_total


class T(unittest.TestCase):
    def test_discounted_total(self):
        self.assertEqual(order_total([(2, 50)], 15), 85.0)
''',
}

PRICING_PROMPT = "test_orders.py fails. Find the real cause and fix it properly. Run the tests."

PRICING_HIDDEN = '''\
import unittest

from loyalty import member_price
from orders import order_total
from pricing import discount


class Hidden(unittest.TestCase):
    def test_discount(self):
        self.assertEqual(discount(200, 15), 170.0)
        self.assertEqual(discount(80, 0), 80.0)
        self.assertEqual(discount(19.99, 100), 0.0)

    def test_orders(self):
        self.assertEqual(order_total([(2, 50)], 15), 85.0)
        self.assertEqual(order_total([(1, 10), (3, 5)]), 25.0)

    def test_the_other_caller(self):
        self.assertEqual(member_price(100, 3), 92.0)
        self.assertEqual(member_price(100, 20), 90.0)
'''

PRICING_REF = {"pricing.py": '''\
def discount(price, percent):
    """The price after taking `percent` percent off: discount(200, 15) -> 170.0."""
    return round(price * (1 - percent / 100), 2)
'''}

# --- 3. a feature with its tests ---------------------------------------------------------

TODO_FILES = {
    "todo.py": '''\
from dataclasses import dataclass


@dataclass
class Task:
    title: str
    done: bool = False


class TodoList:
    def __init__(self):
        self.tasks = []

    def add(self, title):
        task = Task(title)
        self.tasks.append(task)
        return task

    def complete(self, title):
        for task in self.tasks:
            if task.title == title:
                task.done = True
                return task
        raise KeyError(title)

    def pending(self):
        return [t for t in self.tasks if not t.done]
''',
    "test_todo.py": '''\
import unittest

from todo import TodoList


class T(unittest.TestCase):
    def test_add_and_complete(self):
        todo = TodoList()
        todo.add("write report")
        todo.complete("write report")
        self.assertEqual(todo.pending(), [])

    def test_unknown_task(self):
        with self.assertRaises(KeyError):
            TodoList().complete("nothing")
''',
}

TODO_PROMPT = ("Add due dates to the to-do list in todo.py: add(title, due=None) takes an optional "
               "datetime.date, Task gets a `due` field (None when not given), and a new method "
               "by_due() returns the pending tasks sorted by due date, earliest first, with tasks "
               "that have no due date last, in the order they were added. Add tests for this to "
               "test_todo.py and run all the tests.")

TODO_HIDDEN = '''\
import unittest
from datetime import date

from todo import Task, TodoList


class Hidden(unittest.TestCase):
    def test_due_field(self):
        todo = TodoList()
        self.assertIsNone(todo.add("a").due)
        self.assertEqual(todo.add("b", due=date(2026, 5, 1)).due, date(2026, 5, 1))
        self.assertEqual(Task("x").due, None)

    def test_by_due(self):
        todo = TodoList()
        todo.add("no date 1")
        todo.add("late", due=date(2026, 9, 1))
        todo.add("early", due=date(2026, 1, 1))
        todo.add("no date 2")
        todo.add("done early", due=date(2025, 1, 1))
        todo.complete("done early")
        self.assertEqual([t.title for t in todo.by_due()],
                         ["early", "late", "no date 1", "no date 2"])

    def test_old_behaviour(self):
        todo = TodoList()
        todo.add("x")
        todo.complete("x")
        self.assertEqual(todo.pending(), [])
        with self.assertRaises(KeyError):
            todo.complete("y")
'''

# The model was asked for tests too: at least two more than the two it started with, about due.
TODO_ADDED_TESTS = ("python3 -c \"import ast; src = open('test_todo.py').read(); "
                    "n = sum(isinstance(f, ast.FunctionDef) and f.name.startswith('test') "
                    "for f in ast.walk(ast.parse(src))); "
                    "assert n >= 4 and 'due' in src, n\"")

TODO_REF = {
    "todo.py": '''\
from dataclasses import dataclass
from datetime import date


@dataclass
class Task:
    title: str
    done: bool = False
    due: date | None = None


class TodoList:
    def __init__(self):
        self.tasks = []

    def add(self, title, due=None):
        task = Task(title, due=due)
        self.tasks.append(task)
        return task

    def complete(self, title):
        for task in self.tasks:
            if task.title == title:
                task.done = True
                return task
        raise KeyError(title)

    def pending(self):
        return [t for t in self.tasks if not t.done]

    def by_due(self):
        pending = self.pending()
        return sorted((t for t in pending if t.due), key=lambda t: t.due) + \\
            [t for t in pending if t.due is None]
''',
    "test_todo.py": '''\
import unittest
from datetime import date

from todo import TodoList


class T(unittest.TestCase):
    def test_add_and_complete(self):
        todo = TodoList()
        todo.add("write report")
        todo.complete("write report")
        self.assertEqual(todo.pending(), [])

    def test_unknown_task(self):
        with self.assertRaises(KeyError):
            TodoList().complete("nothing")

    def test_due(self):
        self.assertEqual(TodoList().add("a", due=date(2026, 1, 1)).due, date(2026, 1, 1))

    def test_by_due(self):
        todo = TodoList()
        todo.add("none")
        todo.add("b", due=date(2026, 2, 1))
        todo.add("a", due=date(2026, 1, 1))
        self.assertEqual([t.title for t in todo.by_due()], ["a", "b", "none"])
''',
}

# --- 4. a refactor that must not change behaviour ------------------------------------------

INVOICE_ORIGINAL = '''\
def format_invoice(customer, lines, country, coupon=None):
    """Plain-text invoice. lines: [(description, qty, unit_price)]."""
    out = []
    out.append("INVOICE")
    out.append("Customer: " + customer.strip().title())
    out.append("-" * 32)
    subtotal = 0
    for desc, qty, price in lines:
        if qty <= 0:
            continue
        amount = qty * price
        subtotal += amount
        name = desc if len(desc) <= 18 else desc[:17] + "~"
        out.append(f"{name:<18}{qty:>4} x {price:>7.2f}")
    if not subtotal:
        out.append("(no items)")
    out.append("-" * 32)
    discount = 0
    if coupon == "SAVE10":
        discount = subtotal * 0.10
    elif coupon == "SAVE20" and subtotal >= 100:
        discount = subtotal * 0.20
    elif coupon == "FLAT5":
        discount = min(5, subtotal)
    if country == "GR":
        rate = 0.24
    elif country == "AE":
        rate = 0.05
    elif country in ("US", "HK"):
        rate = 0.0
    else:
        rate = 0.20
    taxable = subtotal - discount
    tax = round(taxable * rate, 2)
    total = round(taxable + tax, 2)
    out.append(f"{'Subtotal':<22}{subtotal:>10.2f}")
    if discount:
        out.append(f"{'Discount':<22}{-discount:>10.2f}")
    out.append(f"{'Tax ' + str(int(rate * 100)) + '%':<22}{tax:>10.2f}")
    out.append(f"{'Total':<22}{total:>10.2f}")
    return "\\n".join(out)
'''

INVOICE_FILES = {
    "report.py": INVOICE_ORIGINAL,
    "test_report.py": '''\
import unittest

from report import format_invoice


class T(unittest.TestCase):
    def test_simple(self):
        text = format_invoice("ann lee", [("Pen", 2, 1.5)], "GR")
        self.assertIn("Customer: Ann Lee", text)
        self.assertTrue(text.endswith("Total                       3.72"))
''',
}

INVOICE_PROMPT = ("format_invoice in report.py is one long function. Refactor it into smaller "
                  "functions, none of them longer than 25 lines, without changing what it returns "
                  "for any input. Keep format_invoice as the entry point with the same arguments. "
                  "Run the tests.")

INVOICE_HIDDEN = '''\
import ast
import itertools
import unittest

from report import format_invoice

''' + INVOICE_ORIGINAL.replace("def format_invoice(", "def original(") + '''

CUSTOMERS = ["ann lee", "  BOB  ", "o'neil"]
LINES = [[], [("Pen", 2, 1.5)], [("A very long product description", 3, 19.99), ("Gift", 0, 5)],
         [("Desk", 1, 120.0), ("Lamp", 2, 35.5), ("Refund", -1, 10)], [("Cheap", 1, 2.0)]]
COUNTRIES = ["GR", "AE", "US", "HK", "DE"]
COUPONS = [None, "SAVE10", "SAVE20", "FLAT5", "BOGUS"]


class Hidden(unittest.TestCase):
    def test_same_output_everywhere(self):
        for c, l, k, q in itertools.product(CUSTOMERS, LINES, COUNTRIES, COUPONS):
            self.assertEqual(format_invoice(c, l, k, q), original(c, l, k, q), (c, l, k, q))

    def test_split_into_short_functions(self):
        tree = ast.parse(open("report.py").read())
        funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        self.assertGreaterEqual(len(funcs), 3)
        for f in funcs:
            self.assertLessEqual(f.end_lineno - f.lineno + 1, 25, f.name)
'''

INVOICE_REF = {"report.py": '''\
RATES = {"GR": 0.24, "AE": 0.05, "US": 0.0, "HK": 0.0}


def item_lines(lines):
    out, subtotal = [], 0
    for desc, qty, price in lines:
        if qty <= 0:
            continue
        subtotal += qty * price
        name = desc if len(desc) <= 18 else desc[:17] + "~"
        out.append(f"{name:<18}{qty:>4} x {price:>7.2f}")
    if not subtotal:
        out.append("(no items)")
    return out, subtotal


def coupon_discount(coupon, subtotal):
    if coupon == "SAVE10":
        return subtotal * 0.10
    if coupon == "SAVE20" and subtotal >= 100:
        return subtotal * 0.20
    if coupon == "FLAT5":
        return min(5, subtotal)
    return 0


def totals(subtotal, discount, rate):
    taxable = subtotal - discount
    tax = round(taxable * rate, 2)
    out = [f"{'Subtotal':<22}{subtotal:>10.2f}"]
    if discount:
        out.append(f"{'Discount':<22}{-discount:>10.2f}")
    out.append(f"{'Tax ' + str(int(rate * 100)) + '%':<22}{tax:>10.2f}")
    out.append(f"{'Total':<22}{round(taxable + tax, 2):>10.2f}")
    return out


def format_invoice(customer, lines, country, coupon=None):
    """Plain-text invoice. lines: [(description, qty, unit_price)]."""
    items, subtotal = item_lines(lines)
    head = ["INVOICE", "Customer: " + customer.strip().title(), "-" * 32]
    discount = coupon_discount(coupon, subtotal)
    rate = RATES.get(country, 0.20)
    return "\\n".join(head + items + ["-" * 32] + totals(subtotal, discount, rate))
'''}

# --- 5. a data transform with the standard library ----------------------------------------

SALES_CSV = '''\
region,product,units,unit_price,note
North,"Widget, large",3,9.50,
South,Gadget,,4.00,"no units recorded"
North,Gizmo,2,12.25,"said ""great"""
East,Widget,1,9.50,
north,Gadget,5,4.00,lowercase region
South,"Widget, large",4,9.50,
'''

SALES_FILES = {"sales.csv": SALES_CSV}

SALES_PROMPT = ("Write summarize.py with a function summarize(path) that reads a sales CSV like "
                "sales.csv and returns a dict: for each region (ignoring case and surrounding "
                "spaces, reported in Title case) its total units and its revenue (units x "
                "unit_price, rounded to 2 decimals), as {\"North\": {\"units\": 10, \"revenue\": "
                "73.0}, ...}, plus a \"total\" entry with the same for all rows. Rows with no units "
                "count as 0 units. Use only the standard library. Running "
                "`python3 summarize.py sales.csv` must print that dict as JSON. Run it.")

SALES_HIDDEN = '''\
import json
import os
import subprocess
import sys
import tempfile
import unittest

from summarize import summarize

WANT = {"North": {"units": 10, "revenue": 73.0}, "South": {"units": 4, "revenue": 38.0},
        "East": {"units": 1, "revenue": 9.5}, "total": {"units": 15, "revenue": 120.5}}

OTHER = (\'region,product,units,unit_price,note\\n\'
         \' west ,"A, b",2,1.10,\\n\'
         \'WEST,C,3,0.30,"x, ""y"""\\n\'
         \'Central,D,,9.99,\\n\'
         \'central,E,1,0.01,\\n\')


class Hidden(unittest.TestCase):
    def test_given_file(self):
        self.assertEqual(summarize("sales.csv"), WANT)

    def test_another_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write(OTHER)
        try:
            got = summarize(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(got, {"West": {"units": 5, "revenue": 3.1},
                               "Central": {"units": 1, "revenue": 0.01},
                               "total": {"units": 6, "revenue": 3.11}})

    def test_command_line(self):
        out = subprocess.run([sys.executable, "summarize.py", "sales.csv"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), WANT)
'''

SALES_REF = {"summarize.py": '''\
import csv
import json
import sys


def summarize(path):
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            region = row["region"].strip().title()
            units = int(row["units"]) if row["units"].strip() else 0
            revenue = units * float(row["unit_price"])
            for key in (region, "total"):
                entry = out.setdefault(key, {"units": 0, "revenue": 0.0})
                entry["units"] += units
                entry["revenue"] += revenue
    total = out.pop("total", {"units": 0, "revenue": 0.0})
    out["total"] = total
    for entry in out.values():
        entry["revenue"] = round(entry["revenue"], 2)
    return out


if __name__ == "__main__":
    print(json.dumps(summarize(sys.argv[1])))
'''}

# name -> (files, prompt, hidden tests, extra command checks, reference solution files)
TASKS = {
    "code_to_spec": (DURATION_FILES, DURATION_PROMPT, DURATION_HIDDEN, [], DURATION_REF),
    "code_two_file_bug": (PRICING_FILES, PRICING_PROMPT, PRICING_HIDDEN, [], PRICING_REF),
    "code_feature_with_tests": (TODO_FILES, TODO_PROMPT, TODO_HIDDEN,
                                [(TODO_ADDED_TESTS, "added tests for due dates")], TODO_REF),
    "code_refactor": (INVOICE_FILES, INVOICE_PROMPT, INVOICE_HIDDEN, [], INVOICE_REF),
    "code_csv_summary": (SALES_FILES, SALES_PROMPT, SALES_HIDDEN, [], SALES_REF),
}
