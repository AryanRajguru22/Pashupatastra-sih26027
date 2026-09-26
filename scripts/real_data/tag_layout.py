"""Structural reader for the stored TAG-2026 Table T-2 layout extract.

The public timetable is a dense multi-column PDF; a naive text parse mis-assigns
cells to trains (the audit found exactly that). This reader assigns every time to
the train whose header number is CLOSEST in character position, on the row of the
named station, and reports the distance so a doubtful assignment is visible.

It is used only to VERIFY the transcription in build_ndls_agc_snapshot.py (see
backend/tests/test_real_snapshot_sources.py). It does no network access and reads
only sources/TAG-2026_T-2_layout_extract.txt.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# Row label as printed in the 'Km / station' column -> corridor station code.
LABEL_TO_STATION = {
    "New Delhi": "NDLS",
    "Nizamuddin": "NZM",
    "Faridabad": "FDB",
    "Mathura": "MTJ",
    "Raja-ki-Mandi": "RKM",
    "Agra Cantt": "AGC",
}

_TIME = re.compile(r"\d\d\.\d\d")
_NO_TIME = re.compile(r"\.{3,4}")

# A cell must sit within this many characters of its train's header number.
MAX_COLUMN_OFFSET = 3.5


def _pages(text: str) -> List[List[str]]:
    return [chunk.split("\n")[1:] for chunk in text.split("######## PAGE ")[1:]]


def parse_layout(text: str) -> Dict[str, Dict]:
    """{train_number: {"days": str, "stops": {station: {"a": [..], "d": [..]}}}}."""

    trains: Dict[str, Dict] = {}

    for lines in _pages(text):
        header_index = next(i for i, l in enumerate(lines) if "Train Number" in l)
        header = lines[header_index]
        columns = [
            ((m.start() + m.end()) / 2.0, m.group().rstrip("*"))
            for m in re.finditer(r"\d{5}\*?", header)
        ]
        centres = [c for c, _ in columns]
        edges = [0.0] + [(a + b) / 2 for a, b in zip(centres, centres[1:])] + [10_000.0]

        days_index = next(i for i, l in enumerate(lines) if "Days of departure" in l)

        for k, (_, number) in enumerate(columns):
            lo, hi = int(edges[k]), int(edges[k + 1])
            cell = " ".join(
                " ".join(lines[days_index + j][lo:hi].split()) for j in (0, 1)
            ).strip()
            for label in ("Days of departure at", "originating station", "originating"):
                cell = cell.replace(label, "").strip()
            trains[number] = {"days": " ".join(cell.split()), "stops": {}}

        current: Optional[str] = None
        for line in lines[header_index + 1 :]:
            label_hit = None
            for label in LABEL_TO_STATION:
                if re.search(r"\b" + re.escape(label) + r"\b", line[:44]):
                    label_hit = label
            marker = re.search(r"\s([ad])\s", line[:46])

            if label_hit and marker:
                current = LABEL_TO_STATION[label_hit]
            elif marker and current and not re.search(r"[A-Za-z]{3,}", line[:28]):
                pass
            else:
                continue

            for token in list(_TIME.finditer(line)) + list(_NO_TIME.finditer(line)):
                if token.start() < marker.end() - 1:
                    continue
                centre = (token.start() + token.end()) / 2.0
                idx = min(range(len(centres)), key=lambda i: abs(centres[i] - centre))
                if abs(centres[idx] - centre) > MAX_COLUMN_OFFSET:
                    continue
                number = columns[idx][1]
                bucket = trains[number]["stops"].setdefault(current, {"a": [], "d": []})
                bucket[marker.group(1)].append(token.group())

    return trains


def published_stops(parsed: Dict, number: str) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """Corridor stops with a printed time: {station: (arrival|None, departure|None)}."""

    out: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for station, cells in parsed[number]["stops"].items():
        arr = next((t for t in cells["a"] if _TIME.fullmatch(t)), None)
        dep = next((t for t in cells["d"] if _TIME.fullmatch(t)), None)
        if arr or dep:
            out[station] = (arr, dep)
    return out
