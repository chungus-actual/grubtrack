"""foodcore: the shared brain behind food.db.

Used by fooddb_sqlite.py (the agent's CLI) and GrubTrack (the web app) so both agree on what a
thing is, where it was bought and what it cost. See schema.py for the tables, taxonomy.py for
the categories, ledger.py for receipts and resolution, intel.py for the price analytics.
"""

from .schema import VERSION, migrate  # noqa: F401
from .taxonomy import CATEGORIES, DEPARTMENTS, DEPT_LABEL, cat_label, classify, dept_of  # noqa: F401
