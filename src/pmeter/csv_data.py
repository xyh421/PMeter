from __future__ import annotations

import csv
import itertools
import threading
from pathlib import Path


class CsvDataSet:
    """Thread-safe CSV data provider. Cycles through rows indefinitely.

    Example::

        users = CsvDataSet("users.csv")

        class MyUser(HttpUser):
            def on_start(self):
                self.user_data = users.next_row()
    """

    def __init__(
        self,
        path: str | Path,
        *,
        delimiter: str = ",",
        encoding: str = "utf-8",
    ) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._rows: list[dict[str, str]] = []
        self._iter: itertools.cycle
        self._load(delimiter, encoding)

    def _load(self, delimiter: str, encoding: str) -> None:
        with open(self._path, newline="", encoding=encoding) as fh:
            reader = csv.DictReader(fh, delimiter=delimiter)
            self._rows = [dict(row) for row in reader]
        if not self._rows:
            raise ValueError(f"CSV file '{self._path}' has no data rows")
        self._iter = itertools.cycle(self._rows)

    def next_row(self) -> dict[str, str]:
        """Return the next row, cycling back to the start when exhausted."""
        with self._lock:
            return next(self._iter)

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)
