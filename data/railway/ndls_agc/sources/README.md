# Stored public sources

Every file here was downloaded once from a public Government of India (or, for coordinates,
OpenStreetMap) source on 2026-09-26. `../manifest.json` records, for each file, its URL,
publisher, data-as-of date, last-reviewed date, licence, coverage and SHA-256; the backend
refuses to start on the real dataset if any hash differs.

* `TAG-2026_T-2_*.pdf|txt` - Railway Board *Trains at a Glance 2026*, Table T-2, and its stored
  text extraction (used to re-verify every transcribed train offline).
* `NR_*` - Northern Railway notice and Annexure-A (Faridabad power and traffic block, 2026).
* `NCR_SIP_*.pdf` - North Central Railway Agra Division signal interlocking plans. Each keeps its
  own (older) date: the Raja Ki Mandi drawing is dated 2012.
* `CBS_2026-27_excerpts.json` - excerpts of the 2026-27 Consolidated Budget Statements (the full
  17 MB PDFs are not stored; their hashes are in the file).
* `PIB_*`, `CR_*`, `NCR_Agra_SIP_index_*` - excerpts/indexes of official web pages.
* `OSM_station_nodes_*.json` - OpenStreetMap station nodes, (c) OpenStreetMap contributors, ODbL.

To refresh: download the new documents here, update `scripts/real_data/build_ndls_agc_snapshot.py`
(`SOURCES` and the transcribed tables), and run it; then `build_frontend_snapshot.py`.
Nothing at run time reads the network.
