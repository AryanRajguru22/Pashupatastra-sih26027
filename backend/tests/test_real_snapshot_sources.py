"""Every train in the real snapshot is verified against the stored TAG-2026 source.

The audit found that a naive parse of Table T-2 mis-assigns cells to trains, so no
train enters the snapshot on the strength of a parse alone. Here each transcribed
train (scripts/real_data/build_ndls_agc_snapshot.py TAG_TRAINS) is re-checked
against the stored layout extract by an independent, position-based reader
(scripts/real_data/tag_layout.py): the printed stops, times and days must match
exactly. Where pypdf is installed the extract itself is re-derived from the stored
PDF and the SIP quotations are re-found in their PDFs.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO_ROOT / "data" / "railway" / "ndls_agc"
SOURCES = SNAPSHOT / "sources"


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load("pashupatastra_builder_for_sources", "scripts/real_data/build_ndls_agc_snapshot.py")
tag_layout = _load("pashupatastra_tag_layout", "scripts/real_data/tag_layout.py")

EXTRACT = (SOURCES / "TAG-2026_T-2_layout_extract.txt").read_text(encoding="utf-8")
PARSED = tag_layout.parse_layout(EXTRACT)


@pytest.mark.parametrize("train", builder.TAG_TRAINS, ids=lambda t: t["no"])
def test_transcribed_stops_match_the_stored_timetable_exactly(train):
    number = train["no"]
    assert number in PARSED, f"{number} not found in Table T-2"

    expected = {station: (arr, dep) for station, arr, dep in train["stops"]}
    assert tag_layout.published_stops(PARSED, number) == expected


@pytest.mark.parametrize("train", builder.TAG_TRAINS, ids=lambda t: t["no"])
def test_transcribed_days_of_running_match_the_source(train):
    assert PARSED[train["no"]]["days"].replace(" ", "") == train["days"].replace(" ", "")


def test_every_ingested_train_is_printed_in_table_t2():
    numbers = {t["no"] for t in builder.TAG_TRAINS}
    assert numbers <= set(PARSED)
    assert len(numbers) == 23


def test_excluded_trains_are_named_with_a_reason():
    # Each exclusion is explained; none of the named single trains was ingested.
    ingested = {t["no"] for t in builder.TAG_TRAINS}
    for key, reason in builder.EXCLUDED_TRAINS.items():
        assert reason
        if "/" not in key:
            assert key not in ingested


def test_every_stored_source_matches_its_manifest_hash():
    import hashlib

    manifest = json.loads((SNAPSHOT / "manifest.json").read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        digest = hashlib.sha256((SOURCES / source["file"]).read_bytes()).hexdigest()
        assert digest == source["sha256"], source["source_id"]


def test_the_cbs_excerpts_carry_the_full_document_hashes():
    cbs = json.loads((SOURCES / "CBS_2026-27_excerpts.json").read_text(encoding="utf-8"))
    for zone in ("NCR", "NR"):
        assert len(cbs[zone]["sha256"]) == 64
        assert cbs[zone]["url"].startswith("https://indianrailways.gov.in/")
    assert {(i["zone"], i["item_no"]) for i in cbs["items"]} >= {
        ("NR", 1575), ("NCR", 102), ("NCR", 110), ("NCR", 115), ("NCR", 217),
    }


# ----------------------------------------------------------------------
# Optional, stronger checks when pypdf is available (not a runtime dependency)
# ----------------------------------------------------------------------


def _pdf_text(name: str) -> str:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(SOURCES / name))
    return " ".join((page.extract_text() or "") for page in reader.pages)


def test_the_stored_layout_extract_is_what_the_pdf_yields():
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(SOURCES / "TAG-2026_T-2_Delhi-Agra-Bhopal-CSMT.pdf"))
    rebuilt = "\n".join(
        f"######## PAGE {i}\n" + (page.extract_text(extraction_mode="layout") or "")
        for i, page in enumerate(reader.pages)
    )
    assert rebuilt == EXTRACT


def test_the_tag_pdf_says_it_is_the_2026_edition():
    text = _pdf_text("TAG-2026_T-2_Delhi-Agra-Bhopal-CSMT.pdf")
    assert "TAG-26" in text


def test_sip_and_kilometre_post_quotations_are_present_in_the_stored_pdfs():
    infra = json.loads((SNAPSHOT / "infrastructure.json").read_text(encoding="utf-8"))
    manifest = json.loads((SNAPSHOT / "manifest.json").read_text(encoding="utf-8"))
    files = {s["source_id"]: s["file"] for s in manifest["sources"]}

    checked = 0
    for group in ("kilometre_posts", "level_crossings", "running_lines", "automatic_signalling_sections"):
        for entry in infra[group]:
            quote = entry.get("quote_check")
            if not quote:
                continue
            source_id = entry.get("source_id")
            assert source_id in files, entry
            text = " ".join(_pdf_text(files[source_id]).split())
            assert quote in text, (group, entry.get("place") or entry.get("lc_no") or entry.get("location"), quote)
            checked += 1

    assert checked >= 15
