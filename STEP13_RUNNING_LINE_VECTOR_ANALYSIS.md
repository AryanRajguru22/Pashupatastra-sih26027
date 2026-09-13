# STEP 13 — RUNNING-LINE VECTOR ANALYSIS (EVIDENCE ARTIFACT)

**Status:** analysis only. No production data, code, topology, registry, solver, jobs,
timetable, provenance or frontend was touched. No commit. No push.

**Scope:** determine whether the *actual vector drawing geometry* of the NWR system maps
can establish BG single-line vs BG double-line, section by section, along the candidate
pilot corridor **JAIPUR (JP) → PHULERA (FL) → AJMER (AII)**.

---

## 1. EXECUTIVE VERDICT

**The vector evidence CANNOT close the running-line gap.**

**Running-line gate: RED.**

Every open-line section of JP–FL–AII is **UNKNOWN**. Not one section can be defensibly
classified as single-line or double-line from S1 or S2 vector geometry.

This is *not* a "we didn't look hard enough" result. Both map legends were decoded
successfully from PDF structure, to the level of exact path signatures. The blockers are
positive structural findings about the sources themselves:

| # | Blocking finding | Consequence |
|---|---|---|
| B1 | The BG-double signature established from the S1 legend (black, stroke-width 1.56, twin parallel spines) occurs **zero times** inside the corridor window. 52 of its 127 map-wide instances are *the legend box itself*. | S1 cannot classify the corridor. |
| B2 | S1's **ELECTRIFIED LINE** swatch is *itself* two parallel black lines (w=1.68, Δ=1.92) — confusably similar to **BG DOUBLE LINE** (w=1.56, Δ=2.28). | Even where twin black spines exist, double-track vs electrified-single-track is not safely separable. |
| B3 | S2 **does not cover the FL→AII running section at all**. Its station+chainage labels jump from `MADAR(MDJN) Km.288.37` / `AJMER(AII) Km.294.57` directly to `DAURAI(DOZ) Km.301.85` and onward south. No Phulera, Kishangarh, Naraina, Bhanwsa or Dantra label exists in S2. | The mandated S1/S2 cross-check has almost no overlap to perform. |
| B4 | Where S1 and S2 *do* overlap (Madar), they **disagree**: S1 prints `MADAR(MD)`, S2 prints `MADAR(MDJN)`. | Madar code conflict is now an evidenced inter-source contradiction, not a bookkeeping artifact. It must stay open. |
| B5 | **Zero dash patterns exist anywhere in S1** (all 89,378 stroked subpaths have `dash=None`). | Dash pattern cannot discriminate any line class in S1. |

Per the step's own evidence rules — *"absence of a second visible path is NOT by itself
sufficient if the map representation is ambiguous"* and *"if vector geometry cannot
distinguish … → mark UNKNOWN"* — the correct answer is UNKNOWN throughout.

**Jaipur–Ajmer must NOT advance to REAL_STATIC topology ingestion on this evidence.**

---

## 2. EXACT SOURCES EXAMINED

Both files were retrieved once, read-only, into a temp directory outside the repository
(`C:\Users\Aryan\AppData\Local\Temp\step13-sources`). Neither entered the repo.

### S1 — NWR HQ System Map (PRIMARY)

| Field | Value |
|---|---|
| File | `S1_NWR_HQ_SystemMap_20260401.pdf` |
| Origin | `nwr.indianrailways.gov.in/uploads/files/1777534163999-A-1 NWR System Map hq as on 01.04.2026.pdf` |
| Bytes | 2,367,024 |
| SHA-256 | `a721da1026e811fc8f8475211210a5c5f28903e47941a698994a99785fe0ac16` |
| PDF version | 1.7 |
| Pages | 1 (page 0 inspected) |
| MediaBox | `[0, 0, 2384, 1684]` (A1 landscape), `/Rotate 0` |
| Plan number in body text | `NWR HQ PLAN No. CE(P&D)/SM/01/1-F/HQ-26` |
| Attribution in body text | `CHIEF ENGINEER (P & D)`, `DY.CHIEF ENGINEER (PLG.)` |
| Vector objects inspected? | **Yes** — 8,369,315-byte content stream fully tokenised |
| Extracted | 89,378 stroked/filled subpaths, 2,100 text runs, 116 XObject placements |

### S2 — Ajmer Division System Map (CORROBORATING)

| Field | Value |
|---|---|
| File | `S2_NWR_AII_SystemMap.pdf` |
| Origin | `nwr.indianrailways.gov.in/uploads/files/1777534177357-SYSTEM MAP AII.pdf` |
| Bytes | 163,578 |
| SHA-256 | `379760af9329cd30670c24ebd062c53db02c7d5f2f2a623fddcdfe7b299cc6a9` |
| PDF version | 1.7 |
| Pages | 1 (page 0 inspected) |
| MediaBox | `[0, 0, 595.276, 841.89]` (A4), **`/Rotate 270`** |
| Title text in body | `NORTH WESTERN RAILWAY` / `SYSTEM MAP` / `AJMER DIVISION` |
| Vector objects inspected? | **Yes** — 827,254-byte content stream fully tokenised |
| Extracted | 7,298 stroked/filled subpaths, 4,759 text runs (4,316 decode non-empty) |

### S3 / S4 — Jaipur-division map sources

**NOT inspected. NOT used as evidence anywhere in this report.** No claim in this document
rests on them.

---

## 3. LEGEND EXTRACTION

### 3.1 S1 legend

The legend box occupies page-space `x 74–300, y 155–549`, headed `LEGEND` at
(158.6, 537.6). All 38 entries were decoded. Line-class entries:

| Legend text | Text coords (x, y) | Confidence |
|---|---|---|
| `BROAD GAUGE (SINGLE LINE)` | (83.5, 522.7) | HIGH |
| `BROAD GAUGE (DOUBLE LINE)` | (83.5, 512.6) | HIGH |
| `METRE GAUGE` | (83.5, 501.7) | HIGH |
| `FOREIGN RAILWAY` | (83.5, 492.2) | HIGH |
| `LINE UNDER CONSTRUCTION (NEW LINE)` | (83.5, 483.5) | HIGH |
| `LINE UNDER CONSTRUCTION DOUBLING` | (83.5, 471.6) | HIGH |
| `CONVERSION M.G. INTO B.G.` | (83.5, 461.3) | HIGH |
| `ELECTRIFIED LINE` | (83.5, 287.2) | HIGH |
| `ELECTRIFICATION UNDER PROGRESS` | (83.5, 276.4) | HIGH |
| `DFC ROUTE` | (83.5, 258.0) | HIGH |

Swatch geometry sits in the gutter `x ≈ 250–289`, i.e. to the **right** of the label text.

### 3.2 S2 legend

S2 carries a more explicit, more useful legend. Because the page is `/Rotate 270`, the
legend reads vertically in unrotated page space: labels share `y ≈ 809–812` and vary in `x`.

| Legend text | Text x | Note |
|---|---|---|
| `BG SINGLE LINE` | 474.1 | |
| `BG DOUBLE LINE` | 465.8 | |
| `MG LINES` | 456.8 | |
| `DFC LINE` | 447.6 | |
| `UNDER GC` | 438.4 | |
| `BG DOUBLING WIP` | 428.6 | doubling work-in-progress — *distinct from* double line |
| `PROP. NEW BG LINE` | 419.5 | proposed |
| `RE - ELECTRIFIED LINE` | 201.8 | |

S2 naming it **`BG DOUBLING WIP`** separately from **`BG DOUBLE LINE`** is itself
important: the source distinguishes *commissioned* double line from *doubling in
progress*. Any classification that conflated them would be wrong.

---

## 4. VECTOR-PATH SIGNATURES

### 4.1 Observed facts — S1 (page-space units)

| Class | Spines | Stroke width | Spine separation Δ | Colour | Extra |
|---|---:|---:|---:|---|---|
| BG SINGLE LINE | **1** (y=525.96) | 1.56 | — | `[0,0,0]` | periodic cross-ticks, w=1.56 |
| BG DOUBLE LINE | **2** (y=515.04, 512.76) | 1.56 | **2.28** | `[0,0,0]` | cross-ticks spanning both |
| METRE GAUGE | 1 (y=503.64) | **0.72** | — | `[0,0,0]` | ticks w=1.56 |
| FOREIGN RAILWAY | 1 (y=495.36) | 1.56 | — | `[0,0,0]` | **no ticks**, continuous |
| CONVERSION M.G.→B.G. | 1 (y=463.68) | 0.72 | — | `[0,0,0]` | MG-style spine |
| UNDER CONSTRUCTION (new) | dashes | 0.0 (filled) | — | `[0,0,0]` | `op=b` filled segments |
| UNDER CONSTRUCTION DOUBLING | dashes | 0.0 (filled) | — | `[0,0,0]` | *visually similar to above* |
| **ELECTRIFIED LINE** | **2** (y=290.64, 288.72) | **1.68** | **1.92** | `[0,0,0]` | + blue `[0,0,1]` mast glyph |
| ELECTRIFICATION UNDER PROGRESS | 2 (y=279.72, 277.80) | 1.68 | 1.92 | `[0,0,0]` | + cyan `[0,1,1]` marks |
| DFC ROUTE | 1 (y=260.64) | 1.56 | — | **`[0,0.749,1]`** | light blue |

Non-railway classes decoded for disambiguation: `CONSTITUENCY BOUNDARY` w=1.44
`[0.3608,0.3608,0.7216]`; `DISTRICT BOUNDARY` w=0.84 `[1,0.498,0.749]`; `MP (LOK SABHA)
CONSTITUENCY` w=0.48 `[0.3608,0.3608,0.7216]`; `ARMV` w=1.68 `[1,0,0]`; `ART` w=1.68
`[0,0,1]`; `HEAD-QUATERS OFFICES` `[0.7216,0.3608,0.7216]`; `ADEN OFFICE` `[0,0.749,1]`.

### 4.2 Observed facts — S2 (unrotated page-space units)

S2 draws line classes as **filled bars** (`op=f*`), not strokes. Width is bar geometry.

| Class | Distinct bars | Bar x-extent | Bar width | Gap |
|---|---:|---|---:|---:|
| BG SINGLE LINE | **1** | 476.64–478.80 | 2.16 | — |
| BG DOUBLE LINE | **2** | 464.64–466.20 and 467.76–469.44 | 1.56 each | **1.56** |
| MG LINES | 1 | 458.16–459.36 | 1.20 | — |
| DFC LINE | 1 | 450.24–451.56 | 1.32 | — |
| BG DOUBLING WIP | 1, **broken** | 429.60–431.52 | 1.92 | dashed in y |
| PROP. NEW BG LINE | 1, **broken** | 420.36–422.16 | 1.80 | dashed in y |

**Systematic duplication (critical):** every bar in S2 is emitted **twice** at identical or
near-identical coordinates. `BG SINGLE LINE` = one distinct bar drawn 2×; `BG DOUBLE LINE`
= two distinct bars each drawn 2×. Any bar-counting must de-duplicate first, or a single
line will read as double. This is an observed property of the file, and it is exactly the
kind of artifact that would silently corrupt a naive classification.

### 4.3 Interpretation (clearly separated from the facts above)

- The **form** that distinguishes BG single from BG double is *number of distinct parallel
  spines/bars*, in both sources. That much is solid.
- The **absolute separation** Δ is a legend-local quantity. Legend swatches in these
  drawings are drawn at their own scale; Δ=2.28 (S1) and gap=1.56 (S2) are **not**
  transferable to the map body without independent measurement in the body. No such body
  measurement was possible for this corridor (see §7).
- S1's electrified-line and BG-double-line signatures differ by only 0.12 in stroke width
  and 0.36 in separation. I do **not** treat that as a reliable discriminator.

---

## 5. COORDINATE-SPACE DESCRIPTION

### S1

- Page MediaBox `[0, 0, 2384, 1684]`, `/Rotate 0`.
- The content stream opens with a single global transform: `0.12 0 0 0.12 14 48 cm`,
  followed by clip `0 280 … 19629 12948`.
- Therefore the drawing's **internal pre-CTM coordinate space is ≈ 19,629 × 12,948**.
  This **independently corroborates** Step 12's recorded "approximately 18,000 × 12,000"
  coordinate space — arrived at here from the raw operators, without reference to that figure.
- All reported coordinates in this document are **page space** (post-CTM, PDF points).
- Measured extents: paths `x 54.4–2322.0, y 136.1–1573.4`; text `x 82.8–2281.9, y 159.8–1542.7`.
- **Paths and text share one coordinate space.** Station anchors and geometry are directly
  comparable with no further transformation. No rotation or scaling was applied by me.
- The parser reproduced **exactly 2,100 text runs**, matching Step 12's independently
  recorded count — a second corroboration that the extraction is faithful.

### S2

- Page MediaBox `[0, 0, 595.276, 841.89]`, **`/Rotate 270`**.
- **I worked in unrotated page space throughout and did not apply the rotation.** Every S2
  coordinate in this report is therefore unrotated-page-space. Consequence: what renders
  horizontally to a human reader runs along the `y` axis here, and the legend column varies
  in `x`. This is stated so no reader mistakes the axes.
- S2 labels are set **glyph-by-glyph along rotated baselines** (both x and y advance per
  glyph), so words required nearest-neighbour chaining to reassemble; 362 words recovered.
- Fonts are Identity-H CID. Decoding used the embedded `ToUnicode` CMaps (S1: `/F1`
  Arial-BoldMT 53 entries, `/F2` ArialMT 50, `/F3` DevLys-010 23; S2: `/F5` Arial,Bold 65
  entries, `/F8` Cambria,Bold 1). **443 of 4,759 S2 runs decode to empty** under those
  CMaps and were discarded rather than guessed.

---

## 6. STATION ANCHORS ACTUALLY USED

All from **S1** unless marked. Duplicate draws collapsed; draw-count retained as an
honesty signal (labels are stamped multiple times in the file).

| Station | Code as printed | Chainage as printed | x | y | Source | Draws | Confidence |
|---|---|---|---:|---:|---|---:|---|
| Jaipur | `JAIPUR(JP)` | — | 1140.80 | 810.70 | S1 | 14 | HIGH |
| Kanakpura | `KANAKPURA(KKU)` | 249.77 | 1128.10 | 801.80 | S1 | 1 | HIGH |
| Bindayaka (H) | `BINDAYAKA 'H' (BDYK)` | 254.70 | 1119.60 | 793.90 | S1 | 1 | HIGH |
| Asalpur Jobner | `ASALPUR JOBNER(JOB)` | 277.83 | 1078.80 | 793.20 | S1 | 1 | HIGH |
| Hirnoda | `HIRNODA(HDA)` | 287.25 | 1062.00 | 812.60 | S1 | 1 | HIGH |
| **Phulera** | `PHULERA(FL)` | **0.00** | 1048.00 | 805.30 | S1 | 6 | HIGH |
| Gudha | `GUDHA(GA)` | 15.39 | 1019.60 | 877.80 | S1 | 1 | MEDIUM |
| Bhanwsa | `BHANWSA (BNWS)` | 220.39 | 968.20 | 842.60 | S1 | 1 | HIGH (label only) |
| Naraina | `NARAINA (NRI)` | 225.28 | 965.10 | 833.80 | S1 | 1 | HIGH (label only) |
| Kishangarh | `KISHANGARH(KSG)` | 269.618 | 948.20 | 769.30 | S1 | 2 | HIGH |
| Dantra (H) | `DANTRA 'H'(DTRA)` | 229.76 | 946.80 | 821.60 | S1 | 1 | HIGH (label only) |
| Borawar | `BORAWAR(BOW)` | 70.97 | 941.60 | 821.00 | S1 | 1 | MEDIUM |
| **Madar** | **`MADAR(MD)`** | **288.37.0.00** | 929.00 | 741.60 | S1 | 5 | HIGH |
| Ajmer | `AJMER` (no code in S1) | — | 900.80 | 765.24 | S1 | 11 | HIGH |
| **Madar** | **`MADAR(MDJN)`** | `Km.288.37` | 431.6 | 106.7 | **S2** | — | HIGH |
| **Ajmer** | **`AJMER(AII)`** | `Km.294.57` | 425.4 | 121.1 | **S2** | — | HIGH |

**Two datum facts printed verbatim in the source, recorded and deliberately NOT reconciled:**

- `PHULERA(FL)0.00`
- `MADAR(MD)288.37.0.00`

Both show a chainage **restart to 0.00** at those stations. This corroborates the Phulera
datum discontinuity directly from the map text. It is recorded, not resolved.

**Note on the AII code:** S1 prints `AJMER` with **no station code anywhere**. The code
`AII` is evidenced **only by S2** (`AJMER(AII)Km.294.57`). Chainage 294.57 in S2 matches
Step 12's Chain-B endpoint exactly.

---

## 7. SECTION-BY-SECTION EVIDENCE TABLE

Corridor decomposed at the stations actually evidenced above.

| Section | Station A | Station B | Chainage | Vector evidence | Style | Running lines | Confidence | Verdict | Reason |
|---|---|---|---:|---|---|---:|---|---|---|
| JP–KKU | Jaipur | Kanakpura | 240.83→249.77 (A) | No BG-signature path in window | none matched | **UNKNOWN** | UNKNOWN | UNRESOLVED | B1, B2 |
| KKU–BDYK | Kanakpura | Bindayaka | 249.77→254.70 (A) | No BG-signature path in window | none matched | **UNKNOWN** | UNKNOWN | UNRESOLVED | B1, B2 |
| BDYK–JOB | Bindayaka | Asalpur Jobner | 254.70→277.83 (A) | No BG-signature path in window | none matched | **UNKNOWN** | UNKNOWN | UNRESOLVED | B1, B2 |
| JOB–HDA | Asalpur Jobner | Hirnoda | 277.83→287.25 (A) | No BG-signature path in window | none matched | **UNKNOWN** | UNKNOWN | UNRESOLVED | B1, B2 |
| HDA–FL | Hirnoda | Phulera | 287.25→295.58 (A) | No BG-signature path in window | none matched | **UNKNOWN** | UNKNOWN | UNRESOLVED | B1, B2, junction |
| **FL node** | — | — | datum break | `FL 0.00` printed | — | **N/A** | — | **EXCEPTION** | §8 |
| FL–(BNWS?) | Phulera | Bhanwsa | 215.12→220.39 (B) | Label only; no traceable path | none matched | **UNKNOWN** | UNKNOWN | UNRESOLVED | B1, membership open §10 |
| (BNWS?)–(NRI?) | Bhanwsa | Naraina | 220.39→225.28 (B) | Label only; no traceable path | none matched | **UNKNOWN** | UNKNOWN | UNRESOLVED | B1, membership open §10 |
| (NRI?)–(DTRA?) | Naraina | Dantra | 225.28→229.76 (B) | Label only; no traceable path | none matched | **UNKNOWN** | UNKNOWN | UNRESOLVED | B1, membership open §10 |
| (DTRA?)–KSG | Dantra | Kishangarh | 229.76→269.618 (B) | Label only; no traceable path | none matched | **UNKNOWN** | UNKNOWN | UNRESOLVED | B1, membership open §10 |
| KSG–MD/MDJN | Kishangarh | Madar | 269.618→288.37 (B) | S1 no signature; S2 no coverage | none matched | **UNKNOWN** | UNKNOWN | UNRESOLVED | B1, B3 |
| MD/MDJN–AII | Madar | Ajmer | 288.37→294.57 (B) | S1 no signature; S2 covers node but not as open line | none matched | **UNKNOWN** | UNKNOWN | UNRESOLVED | B1, B3, B4 |

**Running lines populated with a number: zero sections.** As required, no uncertainty was
converted into a count.

### Why "no BG-signature path in window" is a measured result, not a failure to look

Corridor window `x 880–1170, y 730–900` (covering AII→FL→JP with padding) contains
**6,657 paths**. Colour/width census of that window:

- Black paths with stroke width in {1.56, 1.68} — the S1 BG/electrified family — **0**.
- Map-wide, that family totals only **127** paths spanning `x 251–2265`, of which **52 are
  inside the legend box itself**.
- The 1.56-wide paths that *do* exist in the corridor are coloured `[0.7216,0.3608,0.3608]`
  (29) and `[0,1,1]` cyan (5). Cyan is the legend's **ELECTRIFICATION UNDER PROGRESS**
  colour. `[0.7216,0.3608,0.3608]` matches **no** decoded line-class legend entry.
- Within radius 14 of the `JAIPUR(JP)` anchor there is **no railway line-work at all** —
  only constituency hatching (w=0.48 `[0.3608,0.3608,0.7216]`, 200 paths), yellow fills and
  magenta HQ symbology.

So the corridor's line-work is rendered in a colour scheme that encodes **electrification
status and administrative overlays**, in which the legend's gauge/track-count signature does
not appear. Reporting SINGLE from that absence is precisely what the evidence rules forbid.

---

## 8. JUNCTION / BRANCH EXCEPTIONS

| Location | Issue | Status |
|---|---|---|
| **Phulera (FL)** | Chainage datum restarts (`FL 0.00` printed). Multiple routes converge (Ajmer west, Jaipur east, plus at least the Ringas/Sikar and Jodhpur directions implied by neighbouring labels `GUDHA(GA)15.39`, `BORAWAR(BOW)70.97` carrying unrelated low chainages). Station endpoints are **not** sufficient to classify the intervening open line. | **EXCEPTION** |
| **Madar (MD/MDJN)** | Prints **two** chainages, `288.37` and `0.00` — a second datum restart. Junction for the Pushkar branch (S2: `PUSHKAR(PUHT)Km.25.00`, `(BPKH)Km.17.40`). Code conflicts between sources. | **EXCEPTION** |
| **Ajmer (AII)** | S1 prints no station code. Division boundary vicinity (`AIIDIV.`, `RTMDIV.AIIDIV.` in S2). Multiple route continuations south (Beawar chain) and west. | **EXCEPTION** |
| **Jaipur (JP)** | No railway line-work resolvable within r=14 of the anchor; heavy administrative-overlay density. Major multi-direction junction. | **EXCEPTION** |
| **Kishangarh / "NEW KISHANGARH"** | S1 contains a **second, schematic** occurrence of corridor names in the DFC inset (§9). Naive proximity search would conflate inset geometry with the real corridor. | **EXCEPTION** |

No junction continuation was chosen on "visually closest" grounds anywhere in this report.

---

## 9. DFC SEPARATION ANALYSIS

- S1 legend: `DFC ROUTE` = single spine, w=1.56, colour **`[0, 0.749, 1]`** (light blue) —
  distinct from BG black. S2 legend: `DFC LINE` = single filled bar, 1.32 wide.
- **S1 contains a dedicated DFC schematic inset**, headed `SCHEMATIC JUNCTION ARRANGMENT
  FOR IR` at (1740.8, 592.3), with eight `DFC TRACK` labels at x 1834–1936, y 259–558, and
  `(DFC)` at (1786.5, 439.1).
- **The inset repeats corridor station names with different chainages**, e.g.
  `PHULERA(FL)0.00` at (1748.8, 637.7), `JAIPUR(JP) 131.27` at (1887.3, 640.2) —
  note **131.27, not 240.83** — plus `NEW KISHANGARH` (1863.2, 428.2), `HIRNODA`
  (1922.7, 459.1) and the route caption `KANAKPURA-PHULERA-MADAR` (1534.8, 234.5).
- **This is a schematic, not a geographic drawing.** Its coordinates bear no relation to the
  corridor's real position on the map. It lies at `x ≈ 1535–1995`, well outside the
  corridor window `x 880–1170`, and was excluded from all corridor measurements.
- **Honest statement of limits:** because no IR running-line signature could be identified in
  the corridor at all (B1), the question "is this parallel path IR or DFC?" never became
  answerable there. I did **not** inspect DFC geometry *on the corridor*; I established that
  the DFC material in S1 is confined to a schematic inset elsewhere on the sheet. No DFC
  infrastructure has been counted as an IR running line anywhere in this report.

---

## 10. BNWS / NRI / DTRA IMPLICATIONS

| Station | Evidence found | Result |
|---|---|---|
| **BNWS** | S1 label `BHANWSA (BNWS)220.39` at (968.2, 842.6). No S2 coverage. No traceable BG path to test connectivity. | **AMBIGUOUS — UNRESOLVED** |
| **NRI** | S1 label `NARAINA (NRI) 225.28` at (965.1, 833.8). No S2 coverage. No traceable BG path. | **AMBIGUOUS — UNRESOLVED** |
| **DTRA** | S1 label `DANTRA 'H'(DTRA)229.76` at (946.8, 821.6). No S2 coverage. No traceable BG path. | **AMBIGUOUS — UNRESOLVED** |

All three carry chainages (220.39 / 225.28 / 229.76) that fall inside the Chain-B range
(215.12–294.57), which is *consistent with* corridor membership but does **not** establish it.

**Why I did not decide this on coordinates.** The map is schematic, so `x` is not
proportional to chainage; straight-line distance from the FL–AII axis proves nothing, and
label offset is a typesetting artifact. The test that *could* settle membership is
topological — does a continuous BG running-line path run FL → BNWS → NRI → DTRA → KSG, or do
these labels attach to a different route leaving Phulera? That test requires an identifiable
BG path, which B1 denies. **None of the three has been forced into the corridor.**

**Madar code conflict — preserved, not chosen:**

| Source | Printed |
|---|---|
| S1 (NWR HQ) | `MADAR(MD)288.37.0.00` |
| S2 (Ajmer Division) | `MADAR(MDJN)Km.288.37` |

The two authoritative sources **disagree on the code** while **agreeing on chainage
288.37**. Per the rules this is left open. Neither `MD` nor `MDJN` is adopted.

---

## 11. S1 vs S2 CROSS-CHECK

| Location / section | S1 | S2 | Result |
|---|---|---|---|
| JP–FL (all sections) | Present as labels; no classifiable geometry | **No coverage** | **NOT COMPARABLE** |
| FL–KSG (incl. BNWS/NRI/DTRA) | Present as labels; no classifiable geometry | **No coverage** | **NOT COMPARABLE** |
| KSG–MD/MDJN | Label only | **No coverage** | **NOT COMPARABLE** |
| Madar node — chainage | `288.37` | `Km.288.37` | **CONSISTENT** |
| **Madar node — code** | **`MD`** | **`MDJN`** | **INCONSISTENT** |
| Ajmer node — code | *(none printed)* | `AII` | **PARTIALLY CONSISTENT** (S2 only) |
| Ajmer node — chainage | *(none printed)* | `Km.294.57` | **PARTIALLY CONSISTENT** (S2 only) |
| Running-line class, any corridor section | Not classifiable | No coverage | **NOT COMPARABLE** |

**Basis for "S2 has no coverage of FL→AII":** S2's 66 `Km.`-bearing station labels were
enumerated in full. They run `MADAR(MDJN) 288.37` → `AJMER(AII) 294.57` → `DAURAI 301.85` →
`SARADHNA 310.148` → `MAKRERA 314.88` → `MANGALIYAWAS 320.17` → `LAMANA 324.84` →
`KHARWA 331.122` → `PIPLAJ 337.22` → `BANGURGRAM 341.55` → `BEAWAR 346.75` → … → `KARJODA
646.251`, plus the Pushkar branch (`PUSHKAR 25.00`, `BPKH 17.40`). **No Phulera, Kishangarh,
Naraina, Bhanwsa or Dantra label exists in S2.** S2 covers Ajmer and southward, not the
Phulera–Ajmer running section.

Where the two sources touch, they disagree on the Madar code. Under the step's rule
("if they disagree, mark the affected result UNKNOWN"), the Madar-adjacent sections would be
UNKNOWN on that ground alone, independently of B1–B3.

---

## 12. EXPLICIT UNRESOLVED SECTIONS

Every section is unresolved. Precise reason per section:

1. **JP–KKU, KKU–BDYK, BDYK–JOB, JOB–HDA, HDA–FL** — the BG single/double signature
   established from the S1 legend occurs zero times in the corridor window; corridor
   line-work is in electrification/administrative colours that carry no gauge or
   track-count semantics. S2 provides no coverage. *(B1, B2, B3)*
2. **FL–BNWS, BNWS–NRI, NRI–DTRA, DTRA–KSG** — as above, **plus** corridor membership of
   BNWS/NRI/DTRA is itself unestablished (§10), so the section endpoints are not known to
   be correct. *(B1, B3, §10)*
3. **KSG–MD/MDJN** — as (1); S2 coverage begins only at the Madar node. *(B1, B3)*
4. **MD/MDJN–AII** — as (1), **plus** the two sources contradict each other on the Madar
   station code. *(B1, B3, B4)*
5. **Phulera and Madar as nodes** — chainage datum restarts printed in the source
   (`FL 0.00`; `MD 288.37.0.00`). Not reconciled, by instruction.
6. **Direction-specific track identity** — not established anywhere, and not attempted.

---

## 13. EVIDENCE STILL REQUIRED

To close the gap, authoritative railway records are needed. General internet knowledge,
OpenStreetMap, NTES and railway folk-knowledge are **not** substitutes and were not used.

| Gap | Evidence that would close it |
|---|---|
| Running lines per section (the blocking gap) | **Jaipur & Ajmer Division Working Time Table (current)** — the WTT section table states single/double line per block section directly. This is the single highest-value document. |
| Running lines, corroboration | **Station Working Rules (SWR)** for JP, KKU, BDYK, JOB, HDA, FL, KSG, MD/MDJN, AII — each SWR states the line configuration on both approaches. |
| Commissioned vs under-construction doubling | **CRS sanction / commissioning notification** for any doubling on JP–FL–AII. S2 shows `BG DOUBLING WIP` and `PROP. NEW BG LINE` as *distinct legend classes* — sanction alone must never be read as commissioned. |
| Direction-specific track identity (UP/DN) | WTT / SWR line designations. Must not be synthesised. |
| BNWS / NRI / DTRA corridor membership | WTT block-section list for the Phulera–Ajmer section, or the divisional **block section register**. |
| Madar code (MD vs MDJN) | Official **station code master** (IR station directory / RBS), which supersedes both map sheets. |
| Phulera & Madar datum discontinuity | Divisional **chainage/kilometrage diagram** or the WTT distance table showing the datum change explicitly. |
| IR vs DFC on the corridor | **DFCCIL commissioned-alignment record** plus the IR/DFC junction arrangement drawing referenced by S1's inset. |
| A legend→path mapping that actually applies | A **current system map whose body line-work uses the legend's own signature**, or the source vector/CAD (DGN/DWG) with named layers. The published PDF's corridor layer does not carry it. |

---

## 14. FINAL RUNNING-LINE GATE

# RED

**Explanation.** The gate is RED because the primary objective — establishing BG single vs
BG double per section from vector geometry — was tested properly and **failed on evidence**,
not for lack of analysis:

- Both legends *were* decoded to exact path signatures (§4). The method worked.
- The S1 legend's BG signature is **absent from the corridor**, and the corridor's actual
  line-work encodes electrification/administrative status instead (§7).
- S1's electrified-line swatch is itself twin parallel black lines, so twin spines would be
  **ambiguous even if found** (B2).
- **S2 does not cover the corridor's running section at all** (B3), so the mandated
  cross-check has no overlap to perform.
- Where the sources do overlap, they **contradict each other** (B4).

Under the step's own rules, ambiguity resolves to UNKNOWN, and UNKNOWN across every section
is a RED running-line gate.

### Interpretation rule, applied explicitly

Not only is direction-specific track identity unestablished — **even the single-vs-double
question is unresolved**. To be unambiguous:

> **No section of JAIPUR–PHULERA–AJMER has evidenced BG running-line count from S1 or S2
> vector geometry. Neither "one BG running-line path" nor "two BG running-line paths" is
> evidenced for any section. Direction-specific track identity is likewise not established.**

No `UP-1`, `DOWN-1`, track ID, direction assignment or canonical resource identifier has
been invented, and `running_lines_per_section` must **not** be populated from this report.

### Consequence for the pilot

**JAIPUR–AJMER cannot advance to REAL_STATIC topology ingestion.**
`pashupatastra_realistic_dataset.json` remains **SYNTHETIC**. No "real pilot" or
"live-data" claim is supported. The corridor remains a credible *candidate* — station
identity, codes and chainages are now better evidenced than before — but running-line
configuration, the blocking gap, is unchanged at RED.

---

## APPENDIX A — METHOD

Analysis used **pypdf only** (already present); no package was installed and no project
environment was modified. Content streams were decompressed with pypdf and tokenised by a
purpose-written PDF content-stream parser implementing: graphics-state stack (`q`/`Q`),
CTM composition (`cm`), stroke width (`w`), dash (`d`), stroke and fill colour
(`RG`/`G`/`SC`/`SCN`, `rg`/`g`/`sc`/`scn`), full path construction (`m`/`l`/`c`/`v`/`y`/
`re`/`h`), all paint operators (`S`/`s`/`f`/`F`/`f*`/`B`/`B*`/`b`/`b*`/`n`), XObject
placement (`Do`) with CTM, and the text machinery (`BT`/`ET`/`Tf`/`Tm`/`Td`/`TD`/`T*`/
`Tj`/`TJ`). Text was decoded through each font's embedded `ToUnicode` CMap — **no guessed
character offsets**. All geometry was transformed to page space before measurement.

**Self-checks passed:** the extracted S1 text-run count (2,100) matches Step 12's
independently recorded count exactly; the recovered internal coordinate space
(19,629 × 12,948) independently corroborates Step 12's ~18,000 × 12,000.

**Corrections made during analysis, recorded for transparency:**
1. Hex-string text (`<...>`) was initially passed through undecoded, yielding 0 decoded
   runs; fixed at the parser and re-run.
2. Fill colour was initially not tracked, so filled line-work was uncharacterised; added.
3. The first legend-swatch search looked to the *left* of the legend labels; the swatches
   are to the *right* (`x ≈ 250–289`). Corrected.
4. A signature test that relied on transferring the legend's separation Δ into the map body
   was rejected as unsound before use — legend swatches are drawn at their own scale.
5. S2 bar-pairs were nearly misread as double track before the systematic
   **double-draw** artifact was identified (§4.2).

## APPENDIX B — WHAT THIS REPORT DOES NOT CLAIM

- Does not claim S3/S4 were inspected. They were not.
- Does not claim DFC geometry on the corridor was inspected; only that S1's DFC material is
  confined to a schematic inset elsewhere on the sheet (§9).
- Does not claim any section is single-line or double-line.
- Does not claim BNWS/NRI/DTRA are in or out of the corridor.
- Does not choose between `MD` and `MDJN`.
- Does not reconcile the Phulera or Madar chainage datum restarts.
- Does not assert geographic accuracy for either map; both are treated as schematic.
