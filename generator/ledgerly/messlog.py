"""Record of every injected mess instance (written to data/truth/mess_log.parquet)."""

from __future__ import annotations

import pandas as pd


class MessLog:
    def __init__(self) -> None:
        self._rows: list[dict[str, str]] = []

    def add(self, mess_type: str, table: str, object_id: str, note: str = "") -> None:
        self._rows.append({"mess_type": mess_type, "table": table, "object_id": object_id, "note": note})

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self._rows, columns=["mess_type", "table", "object_id", "note"])
