"""Export helpers for audit, winner, claim, and pool data."""

import csv
import json
import os
from datetime import datetime
from pathlib import Path

from config import EXPORTS_PATH
from database.database import db


class ExportService:
    def __init__(self):
        Path(EXPORTS_PATH).mkdir(parents=True, exist_ok=True)

    def _path(self, name: str, ext: str) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return os.path.join(EXPORTS_PATH, f"{name}_{timestamp}.{ext}")

    def export_query_csv(self, name: str, query: str, params=()) -> str:
        rows = db.execute_all(query, params)
        path = self._path(name, "csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            if rows:
                writer = csv.writer(fh)
                writer.writerow(rows[0].keys())
                writer.writerows([tuple(row) for row in rows])
        return path

    def export_query_json(self, name: str, query: str, params=()) -> str:
        rows = db.execute_all(query, params)
        path = self._path(name, "json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([dict(row) for row in rows], fh, indent=2, default=str)
        return path


export_service = ExportService()
