"""Build the offline NDLS -> AGC real-data snapshot (data/railway/ndls_agc/).

    python scripts/real_data/build_ndls_agc_snapshot.py            write the snapshot
    python scripts/real_data/build_ndls_agc_snapshot.py --check    verify the checked-in
                                                                   snapshot equals a rebuild

WHAT THIS IS
    The one place where the audited public railway facts are written down as
    data. It reads ONLY the files already stored in
    data/railway/ndls_agc/sources/ (downloaded once, hashed here) and writes the
    normalized JSON the application loads at runtime. It performs NO network
    access, so the demo never depends on a website being reachable; refreshing
    the snapshot is a deliberate, reviewed act (see sources/README.md).

    Nothing here is transcribed from memory: every published train time below was
    read from the stored TAG-2026 Table T-2 layout extract by column position and
    re-checked by tests (tests/test_real_snapshot_sources.py). Every derived value
    records how it was derived.

DATA CLASSES (per field, see provenance records)
    REAL, REAL_DATED_SNAPSHOT, DERIVED_FROM_REAL, ENGINEERING_ASSUMPTION,
    DEMO_MAINTENANCE_INPUT, UNAVAILABLE_PUBLICLY.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SNAPSHOT_DIR = REPO_ROOT / "data" / "railway" / "ndls_agc"
SOURCES_DIR = SNAPSHOT_DIR / "sources"

CORRIDOR_ID = "CORR-NDLS-AGC"
SNAPSHOT_ID = "ndls-agc-2026-09-26"
SCHEMA_VERSION = 1
RETRIEVED = "2026-09-26"

DATA_CLASSES = (
    "REAL",
    "REAL_DATED_SNAPSHOT",
    "DERIVED_FROM_REAL",
    "ENGINEERING_ASSUMPTION",
    "DEMO_MAINTENANCE_INPUT",
    "UNAVAILABLE_PUBLICLY",
)

# The planning horizon the demo runs over. Kept equal to the checked-in
# DEFAULT_HORIZON_START (asserted below) so the real path anchors to the same
# clock as the synthetic one.
HORIZON_START_DATE = date(2026, 9, 10)
HORIZON_DAYS = 2


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ----------------------------------------------------------------------
# Sources. Files under sources/ are hashed at build time; the manifest is the
# single index the loader trusts.
# ----------------------------------------------------------------------

_TAG_URL = (
    "https://indianrailways.gov.in/railwayboard/uploads/directorate/"
    "coaching/TAG_2026/2.pdf"
)
_NCR_SIP_PAGE = (
    "https://ncr.indianrailways.gov.in/view_section.jsp?lang=0&id=0,1,283,375,704,707"
)

# (source_id, file or None, attributes)
SOURCES: List[Dict[str, Any]] = [
    {
        "source_id": "SRC-TAG-2026-T2",
        "name": "Indian Railways Trains at a Glance 2026 - Table T-2 (New Delhi-Agra Cantt.-Bhopal-Bhusaval-CSMT)",
        "publisher": "Ministry of Railways, Railway Board (Coaching Directorate)",
        "url": _TAG_URL,
        "source_type": "OFFICIAL_TIMETABLE_PDF",
        "file": "TAG-2026_T-2_Delhi-Agra-Bhopal-CSMT.pdf",
        "data_as_of": "2026-01-01",
        "last_reviewed": "2026-01-01",
        "publication_note": "'With effect from 1 January 2026'; PDF created 2025-12-31.",
        "licence": "Not stated in the document. Public Government of India publication, freely downloadable.",
        "coverage": "Mail/Express passenger services on the New Delhi-Agra-Bhopal-Bhusaval route. Does not list every train on NDLS-MTJ (see T-3), EMU/MEMU, freight or the working timetable.",
        "currency": "Current edition as of the retrieval date; nine months old (does not reflect later notices).",
    },
    {
        "source_id": "SRC-TAG-2026-T2-EXTRACT",
        "name": "Text extraction (layout mode) of SRC-TAG-2026-T2, stored so the timetable can be re-verified offline",
        "publisher": "Derived by this project from SRC-TAG-2026-T2",
        "url": _TAG_URL,
        "source_type": "DERIVED_TEXT_EXTRACT",
        "file": "TAG-2026_T-2_layout_extract.txt",
        "data_as_of": "2026-01-01",
        "last_reviewed": "2026-01-01",
        "licence": "Same as SRC-TAG-2026-T2.",
        "coverage": "All four pages of Table T-2.",
        "currency": "Mechanical extraction; not itself authoritative.",
    },
    {
        "source_id": "SRC-NR-FDB-NOTICE",
        "name": "Northern Railway notice 31-08-2026: Cancellation/Regulation/Stoppage Skipping/Change in Platform of trains (Faridabad power and traffic block)",
        "publisher": "Northern Railway (Indian Railways portal)",
        "url": "https://nr.indianrailways.gov.in/view_detail.jsp?lang=0&id=0,4,268&dcd=13026",
        "source_type": "OFFICIAL_PRESS_RELEASE_TEXT",
        "file": "NR_2026-08-31_Faridabad_block_notice.txt",
        "data_as_of": "2026-08-31",
        "last_reviewed": "2026-08-31",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Faridabad station platform lines 4 and 5 (platforms 2 and 3), Delhi Division, 01-09-2026 to 15-10-2026.",
        "currency": "Current.",
    },
    {
        "source_id": "SRC-NR-FDB-ANNEX",
        "name": "Northern Railway Annexure-A: change in platform at Faridabad (43 trains with WTT and PTT times at FDB)",
        "publisher": "Northern Railway, Delhi Division",
        "url": "https://nr.indianrailways.gov.in/uploads/files/1788153462476-Platform%20change%20at%20FDB.pdf",
        "source_type": "OFFICIAL_NOTICE_PDF",
        "file": "NR_2026-08-31_Faridabad_platform-change_AnnexureA.pdf",
        "data_as_of": "2026-08-27",
        "last_reviewed": "2026-08-31",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "43 trains (17 UP, 26 DN) at Faridabad only.",
        "currency": "Current; PDF created 2026-08-27.",
    },
    {
        "source_id": "SRC-NR-FDB-ANNEX-PARSED",
        "name": "Mechanically parsed rows of SRC-NR-FDB-ANNEX",
        "publisher": "Derived by this project from SRC-NR-FDB-ANNEX",
        "url": "https://nr.indianrailways.gov.in/uploads/files/1788153462476-Platform%20change%20at%20FDB.pdf",
        "source_type": "DERIVED_TEXT_EXTRACT",
        "file": "NR_FDB_annexure_A_parsed.json",
        "data_as_of": "2026-08-27",
        "last_reviewed": "2026-08-31",
        "licence": "Same as SRC-NR-FDB-ANNEX.",
        "coverage": "43 rows.",
        "currency": "Mechanical extraction.",
    },
    {
        "source_id": "SRC-NCR-SIP-INDEX",
        "name": "NCR Agra Division S&T page listing Signal Interlocking Plans (AGC to PWL)",
        "publisher": "North Central Railway, Agra Division",
        "url": _NCR_SIP_PAGE,
        "source_type": "OFFICIAL_WEB_PAGE_INDEX",
        "file": "NCR_Agra_SIP_index_2026-09-26.txt",
        "data_as_of": "2026-09-09",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Index of ~30 station and block-section SIPs, Agra Cantt. to Palwal.",
        "currency": "Page last reviewed 09-09-2026; the SIP documents themselves carry their own (older) dates.",
    },
    {
        "source_id": "SRC-NCR-SIP-RKM",
        "name": "NCR SIP - Raja Ki Mandi (EI), drawing 'S.I.PA1202 REV A' dated 20-03-2012",
        "publisher": "North Central Railway, Agra Division",
        "url": "https://ncr.indianrailways.gov.in/uploads/files/1482128948114-002RAJA-KI-MANDI(EI)-Model.pdf",
        "source_type": "OFFICIAL_SIGNAL_INTERLOCKING_PLAN",
        "file": "NCR_SIP_Raja-Ki-Mandi_2012.pdf",
        "data_as_of": "2012-03-20",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Raja Ki Mandi station yard; image-only drawing (read visually). Shows two main lines and 'AGC 3.88 Km', 'BFP 1.45 Km'.",
        "currency": "HISTORICAL (2012). Must not be read as the current infrastructure.",
    },
    {
        "source_id": "SRC-NCR-SIP-AGC",
        "name": "NCR SIP - Agra Cantt.",
        "publisher": "North Central Railway, Agra Division",
        "url": "https://ncr.indianrailways.gov.in/uploads/files/1482128923639-001agrs%20cantt-Model.pdf",
        "source_type": "OFFICIAL_SIGNAL_INTERLOCKING_PLAN",
        "file": "NCR_SIP_Agra-Cantt.pdf",
        "data_as_of": "2016-12-14",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Agra Cantt. yard. Drawing date not printed; PDF created 2016-12-14.",
        "currency": "HISTORICAL (file date 2016).",
    },
    {
        "source_id": "SRC-NCR-SIP-RUNAKTA",
        "name": "NCR SIP - Runakta (mini diagram, updated 09-10-2025)",
        "publisher": "North Central Railway, Agra Division",
        "url": "https://ncr.indianrailways.gov.in/uploads/files/1761911811332-RNKA MINI UPDATED 091025.pdf",
        "source_type": "OFFICIAL_SIGNAL_INTERLOCKING_PLAN",
        "file": "NCR_SIP_Runakta_2025-10.pdf",
        "data_as_of": "2025-10-09",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Runakta station; Bilochpura and Kitham km.",
        "currency": "Dated 2025-10.",
    },
    {
        "source_id": "SRC-NCR-SIP-KITHAM",
        "name": "NCR SIP - Kitham (mini diagram 14-10-2025)",
        "publisher": "North Central Railway, Agra Division",
        "url": "https://ncr.indianrailways.gov.in/uploads/files/1761911878914-KXM MINI 141025.pdf",
        "source_type": "OFFICIAL_SIGNAL_INTERLOCKING_PLAN",
        "file": "NCR_SIP_Kitham_2025-10.pdf",
        "data_as_of": "2025-10-14",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Kitham station; Runakta and Farah km; LC 514.",
        "currency": "Dated 2025-10.",
    },
    {
        "source_id": "SRC-NCR-SIP-KITHAM-FARAH",
        "name": "NCR SIP - Kitham-Farah block section (mini diagram 14-10-2025)",
        "publisher": "North Central Railway, Agra Division",
        "url": "https://ncr.indianrailways.gov.in/uploads/files/1761911920197-KXM-FAR MINI 141025.pdf",
        "source_type": "OFFICIAL_SIGNAL_INTERLOCKING_PLAN",
        "file": "NCR_SIP_Kitham-Farah_2025-10.pdf",
        "data_as_of": "2025-10-14",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Kitham-Farah; LC 514, LC 515.",
        "currency": "Dated 2025-10.",
    },
    {
        "source_id": "SRC-NCR-SIP-FARAH",
        "name": "NCR SIP - Farah (14-10-2025)",
        "publisher": "North Central Railway, Agra Division",
        "url": "https://ncr.indianrailways.gov.in/uploads/files/1761911954548-FAR SIP 141025.pdf",
        "source_type": "OFFICIAL_SIGNAL_INTERLOCKING_PLAN",
        "file": "NCR_SIP_Farah_2025-10.pdf",
        "data_as_of": "2025-10-14",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Farah station; Bad and Kitham km; shows a 3rd main line.",
        "currency": "Dated 2025-10.",
    },
    {
        "source_id": "SRC-NCR-SIP-FARAH-BAD",
        "name": "NCR SIP - Farah-Bad block section (mini diagram 14-10-2025)",
        "publisher": "North Central Railway, Agra Division",
        "url": "https://ncr.indianrailways.gov.in/uploads/files/1761911993838-FAR-BAD MINI 141025.pdf",
        "source_type": "OFFICIAL_SIGNAL_INTERLOCKING_PLAN",
        "file": "NCR_SIP_Farah-Bad_2025-10.pdf",
        "data_as_of": "2025-10-14",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Farah-Bad; LC 522, 523, 524; 3rd line.",
        "currency": "Dated 2025-10.",
    },
    {
        "source_id": "SRC-NCR-SIP-BAD",
        "name": "NCR SIP - Bad (24-10-2025)",
        "publisher": "North Central Railway, Agra Division",
        "url": "https://ncr.indianrailways.gov.in/uploads/files/1761912054814-BAD MINI PH-2 UPDATED.pdf",
        "source_type": "OFFICIAL_SIGNAL_INTERLOCKING_PLAN",
        "file": "NCR_SIP_Bad_2025-10.pdf",
        "data_as_of": "2025-10-24",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Bad station; Mathura 10.22 km, Farah 8.69 km; LC 524/A, 524/B.",
        "currency": "Dated 2025-10. Its station-building km (1386/797.85) differs by 42 m from the 1386.84 chainage printed in the Farah SIP.",
    },
    {
        "source_id": "SRC-NCR-SIP-MTJ",
        "name": "NCR SIP - Mathura Junction",
        "publisher": "North Central Railway, Agra Division",
        "url": "https://ncr.indianrailways.gov.in/uploads/files/1505826510056-008MATHURA JUNCTION-Model.pdf",
        "source_type": "OFFICIAL_SIGNAL_INTERLOCKING_PLAN",
        "file": "NCR_SIP_Mathura-Jn_2017.pdf",
        "data_as_of": "2017-09-04",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Mathura Jn yard; prints 'AT KM 1397.06 AS PER W.T.T.'.",
        "currency": "HISTORICAL (file date 2017); the yard is being remodelled (see blocks.json).",
    },
    {
        "source_id": "SRC-NCR-SIP-HODAL",
        "name": "NCR SIP - Hodal (2021)",
        "publisher": "North Central Railway, Agra Division",
        "url": "https://ncr.indianrailways.gov.in/uploads/files/1642597291762-025 HODAL EI-Model.pdf",
        "source_type": "OFFICIAL_SIGNAL_INTERLOCKING_PLAN",
        "file": "NCR_SIP_Hodal_2021.pdf",
        "data_as_of": "2021-12-03",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Hodal; LC 551, 552, 554.",
        "currency": "Dated 2021 (file date).",
    },
    {
        "source_id": "SRC-NCR-SIP-SHOLAKA-RUNDHI",
        "name": "NCR SIP - Sholaka-Rundhi block section (automatic signalling, 2021)",
        "publisher": "North Central Railway, Agra Division",
        "url": "https://ncr.indianrailways.gov.in/uploads/files/1642597340820-028 AUTO SHOLAKA RUNDHI-Model.pdf",
        "source_type": "OFFICIAL_SIGNAL_INTERLOCKING_PLAN",
        "file": "NCR_SIP_Sholaka-Rundhi_2021.pdf",
        "data_as_of": "2021-12-03",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Sholaka-Rundhi: 'AUTOMATIC SIGNALLING', UP/DN main + 3rd + 4th line; LC 559, 560.",
        "currency": "Dated 2021 (file date).",
    },
    {
        "source_id": "SRC-NCR-SIP-RUNDHI",
        "name": "NCR SIP - Rundhi (2021)",
        "publisher": "North Central Railway, Agra Division",
        "url": "https://ncr.indianrailways.gov.in/uploads/files/1642597354636-029 RUNDHI EI-Model.pdf",
        "source_type": "OFFICIAL_SIGNAL_INTERLOCKING_PLAN",
        "file": "NCR_SIP_Rundhi_2021.pdf",
        "data_as_of": "2021-12-03",
        "last_reviewed": "2026-09-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Rundhi 'KM: 1473.981'; 4th line, '(PALWAL SPL CLASS AT Km 9.31)'; LC 560, 561.",
        "currency": "Dated 2021 (file date).",
    },
    {
        "source_id": "SRC-OSM-STATIONS",
        "name": "OpenStreetMap station nodes (Overpass API), 7 corridor stations",
        "publisher": "OpenStreetMap contributors",
        "url": "https://overpass-api.de/api/interpreter",
        "source_type": "COMMUNITY_MAP_DATABASE",
        "file": "OSM_station_nodes_2026-09-26.json",
        "data_as_of": "2026-09-26T09:31:05Z",
        "last_reviewed": "2026-09-26",
        "licence": "Open Database Licence (ODbL) 1.0. Attribution: (c) OpenStreetMap contributors. Derived databases must stay under ODbL.",
        "coverage": "Coordinates only. Not official Indian Railways coordinates.",
        "currency": "Database timestamp 2026-09-26T09:31:05Z.",
    },
    {
        "source_id": "SRC-CBS-2026-27",
        "name": "Consolidated Budget Statement 2026-27 (Pink Book new format) - North Central Railway and Northern Railway, excerpts",
        "publisher": "Ministry of Railways, Railway Board (Finance/Budget)",
        "url": "https://indianrailways.gov.in/railwayboard/uploads/directorate/finance_budget/CBS_2026-27/",
        "source_type": "OFFICIAL_BUDGET_DOCUMENT_EXCERPT",
        "file": "CBS_2026-27_excerpts.json",
        "data_as_of": "2026-02-04",
        "last_reviewed": "2026-09-26",
        "licence": "Not stated. Public Government of India publication. Full PDFs (17 MB each) are NOT stored; their SHA-256 is recorded inside the excerpt file.",
        "coverage": "11 itemised works on/near the corridor. A sanctioned work is not evidence that a defect exists at any location.",
        "currency": "PDF modified 2026-02-04.",
    },
    {
        "source_id": "SRC-PIB-KAVACH",
        "name": "PIB releases on Kavach 4.0 commissioning (2025-07-30, 2025-12-05, 2026-01-30), excerpts",
        "publisher": "Press Information Bureau, Government of India",
        "url": "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2221011",
        "source_type": "OFFICIAL_PRESS_RELEASE_EXCERPT",
        "file": "PIB_Kavach_excerpts.json",
        "data_as_of": "2026-01-30",
        "last_reviewed": "2026-09-26",
        "licence": "Not stated. Public Government of India press releases.",
        "coverage": "Palwal-Mathura-Nagda; Tughlakabad Jn Cabin-Palwal.",
        "currency": "Latest release 2026-01-30.",
    },
    {
        "source_id": "SRC-CR-MTJ-2024",
        "name": "Central Railway notice 09-01-2024: cancellation/diversion due to non-interlocking work for yard remodelling at Mathura Junction",
        "publisher": "Central Railway (Indian Railways portal)",
        "url": "https://cr.indianrailways.gov.in/view_detail.jsp?lang=0&id=0,4,268&dcd=9099",
        "source_type": "OFFICIAL_PRESS_RELEASE_TEXT",
        "file": "CR_2024-01-09_Mathura_yard_remodelling_notice.txt",
        "data_as_of": "2024-01-09",
        "last_reviewed": "2024-01-09",
        "licence": "Not stated. Public Government of India portal.",
        "coverage": "Historical block; the first part of the notice is stored.",
        "currency": "HISTORICAL (January-February 2024).",
    },
    {
        "source_id": "SRC-PIB-2022-CORRIDOR-BLOCK",
        "name": "PIB 2022 (PRID 1863836): fixed corridor blocks of 3 hours in divisional working timetables (policy statement)",
        "publisher": "Press Information Bureau, Government of India",
        "url": "https://pib.gov.in/PressReleasePage.aspx?PRID=1863836",
        "source_type": "OFFICIAL_PRESS_RELEASE_EXCERPT",
        "file": "PIB_2022_corridor_block_policy_excerpt.json",
        "data_as_of": "2022-10-01",
        "last_reviewed": "2026-09-26",
        "licence": "Not stated. Public Government of India press release.",
        "coverage": "Policy only. Actual corridor-block timings of the Delhi and Agra divisions are not public.",
        "currency": "2022.",
    },
]

_SOURCE_IDS = {s["source_id"] for s in SOURCES}


# ----------------------------------------------------------------------
# Topology
# ----------------------------------------------------------------------

# NDLS-relative chainage inputs. Two independent public bases:
#   (a) TAG-2026 published km (Delhi-origin): NDLS 3 (T-3), NZM 10, FDB 31, MTJ 144
#       (T-3) / 145 (T-2). NDLS-relative = published - 3.
#   (b) Official NCR SIP km-posts (Mumbai-origin, increasing toward Delhi).
TAG_NDLS_KM = 3

# Official km-posts (NCR SIPs). PWL and RKM/AGC are DERIVED (see below).
KMPOST_BILOCHPURA = 1348.60   # SRC-NCR-SIP-RUNAKTA (2025-10)
KMPOST_MTJ = 1397.06          # SRC-NCR-SIP-BAD / -MTJ ('as per W.T.T.')
KMPOST_RUNDHI = 1473.981      # SRC-NCR-SIP-RUNDHI (2021)
RUNDHI_TO_PALWAL_KM = 9.31    # SRC-NCR-SIP-RUNDHI '(PALWAL SPL CLASS AT Km 9.31)'
RKM_TO_BILOCHPURA_KM = 1.45   # SRC-NCR-SIP-RKM (2012) 'BFP 1.45 Km'
AGC_TO_RKM_KM = 3.88          # SRC-NCR-SIP-RKM (2012) 'AGC 3.88 Km'

KMPOST_PALWAL = round(KMPOST_RUNDHI + RUNDHI_TO_PALWAL_KM, 3)          # 1483.291
KMPOST_RKM = round(KMPOST_BILOCHPURA - RKM_TO_BILOCHPURA_KM, 3)        # 1347.150
KMPOST_AGC = round(KMPOST_RKM - AGC_TO_RKM_KM, 3)                      # 1343.270

MTJ_KM = 141.0  # TAG T-3 (144) - 3. TAG T-2 implies 142; +/-1 km recorded.

# Chainage used by the application (km, NDLS-relative). See provenance records.
STATION_KM: Dict[str, float] = {
    "NDLS": 0.0,
    "NZM": 7.0,
    "FDB": 28.0,
    "PWL": round(MTJ_KM - (KMPOST_PALWAL - KMPOST_MTJ), 1),   # 54.8
    "MTJ": MTJ_KM,
    "RKM": round(MTJ_KM + (KMPOST_MTJ - KMPOST_RKM), 1),      # 190.9
    "AGC": round(MTJ_KM + (KMPOST_MTJ - KMPOST_AGC), 1),      # 194.8
}

STATIONS: List[Dict[str, Any]] = [
    {"station_id": "NDLS", "name": "New Delhi", "osm_node_id": 554257841},
    {"station_id": "NZM", "name": "Hazrat Nizamuddin", "osm_node_id": 1988585233},
    {"station_id": "FDB", "name": "Faridabad", "osm_node_id": 1572649890},
    {"station_id": "PWL", "name": "Palwal", "osm_node_id": 1078916610},
    {"station_id": "MTJ", "name": "Mathura Junction", "osm_node_id": 790920249},
    {"station_id": "RKM", "name": "Raja Ki Mandi", "osm_node_id": 301786568},
    {"station_id": "AGC", "name": "Agra Cantt.", "osm_node_id": 458972632},
]

# How each chainage is displayed. Integer where the source is a whole-km
# published figure; '~x.x' where it is derived from official km-posts. No more
# precision than that is claimed anywhere.
KM_DISPLAY = {
    "NDLS": ("0", "derived_origin"),
    "NZM": ("7", "published_integer_km"),
    "FDB": ("28", "published_integer_km"),
    "PWL": ("~54.8", "derived_from_official_kmposts"),
    "MTJ": ("141", "published_integer_km"),
    "RKM": ("~190.9", "derived_from_official_kmposts"),
    "AGC": ("~194.8", "derived_from_official_kmposts"),
}

KM_UNCERTAINTY_KM = {
    "NDLS": 0.0, "NZM": 1.0, "FDB": 1.0, "PWL": 3.0, "MTJ": 1.0, "RKM": 2.0, "AGC": 2.0,
}

# (section, zone, division, source_ids)
_NR = "Northern Railway"
_NCR = "North Central Railway"
SECTION_ADMIN = {
    "NDLS-NZM": (_NR, "Delhi Division", ["SRC-NR-FDB-NOTICE"]),
    "NZM-FDB": (_NR, "Delhi Division", ["SRC-NR-FDB-NOTICE"]),
    "FDB-PWL": (_NR, "Delhi Division", ["SRC-NR-FDB-NOTICE"]),
    "PWL-MTJ": (_NCR, "Agra Division", ["SRC-NCR-SIP-INDEX"]),
    "MTJ-RKM": (_NCR, "Agra Division", ["SRC-NCR-SIP-INDEX"]),
    "RKM-AGC": (_NCR, "Agra Division", ["SRC-NCR-SIP-INDEX"]),
}


def prov(
    field: str,
    value: Any,
    unit: Optional[str],
    cls: str,
    source_ids: List[str],
    confidence: str,
    derived_from: Optional[List[str]] = None,
    derivation_method: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    assert cls in DATA_CLASSES, cls
    for sid in source_ids:
        assert sid in _SOURCE_IDS, sid
    record: Dict[str, Any] = {
        "field": field,
        "value": value,
        "unit": unit,
        "class": cls,
        "source_ids": list(source_ids),
        "derived_from": derived_from,
        "derivation_method": derivation_method,
        "confidence": confidence,
    }
    if note:
        record["note"] = note
    return record


# Attributes the scoring model reads from a track. None of them is a published
# figure for this corridor, so they are declared ENGINEERING_ASSUMPTIONs (see the
# provenance records) rather than presented as data.
TRACK_ATTRIBUTES = {
    "speed_limit_kmh": 160,
    "electrified": True,
    "daily_train_density": 70,
    "route_classification": "GROUP_A",
}


def build_topology() -> Dict[str, Any]:
    ids = [s["station_id"] for s in STATIONS]
    osm = json.loads((SOURCES_DIR / "OSM_station_nodes_2026-09-26.json").read_text(encoding="utf-8"))
    by_node = {n["osm_node_id"]: n for n in osm["nodes"]}

    stations = []
    for s in STATIONS:
        node = by_node[s["osm_node_id"]]
        assert node["station_id"] == s["station_id"], (node, s)
        display, precision = KM_DISPLAY[s["station_id"]]
        stations.append(
            {
                "station_id": s["station_id"],
                "name": s["name"],
                "km": STATION_KM[s["station_id"]],
                "km_display": display,
                "km_precision": precision,
                "km_uncertainty_km": KM_UNCERTAINTY_KM[s["station_id"]],
                "lat": node["lat"],
                "lon": node["lon"],
                "coordinate_source": {
                    "source_id": "SRC-OSM-STATIONS",
                    "osm_node_id": s["osm_node_id"],
                    "attribution": "(c) OpenStreetMap contributors, ODbL",
                    "official_railway_coordinate": False,
                },
            }
        )

    sections = []
    for a, b in zip(ids, ids[1:]):
        zone, division, _ = SECTION_ADMIN[f"{a}-{b}"]
        sections.append(
            {
                "section_id": f"{a}-{b}",
                "start_station_id": a,
                "end_station_id": b,
                "km_start": STATION_KM[a],
                "km_end": STATION_KM[b],
                "length_km": round(STATION_KM[b] - STATION_KM[a], 1),
                "zone": zone,
                "division": division,
            }
        )

    provenance: List[Dict[str, Any]] = []
    for s in STATIONS:
        sid = s["station_id"]
        provenance.append(
            prov(f"stations.{sid}.name_and_code", f"{sid} {s['name']}", None, "REAL",
                 ["SRC-TAG-2026-T2", "SRC-OSM-STATIONS"], "high",
                 note="Station names as printed in TAG; codes agree with the NR annexure (FDB) and OSM 'railway:ref'.")
        )
        provenance.append(
            prov(f"stations.{sid}.lat_lon", [next(x for x in stations if x['station_id']==sid)['lat'], next(x for x in stations if x['station_id']==sid)['lon']], "degrees", "REAL_DATED_SNAPSHOT",
                 ["SRC-OSM-STATIONS"], "medium",
                 note="OpenStreetMap community coordinate, ODbL. Not an official Indian Railways coordinate.")
        )

    km_prov = {
        "NDLS": prov("stations.NDLS.km", 0.0, "km", "DERIVED_FROM_REAL", ["SRC-TAG-2026-T2"], "high",
                     ["TAG-2026 T-3: New Delhi published km 3"],
                     "Origin of the NDLS-relative chainage: published km minus 3. A derived origin, not a railway km-post.",),
        "NZM": prov("stations.NZM.km", 7.0, "km", "DERIVED_FROM_REAL", ["SRC-TAG-2026-T2"], "medium",
                    ["TAG-2026 published km 10 (Hazrat Nizamuddin)", "TAG-2026 published km 3 (New Delhi)"],
                    "published km minus New Delhi's published km", "Whole kilometres only; +/-1 km."),
        "FDB": prov("stations.FDB.km", 28.0, "km", "DERIVED_FROM_REAL", ["SRC-TAG-2026-T2"], "medium",
                    ["TAG-2026 published km 31 (Faridabad)", "TAG-2026 published km 3 (New Delhi)"],
                    "published km minus New Delhi's published km", "Whole kilometres only; +/-1 km."),
        "PWL": prov("stations.PWL.km", STATION_KM["PWL"], "km", "DERIVED_FROM_REAL",
                    ["SRC-NCR-SIP-RUNDHI", "SRC-NCR-SIP-BAD", "SRC-NCR-SIP-MTJ", "SRC-TAG-2026-T2"], "low",
                    [f"Official km-post Mathura Jn {KMPOST_MTJ}", f"Official km-post Rundhi {KMPOST_RUNDHI}",
                     f"Rundhi SIP: Palwal at Km {RUNDHI_TO_PALWAL_KM}", "MTJ chainage 141 km"],
                    f"MTJ chainage - (Palwal km-post {KMPOST_PALWAL} - MTJ km-post {KMPOST_MTJ}) "
                    f"= 141 - {round(KMPOST_PALWAL - KMPOST_MTJ, 2)}",
                    "UNRESOLVED CONFLICT: the previously used unsourced value was ~57. The official chain gives the PWL-MTJ distance as "
                    "~86.23 km, i.e. ~54.8 km from NDLS. The Rundhi SIP text '(PALWAL SPL CLASS AT Km 9.31)' is read as the Rundhi-Palwal distance. Shown as '~54.8'."),
        "MTJ": prov("stations.MTJ.km", 141.0, "km", "DERIVED_FROM_REAL", ["SRC-TAG-2026-T2", "SRC-NCR-SIP-BAD", "SRC-NCR-SIP-MTJ"], "medium",
                    ["TAG-2026 T-3 published km 144 (Mathura)", "TAG-2026 published km 3 (New Delhi)", f"Official km-post {KMPOST_MTJ}"],
                    "published km minus New Delhi's published km", "TAG T-2 prints 145 (=> 142); T-3 prints 144 (=> 141). +/-1 km. The official km-post 1397.06 is Mumbai-origin, not comparable to the NDLS-relative value."),
        "RKM": prov("stations.RKM.km", STATION_KM["RKM"], "km", "DERIVED_FROM_REAL",
                    ["SRC-NCR-SIP-RUNAKTA", "SRC-NCR-SIP-RKM", "SRC-NCR-SIP-BAD"], "low",
                    [f"Bilochpura km-post {KMPOST_BILOCHPURA} (2025)", f"RKM SIP 'BFP {RKM_TO_BILOCHPURA_KM} Km' (2012)", "MTJ chainage 141 km"],
                    f"MTJ chainage + (MTJ km-post {KMPOST_MTJ} - RKM km-post {KMPOST_RKM}) where RKM km-post = {KMPOST_BILOCHPURA} - {RKM_TO_BILOCHPURA_KM}",
                    "Mixes a 2025 km-post with a 2012 SIP distance (read visually). TAG's own whole-km figure implies ~192; difference recorded in km_uncertainty_km."),
        "AGC": prov("stations.AGC.km", STATION_KM["AGC"], "km", "DERIVED_FROM_REAL",
                    ["SRC-NCR-SIP-RKM", "SRC-NCR-SIP-RUNAKTA", "SRC-NCR-SIP-AGC"], "low",
                    [f"RKM km-post {KMPOST_RKM} (derived)", f"RKM SIP 'AGC {AGC_TO_RKM_KM} Km' (2012)", "MTJ chainage 141 km"],
                    f"MTJ chainage + (MTJ km-post {KMPOST_MTJ} - AGC km-post {KMPOST_AGC}) where AGC km-post = RKM km-post - {AGC_TO_RKM_KM}",
                    "The AGC SIP prints Km 1343.5 and 1344.100 for yard features, consistent with ~1343.3."),
    }
    provenance.extend(km_prov[s["station_id"]] for s in STATIONS)

    provenance.append(prov("stations.*.km_display", {k: v[0] for k, v in KM_DISPLAY.items()}, "km", "ENGINEERING_ASSUMPTION", [], "high",
                           derived_from=None,
                           derivation_method="Display precision chosen to be no finer than the least precise input (whole km, or one decimal for values derived from official km-posts).",))
    for sec in sections:
        zone, division, srcs = SECTION_ADMIN[sec["section_id"]]
        provenance.append(
            prov(f"sections.{sec['section_id']}.zone_division", f"{zone} / {division}", None, "REAL", srcs, "medium",
                 note="NR Delhi Division through Palwal (NR notice 2026-08-31 names the Hazrat Nizamuddin-Palwal section 'over Delhi Division'); NCR Agra Division beyond Palwal (NCR Agra SIP index). NDLS-NZM is taken as Delhi Division per the same division's Nizamuddin section."))
    provenance.append(
        prov("tracks.modelled_lines", 2, "lines", "ENGINEERING_ASSUMPTION", ["SRC-NCR-SIP-FARAH", "SRC-NCR-SIP-SHOLAKA-RUNDHI"], "high",
             derivation_method="Two running lines (one per direction) are modelled. The real corridor has 2 to 4 running lines depending on the section and date (see infrastructure.json); the 3rd/4th lines are not modelled.",))
    provenance.append(
        prov("tracks.speed_limit_kmh", 160, "km/h", "ENGINEERING_ASSUMPTION", [], "low",
             note="Carried over from the existing scoring model (Group A route). No sectional speed limit was established from a public source."))
    provenance.append(
        prov("tracks.daily_train_density", 70, "trains per day per direction", "ENGINEERING_ASSUMPTION", [], "low",
             note="Scoring input. Order of magnitude taken from the 2013 NCR Planning Board description of Delhi-Palwal ('around 70 trains each way'; historical, not stored in sources/). The public passenger timetable alone (about 12 per direction) would understate density because freight and EMU services are not published."))
    provenance.append(
        prov("tracks.route_classification", "GROUP_A", None, "ENGINEERING_ASSUMPTION", [], "low",
             note="Carried over from the existing scoring model."))
    provenance.append(
        prov("tracks.electrified", True, None, "REAL_DATED_SNAPSHOT", ["SRC-CBS-2026-27"], "medium",
             note="Budget item 217 (Mathura-Dholpur-Antri electric traction upgrade) and the 2013 NCR Planning Board description of Delhi-Faridabad-Palwal as an electrified double line."))
    provenance.append(
        prov("tracks.direction_convention", "UP = away from Delhi (NDLS->AGC); DN = toward Delhi (AGC->NDLS)", None, "REAL",
             ["SRC-NR-FDB-ANNEX", "SRC-NCR-SIP-BAD"], "high",
             note="Annexure A labels 12280 (New Delhi-Jhansi) UP and 12279 (Jhansi-New Delhi) DN; NCR SIPs label the Delhi side 'NDLS END'."))

    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "corridor_id": CORRIDOR_ID,
        "name": "New Delhi - Agra Cantt. (via Hazrat Nizamuddin, Faridabad, Palwal, Mathura Jn., Raja Ki Mandi)",
        "zone": "Northern Railway (NDLS-PWL) / North Central Railway (PWL-AGC)",
        "division": "Delhi Division (NDLS-PWL) / Agra Division (PWL-AGC)",
        "distance_unit": "km",
        "chainage_origin": "NDLS = 0 km (derived origin). Chainage is NDLS-relative, not the railway's own km-post.",
        "direction_convention": {
            "UP": "Away from Delhi: NDLS -> AGC.",
            "DOWN": "Toward Delhi: AGC -> NDLS.",
            "track_ids": {
                "UP-1": "Up line - trains running NDLS -> AGC (official UP).",
                "DOWN-1": "Down line - trains running AGC -> NDLS (official DN).",
            },
            "note": "The synthetic fallback dataset uses the opposite meaning for the same identifiers (NDLS->AGC as DOWN-1). Identifiers are kept for contract stability; their meaning is stated per dataset.",
        },
        "stations": stations,
        "sections": sections,
        "tracks": [
            {"track_id": "UP-1", "direction": "UP", "section_name": "NDLS-AGC Up line (away from Delhi)",
             "km_start": 0.0, "km_end": STATION_KM["AGC"], **TRACK_ATTRIBUTES},
            {"track_id": "DOWN-1", "direction": "DOWN", "section_name": "AGC-NDLS Down line (toward Delhi)",
             "km_start": 0.0, "km_end": STATION_KM["AGC"], **TRACK_ATTRIBUTES},
        ],
        "provenance": provenance,
    }


# ----------------------------------------------------------------------
# Timetable. Each train transcribed from TAG-2026 Table T-2 by column position.
# Stops are (station, arrival, departure) exactly as printed; None = not printed
# for that row (an originating station prints only its departure on the 'a' row,
# a passed station prints only a time on its 'd' row, ...). '...' rows (train does
# not stop and no time is printed) are omitted.
# ----------------------------------------------------------------------

# name, from, to, days, [(station, arr, dep)], table page (0-based in the PDF)
TAG_TRAINS: List[Dict[str, Any]] = [
    # ---- UP: NDLS -> AGC ----
    dict(no="12138", direction="UP", name="Punjab Mail", frm="Firozpur", to="Mumbai CSMT", days="Daily", page=0,
         stops=[("NDLS", "04.55", "05.10"), ("FDB", None, "05.40"), ("MTJ", "07.23", "07.28"), ("RKM", None, "07.50"), ("AGC", "08.10", "08.15")]),
    dict(no="12002", direction="UP", name="Shatabdi", frm="New Delhi", to="Rani Kamalapati", days="Daily", page=0,
         stops=[("NDLS", "06.00", None), ("MTJ", "07.19", "07.20"), ("AGC", "07.50", "07.55")]),
    dict(no="12280", direction="UP", name="Taj Express", frm="New Delhi", to="Virangana Laxmibai Jhansi", days="Daily", page=0,
         stops=[("NDLS", "06.55", None), ("NZM", "07.06", "07.08"), ("FDB", None, "07.26"), ("MTJ", "08.35", "08.40"), ("RKM", None, "09.10"), ("AGC", "09.25", "09.30")]),
    dict(no="12050", direction="UP", name="Gatiman Express", frm="Nizamuddin", to="Virangana Laxmibai Jhansi", days="Except F", page=0,
         stops=[("NZM", "08.10", None), ("AGC", "09.50", "09.55")]),
    dict(no="12618", direction="UP", name="Mangala Lakshadweep Express", frm="Nizamuddin", to="Ernakulam", days="Daily", page=0,
         stops=[("NZM", "05.35", None), ("FDB", None, "05.52"), ("MTJ", "07.40", "07.45"), ("AGC", "08.20", "08.25")]),
    dict(no="12406", direction="UP", name="Gondwana Express", frm="Nizamuddin", to="Bhusaval", days="F,Su", page=0,
         stops=[("NZM", "15.05", None), ("MTJ", "16.25", "16.27"), ("AGC", "17.15", "17.20")]),
    dict(no="14212", direction="UP", name="Intercity", frm="New Delhi", to="Agra Cantt.", days="Daily", page=0,
         stops=[("NDLS", "17.40", None), ("NZM", "18.00", "18.01"), ("FDB", None, "18.25"), ("MTJ", "20.13", "20.18"), ("RKM", None, "21.05"), ("AGC", "22.05", None)]),
    dict(no="11058", direction="UP", name="Express", frm="Amritsar", to="Mumbai CSMT", days="Daily", page=0,
         stops=[("NDLS", "20.25", "20.40"), ("NZM", "20.51", "20.53"), ("FDB", None, "21.12"), ("MTJ", "22.50", "22.55"), ("AGC", "23.45", "23.50")]),
    dict(no="12156", direction="UP", name="Bhopal Express", frm="Nizamuddin", to="Rani Kamalapati", days="Daily", page=0,
         stops=[("NZM", "20.40", None), ("AGC", "22.38", "22.40")]),
    dict(no="11842", direction="UP", name="Express", frm="Kurukshetra Jn.", to="Khajuraho Jn.", days="Daily", page=1,
         stops=[("NDLS", "18.05", "18.20"), ("NZM", "18.31", "18.33"), ("FDB", None, "18.52"), ("MTJ", "20.35", "20.40"), ("AGC", "21.25", "21.30")]),
    dict(no="22222", direction="UP", name="Rajdhani Express", frm="Nizamudin", to="Mumbai CSMT", days="Daily", page=1,
         stops=[("NZM", "16.55", None), ("AGC", "18.45", "18.47")]),
    dict(no="20172", direction="UP", name="Vande Bharat Express", frm="Nizamuddin", to="Rani Kamalapati", days="Except Sa", page=1,
         stops=[("NZM", "14.40", None), ("AGC", "16.16", "16.20")]),
    # ---- DN: AGC -> NDLS (times listed in travel order) ----
    dict(no="14211", direction="DN", name="Intercity Express", frm="Agra Cantt.", to="New Delhi", days="Daily", page=2,
         stops=[("AGC", "05.45", None), ("RKM", None, "05.53"), ("MTJ", "06.20", "06.25"), ("FDB", None, "08.51"), ("NZM", "09.15", "09.17"), ("NDLS", "10.00", None)]),
    dict(no="12617", direction="DN", name="Mangala Lakshadweep Express", frm="Ernakulam", to="Nizamuddin", days="Daily", page=2,
         stops=[("AGC", "09.55", "10.00"), ("MTJ", "10.33", "10.38"), ("FDB", None, "12.50"), ("NZM", "13.35", None)]),
    dict(no="12049", direction="DN", name="Gatiman Express", frm="Virangana Laxmibai Jhansi", to="Nizamuddin", days="Except F", page=2,
         stops=[("AGC", "17.30", "17.35"), ("NZM", "19.30", None)]),
    dict(no="12137", direction="DN", name="Punjab Mail", frm="Mumbai CSMT", to="Firozpur", days="Daily", page=2,
         stops=[("AGC", "18.00", "18.05"), ("RKM", None, "18.13"), ("MTJ", "18.45", "18.50"), ("FDB", None, "20.35"), ("NZM", "20.55", "20.57"), ("NDLS", "21.25", "21.40")]),
    dict(no="12279", direction="DN", name="Taj Express", frm="Virangana Laxmibai Jhansi", to="New Delhi", days="Daily", page=2,
         stops=[("AGC", "18.15", "18.20"), ("RKM", None, "18.28"), ("MTJ", "19.00", "19.05"), ("FDB", None, "20.45"), ("NZM", "21.05", "21.07"), ("NDLS", "21.35", None)]),
    dict(no="12001", direction="DN", name="Shatabdi Express", frm="Rani Kamlapati", to="New Delhi", days="Daily", page=2,
         stops=[("AGC", "21.20", "21.25"), ("MTJ", "22.00", "22.01"), ("NDLS", "23.50", None)]),
    dict(no="11057", direction="DN", name="Express", frm="Mumbai CSMT", to="Amritsar", days="Daily", page=2,
         stops=[("AGC", "23.45", "23.50"), ("MTJ", "00.30", "00.35"), ("FDB", None, "02.47"), ("NZM", "03.07", "03.09"), ("NDLS", "03.30", "03.45")]),
    dict(no="12155", direction="DN", name="Bhopal Express", frm="Rani Kamlapati", to="Nizamuddin", days="Daily", page=2,
         stops=[("AGC", "05.15", "05.17"), ("NZM", "08.00", None)]),
    dict(no="11841", direction="DN", name="Express", frm="Khajuraho Jn.", to="Kurukshetra Jn.", days="Daily", page=3,
         stops=[("AGC", "03.50", "03.55"), ("MTJ", "05.00", "05.05"), ("FDB", None, "07.33"), ("NZM", "08.12", "08.14"), ("NDLS", "08.45", "09.00")]),
    dict(no="22221", direction="DN", name="Rajdhani Express", frm="Mumbai CSMT", to="Nizamudin", days="Daily", page=3,
         stops=[("AGC", "07.30", "07.32"), ("NZM", "09.55", None)]),
    dict(no="20171", direction="DN", name="Vande Bharat Express", frm="Rani Kamalapati", to="Nizamuddin", days="Except Sa", page=3,
         stops=[("AGC", "11.01", "11.05"), ("NZM", "13.16", None)]),
]

# Trains in T-2 that touch the corridor but were NOT ingested, and why. Recorded
# so the omission is explicit rather than silent.
EXCLUDED_TRAINS = {
    "20424": "Firozpur-Seoni Patalkot: T-2 prints only MTJ and AGC times; the Delhi-side timing is not in this table.",
    "20423": "Seoni Patalkot-Firozpur: only AGC and MTJ printed.",
    "11906": "Hoshiarpur-Agra Cantt.: only MTJ and AGC printed; the corridor entry point is not in this table.",
    "11905": "Agra Cantt.-Hoshiarpur: only AGC and MTJ printed.",
    "12172": "Haridwar-LTT AC: non-daily and originates off-corridor; day offset at NZM not established from T-2.",
    "12171": "LTT-Haridwar: non-daily and originates off-corridor.",
    "22456/22710/12405/22129": "Non-daily trains originating off the corridor; origin-day offset not established from this table.",
    "12154/12153/12162/14314/14320/22548": "Touch AGC only (no corridor section traversed), or times printed only at AGC.",
}

DAY_LETTERS = ["M", "Tu", "W", "Th", "F", "Sa", "Su"]  # Python weekday() order


def parse_days(text: str) -> Tuple[str, List[str]]:
    """TAG days-of-departure text -> the day letters a train runs."""

    t = text.strip()
    if t == "Daily":
        return "Daily", list(DAY_LETTERS)
    if t.startswith("Except "):
        skip = {x.strip() for x in t[len("Except "):].split(",")}
        assert skip <= set(DAY_LETTERS), t
        return t, [d for d in DAY_LETTERS if d not in skip]
    run = [x.strip() for x in t.split(",")]
    assert set(run) <= set(DAY_LETTERS), t
    return t, run


def parse_clock(text: str) -> int:
    hh, mm = text.split(".")
    h, m = int(hh), int(mm)
    assert 0 <= h < 24 and 0 <= m < 60, text
    return h * 60 + m


def fmt_clock(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


STATION_ORDER = [s["station_id"] for s in STATIONS]
STATION_NAME = {s["station_id"]: s["name"] for s in STATIONS}


def _unwrap(clock_events: List[int]) -> List[int]:
    """Make a train's day-less clock minutes monotonic by adding 1440 on rollover."""

    out: List[int] = []
    day = 0
    prev: Optional[int] = None
    for c in clock_events:
        if prev is not None and c + day * 1440 < prev:
            day += 1
        out.append(c + day * 1440)
        prev = out[-1]
    return out


def derive_station_times(train: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
    """Published stops + chainage-interpolated passing times for unlisted stations.

    Returns (stops in travel order, last_event_day_offset). An interpolated
    stop is flagged: its time is DERIVED, not published.
    """

    stops = train["stops"]
    # 1. published events in travel order, absolute minutes from the first
    #    modelled stop's calendar day.
    clock = []
    for st, arr, dep in stops:
        a = arr or dep
        d = dep or arr
        clock.extend([parse_clock(a), parse_clock(d)])
    absolute = _unwrap(clock)

    published = []
    for i, (st, arr, dep) in enumerate(stops):
        published.append(
            {
                "station_id": st,
                "arr": absolute[2 * i],
                "dep": absolute[2 * i + 1],
                "tag_arrival": arr,
                "tag_departure": dep,
            }
        )

    # 2. chainage interpolation for corridor stations strictly between two
    #    published stops in travel order.
    result: List[Dict[str, Any]] = []
    for i, cur in enumerate(published):
        result.append({**cur, "basis": "TAG_PUBLISHED"})
        if i + 1 == len(published):
            break
        nxt = published[i + 1]
        a_km, b_km = STATION_KM[cur["station_id"]], STATION_KM[nxt["station_id"]]
        lo, hi = sorted((a_km, b_km))
        between = [s for s in STATION_ORDER if lo < STATION_KM[s] < hi]
        if a_km > b_km:
            between.reverse()
        span = abs(b_km - a_km)
        t0, t1 = cur["dep"], nxt["arr"]
        prev_t = t0
        for s in between:
            frac = abs(STATION_KM[s] - a_km) / span
            t = int(round(t0 + frac * (t1 - t0)))
            assert prev_t < t < t1, (train["no"], s, prev_t, t, t1)
            result.append(
                {
                    "station_id": s,
                    "arr": t,
                    "dep": t,
                    "tag_arrival": None,
                    "tag_departure": None,
                    "basis": "INTERPOLATED_BY_CHAINAGE",
                }
            )
            prev_t = t

    last_offset = max(r["dep"] for r in result) // 1440
    return result, last_offset


def build_timetable() -> Dict[str, Any]:
    trains_out: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []

    horizon_dates = [HORIZON_START_DATE + timedelta(days=i) for i in range(HORIZON_DAYS)]
    carry_in_date = HORIZON_START_DATE - timedelta(days=1)

    for train in TAG_TRAINS:
        days_text, run_days = parse_days(train["days"])
        stops, last_offset = derive_station_times(train)
        track = "UP-1" if train["direction"] == "UP" else "DOWN-1"
        official_direction = train["direction"]

        # direction/chainage sanity: travel order must follow the direction.
        order_km = [STATION_KM[s["station_id"]] for s in stops]
        assert order_km == sorted(order_km, reverse=(official_direction == "DN")), train["no"]

        trains_out.append(
            {
                "train_number": train["no"],
                "train_name": train["name"],
                "from_station_name": train["frm"],
                "to_station_name": train["to"],
                "direction": official_direction,
                "track_assignment": track,
                "days_of_departure_tag": days_text,
                "runs_on": run_days,
                "origin_day_semantics": (
                    "TAG prints days of departure at the ORIGINATING station. For every ingested train the first "
                    "modelled corridor stop falls on that same calendar day (or the train is daily), so the "
                    "weekday pattern applies to the date of the first modelled stop (day offset 0)."
                ),
                "tag_table": "T-2",
                "tag_pdf_page": train["page"] + 1,
                "published_stops": [
                    {"station_id": s, "tag_arrival": a, "tag_departure": d} for s, a, d in train["stops"]
                ],
                "crosses_midnight_within_corridor": last_offset >= 1,
                "verification": "Column located by train-number position in the stored layout extract; every printed time re-checked by tests/test_real_snapshot_sources.py; FDB times cross-checked against NR Annexure-A where the train appears.",
            }
        )

        candidate_dates = list(horizon_dates)
        if last_offset >= 1:
            candidate_dates.insert(0, carry_in_date)

        for d in candidate_dates:
            letter = DAY_LETTERS[d.weekday()]
            if letter not in run_days:
                continue
            station_times = []
            for s in stops:
                st = {
                    "station_id": s["station_id"],
                    "station_name": STATION_NAME[s["station_id"]],
                    "scheduled_arrival": fmt_clock(s["arr"]),
                    "scheduled_departure": fmt_clock(s["dep"]),
                    "time_basis": s["basis"],
                }
                station_times.append(st)
            records.append(
                {
                    "train_number": train["no"],
                    "service_date": d.isoformat(),
                    "train_name": train["name"],
                    "direction": "UP" if official_direction == "UP" else "DOWN",
                    "track_assignment": track,
                    "origin": stops[0]["station_id"],
                    "destination": stops[-1]["station_id"],
                    "full_route_from": train["frm"],
                    "full_route_to": train["to"],
                    "days_of_departure_tag": days_text,
                    "route": [s["station_id"] for s in stops],
                    "station_times": station_times,
                    "delay_minutes": 0,
                    "status": "SCHEDULED_PER_PUBLIC_TIMETABLE",
                    "source_ids": ["SRC-TAG-2026-T2"],
                    "carry_in": d == carry_in_date,
                }
            )

    records.sort(key=lambda r: (r["service_date"], r["direction"], r["station_times"][0]["scheduled_departure"], r["train_number"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "timetable_id": "TAG-2026-T2-verified-subset",
        "source_ids": ["SRC-TAG-2026-T2", "SRC-TAG-2026-T2-EXTRACT", "SRC-NR-FDB-ANNEX"],
        "publication": {
            "title": "Indian Railways Trains at a Glance 2026, Table T-2",
            "effective": "2026-01-01",
            "kind": "PUBLIC_PASSENGER_TIMETABLE",
        },
        "coverage": {
            "ingested_trains": len(trains_out),
            "up_trains": sum(1 for t in trains_out if t["direction"] == "UP"),
            "dn_trains": sum(1 for t in trains_out if t["direction"] == "DN"),
            "service_dates": sorted({r["service_date"] for r in records}),
            "horizon_dates": [d.isoformat() for d in horizon_dates],
            "carry_in_dates": [carry_in_date.isoformat()],
            "not_included": [
                "Trains listed only in other TAG tables (T-3, T-8, T-9, ...) that also use NDLS-MTJ or PWL-MTJ",
                "EMU/MEMU/local trains (the 64xxx services named in Northern Railway notices)",
                "Freight and light-engine/departmental movements",
                "The working timetable (WTT) and any as-run movements",
                "Notices after 2026-01-01 other than the Faridabad block, which is not applied to the timetable",
            ],
            "excluded_trains_in_t2": EXCLUDED_TRAINS,
            "label": "Public passenger timetable subset. NOT complete corridor occupancy.",
        },
        "derivation": {
            "record_construction": "Each verified train is expanded to every horizon date it runs on (TAG days-of-departure applied to the calendar date of its first modelled stop).",
            "carry_in": "A train whose modelled run crosses midnight is also emitted for the day before the horizon so its early-horizon occupancy is present (11057 only).",
            "passing_times": "Corridor stations with no printed time between two published stops receive a passing time interpolated linearly by chainage between the previous published departure and the next published arrival (time_basis=INTERPOLATED_BY_CHAINAGE). This is an engineering assumption; it is applied so that section occupancy is not overstated by attributing a whole multi-section leg to every section.",
            "fdb_cross_check": "TAG time at FDB is compared with the PTT time in NR Annexure-A wherever the train appears in both (tests).",
        },
        "trains": trains_out,
        "service_records": records,
        "provenance": [
            prov("timetable.published_times", "TAG-2026 T-2 (23 verified trains)", "HH.MM local", "REAL_DATED_SNAPSHOT",
                 ["SRC-TAG-2026-T2", "SRC-TAG-2026-T2-EXTRACT"], "high",
                 note="Effective 2026-01-01. A scheduled pattern, not as-run movements."),
            prov("timetable.interpolated_passing_times", "linear by chainage", "HH:MM", "DERIVED_FROM_REAL",
                 ["SRC-TAG-2026-T2"], "medium",
                 derived_from=["published stop times", "station chainage"],
                 derivation_method="linear interpolation by chainage between adjacent published stops"),
            prov("timetable.service_dates", [d.isoformat() for d in horizon_dates], "date", "DERIVED_FROM_REAL",
                 ["SRC-TAG-2026-T2"], "medium",
                 derived_from=["TAG days-of-departure"],
                 derivation_method="weekday pattern applied to the calendar date of the first modelled stop; 2026-09-10 is a Thursday, 2026-09-11 a Friday"),
            prov("timetable.freight_emu_wtt", None, None, "UNAVAILABLE_PUBLICLY", [], "high",
                 note="Freight schedules, EMU/MEMU timetables and the working timetable are not published; none is invented."),
        ],
    }


# ----------------------------------------------------------------------
# Infrastructure, works, blocks, rules
# ----------------------------------------------------------------------


def build_infrastructure() -> Dict[str, Any]:
    kmposts = [
        ("Bilochpura", KMPOST_BILOCHPURA, "SRC-NCR-SIP-RUNAKTA", "2025-10-09", "Runakta SIP: 'BILOCHPURA STN. AT KM: 1348.60'", "1348.60"),
        ("Runakta", 1357.67, "SRC-NCR-SIP-RUNAKTA", "2025-10-09", "Runakta SIP: 'RLY.Km - 1357.670'", "1357.670"),
        ("Kitham", 1366.65, "SRC-NCR-SIP-KITHAM", "2025-10-14", "Kitham SIP: 'RLY.Km - 1366.650'", "1366.650"),
        ("Farah", 1378.15, "SRC-NCR-SIP-FARAH", "2025-10-14", "Farah SIP: 'FARAH (RLY.KM.1378.15)'", "1378.15"),
        ("Bad", 1386.84, "SRC-NCR-SIP-FARAH", "2025-10-14", "Farah SIP: 'BAAD AT KM:1386.84 (DISTANCE 8.69 KMS)'; the Bad SIP prints 1386/797.85 for the station building (42 m apart)", "1386.84"),
        ("Mathura Jn", KMPOST_MTJ, "SRC-NCR-SIP-BAD", "2025-10-24", "Bad SIP: '(MATHURA STN. 10.22Km) KM. 1397.06M'; Mathura Jn SIP (2017): 'AT KM 1397.06 AS PER W.T.T.'", "1397.06"),
        ("Rundhi", KMPOST_RUNDHI, "SRC-NCR-SIP-RUNDHI", "2021-12-03", "Rundhi SIP: 'RUNDHI STATION ... KM: 1473.981'", "1473.981"),
    ]
    level_crossings = [
        (514, "SPL", "1368.60", "SRC-NCR-SIP-KITHAM", "2025-10-14", "LX NO. 514 'SPL' CLASS KM - 1368.60", "1368.60"),
        (515, "C", "1370.25", "SRC-NCR-SIP-KITHAM-FARAH", "2025-10-14", "LX NO. 515 'C' CLASS KM - 1370.25", "1370.25"),
        (523, "SPL", "1383/25-27", "SRC-NCR-SIP-FARAH-BAD", "2025-10-14", "LX NO. 523 'SPL' CLASS KM - 1383/25-27", "1383/25-27"),
        (551, "SPL", "1447.084", "SRC-NCR-SIP-HODAL", "2021-12-03", "LC GATE NO.551 ... KMS 1447.084", "1447.084"),
        (552, "B", "1450.082", "SRC-NCR-SIP-HODAL", "2021-12-03", "LC GATE NO.552 ... KMS 1450.082", "1450.082"),
        (554, "SPL/E", "1452.576", "SRC-NCR-SIP-HODAL", "2021-12-03", "LC GATE NO.554 ... KMS 1452.576", "1452.576"),
        (559, "C", "1463.441", "SRC-NCR-SIP-SHOLAKA-RUNDHI", "2021-12-03", "LC GATE NO.559 ... KMS 1463.441", "1463.441"),
        (560, "SPL", "1467.536", "SRC-NCR-SIP-SHOLAKA-RUNDHI", "2021-12-03", "LC GATE NO.560 ... KMS 1467.536", "1467.536"),
        (561, "SPL/T", "1470.347", "SRC-NCR-SIP-RUNDHI", "2021-12-03", "LC GATE NO.561 ... KMS 1470.347", "1470.347"),
    ]
    lines = [
        {"location": "Sholaka-Rundhi (block section)", "running_lines": "UP main, DN main, 3rd line, 4th line", "count": 4,
         "document_date": "2021-12-03", "source_id": "SRC-NCR-SIP-SHOLAKA-RUNDHI", "verification": "text layer: '4TH LINE 3RD LINE DN MN LINE UP MN LINE'", "quote_check": "4TH LINE"},
        {"location": "Kitham / Farah / Bad", "running_lines": "UP main, DN main, 3rd main line", "count": 3,
         "document_date": "2025-10-14", "source_id": "SRC-NCR-SIP-FARAH", "verification": "text layer: '3rd MAIN LINE'", "quote_check": "3rd MAIN LINE"},
        {"location": "Raja Ki Mandi", "running_lines": "UP main, DN main", "count": 2,
         "document_date": "2012-03-20", "source_id": "SRC-NCR-SIP-RKM", "verification": "visual reading of a 2012 drawing", "quote_check": None},
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "kilometre_post_datum": "Railway km-posts on this route run from the Mumbai end and INCREASE toward Delhi (NDLS end). They are not comparable to the NDLS-relative chainage in topology.json.",
        "kilometre_posts": [
            {"place": p, "km_post": km, "source_id": s, "document_date": d, "evidence": e, "quote_check": q,
             "class": "REAL_DATED_SNAPSHOT"}
            for p, km, s, d, e, q in kmposts
        ]
        + [
            {"place": "Palwal", "km_post": KMPOST_PALWAL, "source_id": "SRC-NCR-SIP-RUNDHI", "document_date": "2021-12-03",
             "evidence": f"DERIVED: Rundhi {KMPOST_RUNDHI} + 9.31 ('PALWAL SPL CLASS AT Km 9.31')", "quote_check": "Km 9.31", "class": "DERIVED_FROM_REAL"},
            {"place": "Raja Ki Mandi", "km_post": KMPOST_RKM, "source_id": "SRC-NCR-SIP-RKM", "document_date": "2012-03-20",
             "evidence": f"DERIVED: Bilochpura {KMPOST_BILOCHPURA} - 1.45 (RKM SIP, 2012)", "quote_check": None, "class": "DERIVED_FROM_REAL"},
            {"place": "Agra Cantt.", "km_post": KMPOST_AGC, "source_id": "SRC-NCR-SIP-RKM", "document_date": "2012-03-20",
             "evidence": f"DERIVED: RKM {KMPOST_RKM} - 3.88 (RKM SIP, 2012); the AGC SIP prints Km 1343.5 / 1344.100 for yard features", "quote_check": None, "class": "DERIVED_FROM_REAL"},
        ],
        "level_crossings": [
            {"lc_no": n, "class_of_lc": c, "km_post": km, "source_id": s, "document_date": d, "evidence": e, "quote_check": q,
             "class": "REAL_DATED_SNAPSHOT"}
            for n, c, km, s, d, e, q in level_crossings
        ],
        "running_lines": [{**l, "class": "REAL_DATED_SNAPSHOT"} for l in lines],
        "automatic_signalling_sections": [
            {"section": "Bilochpura-Runakta", "evidence": "SIP titled 'BILOCHPURA - RUNAKTA AUTO' on the NCR Agra SIP index", "source_id": "SRC-NCR-SIP-INDEX", "document_date": "2026-09-09", "class": "REAL_DATED_SNAPSHOT"},
            {"section": "Runakta-Kitham", "evidence": "SIP titled 'RUNKUTA -KITHAM AUTO'", "source_id": "SRC-NCR-SIP-INDEX", "document_date": "2026-09-09", "class": "REAL_DATED_SNAPSHOT"},
            {"section": "Hodal-Sholaka", "evidence": "SIP titled 'AUTO HODAL SHOLAKA'", "source_id": "SRC-NCR-SIP-INDEX", "document_date": "2026-09-09", "class": "REAL_DATED_SNAPSHOT"},
            {"section": "Sholaka-Rundhi", "evidence": "SIP text 'AUTOMATIC SIGNALLING BETWEEN SHOLAKA-RUNDHI SECTION'", "source_id": "SRC-NCR-SIP-SHOLAKA-RUNDHI", "document_date": "2021-12-03", "class": "REAL_DATED_SNAPSHOT", "quote_check": "AUTOMATIC SIGNALLING"},
            {"section": "Rundhi-Palwal", "evidence": "SIP titled 'AUTO SIG RUNDHI PALWAL'", "source_id": "SRC-NCR-SIP-INDEX", "document_date": "2026-09-09", "class": "REAL_DATED_SNAPSHOT"},
        ],
        "kavach": [
            {"section": "Palwal-Mathura-Nagda (633 route-km)", "commissioned": "Kavach 4.0", "date": "2025-12-05", "source_id": "SRC-PIB-KAVACH", "class": "REAL_DATED_SNAPSHOT", "note": "PIB 2025-12-05 headline: 'Kavach 4.0 Commissioned on 738 Route km Across Palwal-Mathura-Nagda Section (633 Rkm) ... and Howrah-Bardhaman Section (105 Rkm)'."},
            {"section": "Tughlakabad Jn Cabin-Palwal (35 route-km)", "commissioned": "Kavach 4.0", "date": "2026-01-30", "source_id": "SRC-PIB-KAVACH", "class": "REAL_DATED_SNAPSHOT", "note": "PIB 2026-01-30 headline names 'Tuglakabad Junction Cabin-Palwal (35 km)' on Northern Railway."},
        ],
        "electrification": [
            {"statement": "Delhi-Faridabad-Palwal is a double line electrified section (as described in 2013).", "document_date": "2013-01-10", "source_id": None, "source_ref": "NCR Planning Board, Functional Plan on Transport for NCR-2032, chapter 4", "class": "REAL_DATED_SNAPSHOT", "note": "Historical; not stored in sources/ (public document, https://ncrpb.nic.in/pdf_files/Chapter%204_FNPLTr_Rail.%20Net.pdf)."},
            {"statement": "Budget item 217: 'Mathura-Dholpur-Antri section - Upgradation work of Electric Traction System' (Rs 498.89 crore), i.e. an electrified section under upgrade.", "document_date": "2026-02-04", "source_id": "SRC-CBS-2026-27", "class": "REAL_DATED_SNAPSHOT"},
        ],
        "not_established": [
            "Number of running lines per section as of today (SIPs are dated 2012-2025; the 4th line Mathura-Palwal and Tughlakabad-Palwal are still listed as sanctioned works in 2026-27).",
            "Kavach on NDLS-NZM-Tughlakabad and on Mathura-Agra Cantt.",
            "Sectional speed limits, OHE/bridge inventories, asset condition (see UNAVAILABLE_PUBLICLY).",
        ],
        "unavailable_publicly": prov("infrastructure.asset_condition_and_inventories", None, None, "UNAVAILABLE_PUBLICLY", [], "high",
                                     note="No public source gives defect-level asset condition, inspection results or asset inventories for this corridor."),
        "intermediate_stations_note": "About 25 intermediate stations (Bilochpura, Runakta, Kitham, Farah, Bad, Bhuteshwar, Vrindavan, Ajhai, Chhata, Kosi Kalan, Hodal, Sholaka, Rundhi, ... and Okhla, Tughlakabad, Faridabad New Town, Ballabgarh, Asaoti on the NR side) exist. They are reference data only: adding them as registry sections would change every section_id used by jobs, scope and authorization.",
    }


WORKS_GROUNDING = [
    {
        "demo_job_key": "signal-fdb-pwl",
        "job_type": "SIGNALLING_INTERLOCKING",
        "section_id": "FDB-PWL",
        "grounding_items": [("NR", 1575)],
        "summary": "Strengthening of the UP/DN main-line track circuits, Okhla-Palwal ('A' route), Delhi Division",
    },
    {
        "demo_job_key": "ballast-rkm-agc",
        "job_type": "BALLAST_TAMPING",
        "section_id": "RKM-AGC",
        "grounding_items": [("NCR", 102), ("NCR", 110)],
        "summary": "Complete track renewal (33.439 km) and through sleeper renewal (57.810 Tkm), Agra Cantt.-Mathura",
    },
    {
        "demo_job_key": "inspection-mtj-rkm",
        "job_type": "ROUTINE_INSPECTION",
        "section_id": "MTJ-RKM",
        "grounding_items": [("NCR", 115)],
        "summary": "Through rail renewal, Agra-Palwal (66.902 km)",
    },
    {
        "demo_job_key": "fracture-mtj-rkm",
        "job_type": "EMERGENCY_REPAIR",
        "section_id": "MTJ-RKM",
        "grounding_items": [("NCR", 115)],
        "summary": "Through rail renewal, Agra-Palwal (66.902 km)",
    },
    {
        "demo_job_key": "ohe-rkm-agc",
        "job_type": "OHE_MAINTENANCE",
        "section_id": "RKM-AGC",
        "grounding_items": [("NCR", 217)],
        "summary": "Upgradation of the Electric Traction System, Mathura-Dholpur-Antri (covers Mathura-Agra Cantt.)",
    },
]

DEMO_JOB_WORDING = (
    "GROUNDING: REAL RAILWAY WORK / PROJECT (sanctioned in the 2026-27 budget). "
    "The field observation itself is a DEMO MAINTENANCE INPUT: it is not evidence that a defect "
    "exists at that location."
)


def build_works() -> Dict[str, Any]:
    cbs = json.loads((SOURCES_DIR / "CBS_2026-27_excerpts.json").read_text(encoding="utf-8"))
    by_key = {(i["zone"], i["item_no"]): i for i in cbs["items"]}

    def item(zone: str, no: int) -> Dict[str, Any]:
        i = by_key[(zone, no)]
        return {"zone": zone, "item_no": no, "pdf_page": i["pdf_page"], "text": i["text"]}

    listed = [(z, n) for z, n in sorted(by_key)]
    grounded = []
    for w in WORKS_GROUNDING:
        grounded.append(
            {
                "demo_job_key": w["demo_job_key"],
                "job_type": w["job_type"],
                "section_id": w["section_id"],
                "grounding_summary": w["summary"],
                "grounding_items": [item(z, n) for z, n in w["grounding_items"]],
                "grounding_class": "REAL_DATED_SNAPSHOT",
                "observation_class": "DEMO_MAINTENANCE_INPUT",
                "wording": DEMO_JOB_WORDING,
                "not_evidence_of_defect": True,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "source_ids": ["SRC-CBS-2026-27"],
        "as_of": "2026-02-04",
        "note": "Sanctioned works from the 2026-27 Consolidated Budget Statements. A sanctioned work is a real work category on a real section; it does not show that a defect exists at any demo job's location.",
        "sanctioned_works": [item(z, n) for z, n in listed],
        "demo_job_grounding": grounded,
        "provenance": [
            prov("works.sanctioned_items", [f"{z}-{n}" for z, n in listed], None, "REAL_DATED_SNAPSHOT", ["SRC-CBS-2026-27"], "high",
                 note="Item numbers and page numbers refer to the 2026-27 CBS PDFs (hashes in the excerpt file)."),
            prov("works.demo_job_observations", "5 demo field observations", None, "DEMO_MAINTENANCE_INPUT", ["SRC-CBS-2026-27"], "high",
                 note="Locations, severities and descriptions of the demo jobs are controlled demonstration inputs grounded in the work categories above."),
        ],
    }


def build_blocks() -> Dict[str, Any]:
    annex = json.loads((SOURCES_DIR / "NR_FDB_annexure_A_parsed.json").read_text(encoding="utf-8"))
    rows = annex["rows"]
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "usage": "EVIDENCE AND CONTEXT ONLY. Neither block below is applied to the timetable or to possession derivation, and neither is a live feed.",
        "blocks": [
            {
                "block_id": "NR-FDB-2026-09",
                "kind": "POWER_AND_TRAFFIC_BLOCK",
                "status_at_retrieval": "IN_PROGRESS (01-09-2026 to 15-10-2026)",
                "location": "Faridabad station, platform lines 4 and 5 (platforms 2 and 3), Hazrat Nizamuddin-Palwal section, Delhi Division",
                "purpose": "Redevelopment of Faridabad station",
                "period_from": "2026-09-01",
                "period_to": "2026-10-15",
                "scope_note": "A station-yard block on platform lines. It is not a block-section possession on the running lines modelled here.",
                "cancelled_trains": [
                    {"train": "64908", "from": "Shakurbasti", "to": "Ballabgarh"},
                    {"train": "64071", "from": "Ballabgarh", "to": "Shakurbasti"},
                    {"train": "64052", "from": "Ghaziabad", "to": "Palwal"},
                    {"train": "64015", "from": "Palwal", "to": "Shakurbasti"},
                    {"train": "64078", "from": "New Delhi", "to": "Palwal"},
                    {"train": "64057", "from": "Palwal", "to": "Ghaziabad"},
                    {"train": "64012", "from": "Shakurbasti", "to": "Palwal"},
                    {"train": "64013", "from": "Palwal", "to": "Shakurbasti"},
                    {"train": "64076", "from": "New Delhi", "to": "Palwal", "dates": "01.09.2026 and 15.10.2026"},
                ],
                "regulated_trains": [
                    {"train": "19019", "detail": "Bandra Terminus-Haridwar Jn, regulated 45 minutes between Palwal and Faridabad New Town on 31.08.2026 and 14.10.2026"}
                ],
                "stoppage_skips_note": "12 UP and 8 DN trains skip Faridabad for the period (see the notice).",
                "platform_changes": {"trains_up": sum(1 for r in rows if r["direction"] == "UP"),
                                     "trains_dn": sum(1 for r in rows if r["direction"] == "DN"),
                                     "from_platforms_to": "UP: platform 3 -> 4; DN: platform 2 -> 1"},
                "annexure_a_rows": rows,
                "timings_note": "Daily block timings are not stated in the notice text retrieved. Secondary news reports give 00:05-03:05; they are not used.",
                "source_ids": ["SRC-NR-FDB-NOTICE", "SRC-NR-FDB-ANNEX", "SRC-NR-FDB-ANNEX-PARSED"],
                "class": "REAL_DATED_SNAPSHOT",
                "used_in_derivation": False,
            },
            {
                "block_id": "CR-MTJ-2024-01",
                "kind": "NON_INTERLOCKING_WORK_YARD_REMODELLING",
                "status_at_retrieval": "HISTORICAL",
                "location": "Mathura Junction, Agra Division, North Central Railway",
                "purpose": "Yard remodelling",
                "period_from": "2024-01-10",
                "period_to": "2024-02-07",
                "period_note": "Cancellation periods of individual trains in the notice range from 10-01-2024 to 07-02-2024.",
                "source_ids": ["SRC-CR-MTJ-2024"],
                "class": "REAL_DATED_SNAPSHOT",
                "used_in_derivation": False,
            },
        ],
        "policy_context": {
            "statement": "Indian Railways plans fixed corridor blocks of at least 3 hours in each section in divisional working timetables (PIB, 2022).",
            "source_ids": ["SRC-PIB-2022-CORRIDOR-BLOCK"],
            "class": "REAL",
            "limitation": "The actual corridor-block timings of the Delhi and Agra divisions are not public. The 3-hour figure is context; it is not applied as a filter.",
        },
        "provenance": [
            prov("blocks.faridabad_2026", "power and traffic block 2026-09-01..2026-10-15", None, "REAL_DATED_SNAPSHOT", ["SRC-NR-FDB-NOTICE", "SRC-NR-FDB-ANNEX"], "high"),
            prov("blocks.mathura_2024", "yard remodelling block 2024-01-10..2024-02-07", None, "REAL_DATED_SNAPSHOT", ["SRC-CR-MTJ-2024"], "high"),
            prov("blocks.live_possession_feed", None, None, "UNAVAILABLE_PUBLICLY", [], "high",
                 note="Actual possession grants, block registers, caution orders and temporary speed restrictions are not public."),
        ],
    }


DEMO_ASSET_SEED = 42
DEMO_ASSETS_PER_TRACK = 24


def build_rules() -> Dict[str, Any]:
    from backend.app.data.timetable_adapter import (
        DEFAULT_MINIMUM_WINDOW_MINUTES,
        DEFAULT_SAFETY_BUFFER_MINUTES,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "engineering_rules": {
            "safety_buffer_minutes": DEFAULT_SAFETY_BUFFER_MINUTES,
            "minimum_window_minutes": DEFAULT_MINIMUM_WINDOW_MINUTES,
            "occupancy": "Every section between a train's consecutive (published or interpolated) stops is occupied for the whole leg, extended by the safety buffer at both ends.",
            "windows": "Train-free gaps of at least the minimum window per (track, section); a section any rejected train might have occupied yields no window.",
            "label": "CANDIDATE POSSESSION WINDOW - DERIVED FROM PUBLIC PASSENGER TIMETABLE",
            "coverage_statement": "Timetable source: TAG-2026 (23 trains). Freight, EMU/MEMU and the working timetable are NOT included, so a derived window may overstate free track.",
        },
        "demo_inputs": {
            "asset_condition": {
                "class": "DEMO_MAINTENANCE_INPUT",
                "generator": "backend.app.data.generator.CorridorDataGenerator.generate_assets",
                "asset_seed": DEMO_ASSET_SEED,
                "assets_per_track": DEMO_ASSETS_PER_TRACK,
                "note": "Asset condition, criticality and failure history are generated demo inputs: no public source exists. "
                        "24 assets per track (about 8 km apart) keep every reportable location near a demo asset "
                        "(the association policy refuses a report more than 20 km from any asset).",
            },
        },
        "provenance": [
            prov("rules.demo_asset_condition", "generated, seed 42, 24 assets per track", None, "DEMO_MAINTENANCE_INPUT", [], "high",
                 note="Deterministic generator output; not an asset register."),
            prov("rules.safety_buffer_minutes", DEFAULT_SAFETY_BUFFER_MINUTES, "min", "ENGINEERING_ASSUMPTION", [], "medium",
                 note="The adapter's existing default. Not an Indian Railways rule."),
            prov("rules.minimum_window_minutes", DEFAULT_MINIMUM_WINDOW_MINUTES, "min", "ENGINEERING_ASSUMPTION", [], "medium",
                 note="The adapter's existing default. Not an Indian Railways rule."),
            prov("possession.candidate_windows", "derived", "minutes from horizon start", "DERIVED_FROM_REAL", ["SRC-TAG-2026-T2"], "low",
                 ["timetable.published_times", "timetable.interpolated_passing_times", "rules.safety_buffer_minutes"],
                 "section traversal -> occupancy interval (+buffer) -> merged train-free gaps >= minimum window",
                 "Not a real possession grant. Freight, EMU and the working timetable are absent."),
        ],
    }


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------

DATASET_FILES = {
    "topology": "topology.json",
    "timetable": "timetable.json",
    "infrastructure": "infrastructure.json",
    "works": "works.json",
    "blocks": "blocks.json",
    "rules": "rules.json",
}


def dump_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def build_all() -> Dict[str, str]:
    """Return {relative path: file text} for every generated file."""

    from contracts import DEFAULT_HORIZON_START

    assert DEFAULT_HORIZON_START.startswith(HORIZON_START_DATE.isoformat()), DEFAULT_HORIZON_START

    outputs: Dict[str, str] = {
        DATASET_FILES["topology"]: dump_json(build_topology()),
        DATASET_FILES["timetable"]: dump_json(build_timetable()),
        DATASET_FILES["infrastructure"]: dump_json(build_infrastructure()),
        DATASET_FILES["works"]: dump_json(build_works()),
        DATASET_FILES["blocks"]: dump_json(build_blocks()),
        DATASET_FILES["rules"]: dump_json(build_rules()),
    }

    sources = []
    for s in SOURCES:
        entry = {k: v for k, v in s.items() if k != "file"}
        entry["retrieved_at"] = RETRIEVED
        entry["file"] = s["file"]
        path = SOURCES_DIR / s["file"]
        assert path.is_file(), f"missing source file {path}"
        entry["sha256"] = sha256_file(path)
        entry["bytes"] = path.stat().st_size
        sources.append(entry)

    datasets = {
        name: {"file": fname, "sha256": hashlib.sha256(outputs[fname].encode("utf-8")).hexdigest()}
        for name, fname in DATASET_FILES.items()
    }

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "corridor_id": CORRIDOR_ID,
        "title": "NDLS-AGC offline public-railway-data snapshot",
        "created": RETRIEVED,
        "retrieved_at": RETRIEVED,
        "offline": True,
        "data_classes": list(DATA_CLASSES),
        "statement": (
            "Published railway infrastructure and timetable data as an offline, dated reference layer. "
            "Possession windows are derived from the public timetable using explicit engineering rules. "
            "Maintenance observations are controlled demo inputs grounded in real railway work categories. "
            "No live or confidential Indian Railways feed is connected."
        ),
        "sources": sources,
        "datasets": datasets,
        "generated_by": "scripts/real_data/build_ndls_agc_snapshot.py",
    }
    outputs["manifest.json"] = dump_json(manifest)
    return outputs


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the checked-in snapshot differs from a rebuild")
    args = parser.parse_args(argv)

    outputs = build_all()

    if args.check:
        bad = []
        for rel, text in outputs.items():
            path = SNAPSHOT_DIR / rel
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                bad.append(rel)
        if bad:
            print("snapshot is out of date:", ", ".join(bad))
            return 1
        print("snapshot is up to date")
        return 0

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for rel, text in outputs.items():
        (SNAPSHOT_DIR / rel).write_text(text, encoding="utf-8", newline="\n")
        print("wrote", (SNAPSHOT_DIR / rel).relative_to(REPO_ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
