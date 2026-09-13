# STEP 14 — AUTHORITATIVE SOURCE GATE FOR JAIPUR–AJMER TOPOLOGY

**Status:** evidence/research gate only. No production data, code, topology, registry, solver,
jobs, timetable adapter, provenance, frontend, dataset or `jobs.db` was touched. No commit.
No push. The only repository artifact added is this file.

**Audit date (all retrievals):** 2026-09-13
**Scope:** JAIPUR (JP) → PHULERA (FL) → AJMER (AII), North Western Railway.
**Predecessors:** Step 12 (source & pilot audit) APPROVED; Step 13 (running-line vector
analysis) APPROVED as an analysis result, verdict RED.

---

## 1. EXECUTIVE VERDICT

**Final topology evidence gate: AMBER.**

A current, authoritative, operating-department source was found that Step 13 did not have:
the **North Western Railway Working Time Table (WTT 2025)**, published by NWR under RTI
s.4(1)(b) on the Jodhpur Division Operating Department page. It is a genuine WTT — section
tables with *System of Working*, station sequences, chainages, block-section names and route
classification — and it covers **Jaipur–Phulera in full** plus the **Ajmer–Madar** section.

What that source actually closes, and what it does not, is the whole of this report:

| Outcome | Detail |
|---|---|
| **Closed — running lines** | Exactly **two** open-line sections now have an explicit, current, authoritative running-line statement: **FL–HDA = DOUBLE LINE** and **AII–MDJN = DOUBLE LINE**. Both are printed verbatim in the WTT. Throughout this report **"Double Line" = 2 running lines on the open line, exclusive of station loops and yard lines.** |
| **Closed — station sequence & boundaries** | The **JP–FL** station sequence, block-section boundaries and inter-station distances are now fully and self-consistently established (arithmetic closes to the printed 54.75 km). Step 13's 5-station decomposition of JP–FL was **incomplete**: the WTT shows **10 stations / 9 block sections**, including four (DHND, BOBS, SHNX, DNK) that appear nowhere in Step 13. |
| **Closed — BNWS membership** | The WTT names **`FL-BNWS` / `BNWS-FL`** as a block section under the corridor heading **`JP-FL-MDJN`**. BNWS is on the corridor. |
| **NOT closed — running lines** | **8 of the 9 JP–FL block sections remain UNKNOWN** (the WTT gives their system of working as *Automatic Block Signalling*, which Railway Board IRSEM Chapter 20 explicitly provides for on **both** double **and single** line — see §4.4). The **entire FL–MDJN half remains UNKNOWN**: no WTT, SWR or line-capacity document covering it is publicly available. |
| **NOT closed — membership** | **NRI** and **DTRA** remain UNRESOLVED. **KSG** corridor membership is also unestablished by any source found. |
| **NOT closed — station code** | Madar **MD vs MDJN remains a CONFLICT** between authoritative sources, though the balance of evidence now favours MDJN (§8). |
| **NOT closed — datum** | No official source states the kilometrage datum or restart rule for Phulera or Madar. |

**On the WTT's currency — the best answer available.** The WTT's effective date is not printed
(§3, S4), so it cannot be *verified* as in force on 2026-09-13. But it can be **tested against
the newest source in the audit**: S1, the NWR HQ System Map corrected to **01-04-2026**. On
every corridor chainage the two share, they agree **exactly** — `JP 240.83`, `KKU 249.77`,
`BDYK 254.70`, `HDA 287.25` — and Step 13's chain-A endpoint for Phulera (`295.58`) equals the
WTT's FL chainage exactly. One value disagrees: `JOB` (S1 `277.83` vs WTT `277.61`), and the
WTT value is the one that closes the 54.75 km chain arithmetically. **A 2026 source therefore
contradicts the 2025 WTT on one chainage out of five and corroborates it on the rest.** That
is corroboration of continued validity, not proof of it, and it is not treated as proof
anywhere below.

**GREEN was not reachable and was not forced.** **RED is no longer correct** either: two sections
now carry defensible, current, authoritative running-line evidence, and the JP–FL topology
skeleton is established. Hence **AMBER**.

**Jaipur–Ajmer must still NOT advance to a full REAL_STATIC topology ingestion.** See §16 for
the one narrowly-scoped exception that *may* be considered in a later step.

`pashupatastra_realistic_dataset.json` remains SYNTHETIC. No live-data claim. No "real pilot"
claim. `running_lines_per_section` remains unpopulated.

---

## 2. SOURCES SEARCHED

All searching was read-only. No authentication, no credentials, no forms, no NTES, no
internal railway systems, no access-control bypass. Downloads went to a temporary directory
**outside the repository**:
`C:\Users\Aryan\AppData\Local\Temp\claude\D--Pashupatastra\<session>\scratchpad\step14`.
No downloaded PDF was added to Git.

### 2.1 Official sources probed

| Target | Path taken | Result |
|---|---|---|
| NWR portal, site-wide | `site:nwr.indianrailways.gov.in` searches for *working time table*, *station working rules*, *SWR*, *line capacity* | 1 WTT found (Jodhpur Div.); 0 SWRs |
| NWR → RTI s.4(1)(b) → **Jodhpur Division** → Operating | `view_section.jsp?...id=0,1,291,355,465` | **WTT 2025 found here** (link text "Working Time Table"); page *Last Reviewed 26-03-2025* |
| NWR → RTI s.4(1)(b) → **Ajmer Division** → Operating | `...id=0,1,291,353,452` | No WTT. **Line Capacity (July-2025)** and **Main System Map (01.04.2025)** found; page *Last Reviewed 12-08-2025* |
| NWR → RTI s.4(1)(b) → **Jaipur Division** → Operating | `...id=0,1,291,360,481` | **No WTT, no line-capacity document.** Only org chart, duty list, CUG numbers, "tech op 1.pdf" (goods sheds/sidings); *Last Reviewed 05-06-2025* |
| NWR → RTI s.4(1)(b) → **HQ** → Operating | `...id=0,1,304,375` | No WTT, no line capacity, no section list; *Last Reviewed 04-08-2026* |
| NWR → RTI s.4(1)(b) → **HQ** → Engineering | `...id=0,1,304,373` | **"Route KM and Track KM" (as on 01.04.2026)** found; *Last Reviewed 29-06-2026* |
| NWR → Jaipur Division landing page | `...id=0,1,291,360` and `...id=0,1,261` | **Jaipur Division System Map** (uploaded 2025-08-11) found; no operating documents |
| Commission of Railway Safety | `crs.gov.in` (via search) | Directory / jurisdiction pages only. **No Jaipur–Ajmer authorization or inspection report located.** |
| PIB (pib.gov.in) | targeted searches, direct HTML retrieval | 2 relevant releases found (WDFC Rewari–Madar) |
| DFCCIL (`dfccil.com`) | targeted search | Project briefs and press releases indexed; the PIB releases were used as the primary DFC evidence |
| Railway Board (`indianrailways.gov.in/railwayboard`) | targeted search | **IRSEM Chapter 20, *Automatic Block Signalling* (2023)** retrieved — decisive negative control, §4.4 |
| Parliament (`sansad.in`, `eparlib.sansad.in`) | targeted search | Only pre-2010 material on Phulera–Ajmer surfaced (1993-94 / 1995 / 2009) — **HISTORICAL**, not used |
| data.gov.in | considered | **Not used.** No dataset located whose publisher *and* validity date were clear enough to meet the currentness rule for a 2026 topology claim. |

### 2.2 Sources deliberately NOT used as evidence

Wikipedia, Google Maps, OpenStreetMap, RailYatri, Railways.app, Trainman, indiarailinfo,
makemytrip, erail, IRFCA/forums, blogs, YouTube, NTES mirrors, unofficial railway databases.

Several appeared in search results (indiarailinfo and makemytrip in particular, for
Phulera/Kishangarh/Madar train timings). **They were read only to the extent search engines
displayed them, and no claim, confidence level or cell in any matrix in this document rests
on them.** They are listed here as non-authoritative leads only:

| Non-authoritative lead | Why it is only a lead |
|---|---|
| indiarailinfo train timetables through FL / KSG / MDJN | Third-party aggregator; no stated provenance or validity date; states nothing about track count |
| makemytrip station page for "MADAR JN (MDJN)" | Commercial aggregator; cannot settle an official station code |
| Wikipedia "Jaipur–Ahmedabad line" / "Ahmedabad–Jaipur line" | Explicitly excluded by the source rules |

### 2.3 Search-result claim that was checked and rejected

A search-engine summary asserted that an NWR page referenced *"the Phulera-Madar section in
Jaipur Division with traffic blocks for provision of automatic block signalling"*. The page
it named (`...id=0,1,291,360,629`) was fetched directly and **contains no such text**. The
claim is therefore **discarded**; it does not appear anywhere in the matrices below. Recorded
here because a negative verification is itself an audit result.

---

## 3. SOURCES FOUND

All retrieved 2026-09-13. Hashes are of the bytes as downloaded.

### S4 — NWR WORKING TIME TABLE 2025 *(PRIMARY NEW SOURCE)*

| Field | Value |
|---|---|
| Local file | `WTT_candidate_9.pdf` |
| URL | `https://nwr.indianrailways.gov.in/uploads/files/1742970525725-9%20-%20Working%20Time%20Table.pdf` |
| Hosting page | NWR → Divisions → **Jodhpur** → *Departmentwise Information u/s 4(1)(b) of RTI Act* → **Operating**, item 9, link text **"Working Time Table"** |
| Hosting page last reviewed | **26-03-2025** |
| Upload token in filename | `1742970525725` → 2025-03-26 |
| Bytes | 4,172,911 |
| SHA-256 | `4cac6fa3f6bc34c7e6d91bfbb4710b513f55bf185b288a524051db8cce05950b` |
| Pages | 180 |
| PDF `/CreationDate` | `D:20241214151331+05'30'` (2024-12-14) |
| Producer / Creator | Adobe PDF Library 17.0 / Adobe InDesign 18.1 (Windows) |
| Internal running footers | **`WTT 2025`** (appendix pages), **`JU 2025`** (Jodhpur Division body pages) |
| Explicit "w.e.f." / effective date | **NOT PRINTED anywhere in the extractable text.** Searched exhaustively for `w.e.f`, `with effect`, `effective`, `valid from`, `01.10.`, `Time Table No`. Zero hits. **File observation:** the posted PDF opens directly on the General Manager's Hindi foreword — it carries **no cover page and no notice page**, which is where an effective date would normally sit. So the right characterisation is *"the posted extract omits the page that would carry the date"*, not *"the WTT has no date"*. |
| Text extraction | Full 180-page `pdftotext -layout` dump, 1,818,531 characters |

**Currentness assessment — stated honestly:** this is the **most recent Working Time Table
publicly posted by NWR that could be located**, it self-identifies as **WTT 2025**, and its
hosting page was last reviewed 2025-03-26. **It is not possible to verify from the public
document that it is the edition currently in force on 2026-09-13.** No WTT 2026 is posted on
any NWR page examined. Note the asymmetry: source **S1** (NWR HQ System Map) is *corrected up
to 01-04-2026* and is therefore **newer** than this WTT. Every WTT-derived claim below carries
this caveat.

### S5 — NWR Ajmer Division, Line Capacity (July-2025)

| Field | Value |
|---|---|
| Local file | `AII_LineCapacity_Jul2025.pdf` |
| URL | `.../uploads/files/1754980195423-Line%20Capacity(July-2025).pdf` |
| Hosting page | Ajmer Division → RTI s.4(1)(b) → Operating, link text **"Track Capacity of Ajmer Division"**, *Last Reviewed 12-08-2025* |
| Bytes / SHA-256 | 213,571 / `e5ecfedc1f6833ed36d65cc78fa1549693d37b8e8aeaa321a3aaa72c33dfd2ef` |
| Content | Section-wise charted capacity, Ajmer Division, BG + MG, for July 2025 |

### S6 — NWR HQ Engineering, "Route and Track KMs" (as on 01.04.2026)

| Field | Value |
|---|---|
| Local file | `NWR_RouteKM_TrackKM.pdf` |
| URL | `.../uploads/files/1777534439440-1.pdf` |
| Hosting page | NWR HQ → RTI s.4(1)(b) → Engineering, link text **"Route KM and Track KM"**, *Last Reviewed 29-06-2026* |
| Bytes / SHA-256 | 67,356 / `c2865fd987b3fc8520881959834730fd77ad52c0074aa19269b08ab9c5cb289f` |
| PDF metadata | Title `route track km.pdf`; Author *Mukesh Yadav*; Created **2026-04-30 12:43:15 +05:30**; Producer *Microsoft: Print To PDF* |
| Signature | Digitally signed **MUKESH KUMAR YADAV, AEN/PLG-II, 2026.04.30 12:40:14 +05:30** |
| Extraction note | **No text layer, no raster image.** All glyphs are drawn as filled Bézier outlines with an empty `/Resources` dictionary. `pdftotext` returns 1 character; `pypdf` reports 0 images. The three content streams were decompressed and **rasterised with a purpose-written path renderer**, then read visually. Method recorded so the result is reproducible and auditable. |

### S3 — NWR Jaipur Division System Map (uploaded 2025-08-11)

| Field | Value |
|---|---|
| Local file | `S3_SystemMap_Jaipur_Division.pdf` |
| URL | `.../uploads/files/1754915470632-SYSTEM%20MAP%20JAIPUR%20DIVSION%202.pdf` |
| Bytes / SHA-256 | 672,982 / `98f81ca383f0cb30b78392ad2784f251b4b374e3b5cbf60b144c14b4a8df0c9b` |
| Text layer | 6,572 characters. Legend and annotations decode; **station labels do not** (outlined/CID, same failure mode as Step 13) |

### S7 — NWR Ajmer Division Main System Map (as on 01.04.2025)

| Field | Value |
|---|---|
| Local file | `AII_MainSystemMap_20250401.pdf` |
| URL | `.../uploads/files/1754980182183-1.0%20Main%20System%20Map(01.04.2025).pdf` |
| Bytes / SHA-256 | 150,212 / `5684a4145cce51f0c07bc5cf6eaa2f220db085640d5e52aad2638505f769e03e` |
| Text layer | 64,309 characters, almost entirely CID-garbled. Recognisable: `PALI DISTT.`, `AJMER DISTT.`, `MERTA CITY`, `PUSHKAR`, `PUHT`, `MVJ`, `UDZ`, `AII`, `NDT`. **No Phulera, Kishangarh, Naraina, Bhanwsa or Dantra.** |

### S8 — Railway Board, IRSEM Chapter 20: *Automatic Block Signalling* (2023)

| Field | Value |
|---|---|
| Local file | `IRSEM_Ch20_ABS.pdf` |
| URL | `https://indianrailways.gov.in/railwayboard/uploads/directorate/signal/2023/20-Automatic%20Block%20Signalling.pdf` |
| SHA-256 | `4a05025725aaf008f5b643eb741061607ce3bdeb00d974a1f0169974493efef3` |
| Pages | 14 (document paginated "Page 421 of 535" — an extract of the Signal Engineering Manual) |
| Role | **Negative control.** Used *only* to test whether "Automatic Block Signalling" implies double line. It does not. |

### S9 / S10 — PIB releases on WDFC Rewari–Madar

| # | URL | Issuer | Date | Nature |
|---|---|---|---|---|
| S9 | `pib.gov.in/PressReleseDetailm.aspx?PRID=1597847` | Ministry of Railways | **27 DEC 2019** | Trial run — **NON-COMMISSIONING EVIDENCE** |
| S10 | `pib.gov.in/Pressreleaseshare.aspx?PRID=1686252` | PMO | **05 JAN 2021** (event 07-01-2021) | Dedication to the nation — **COMMISSIONING EVIDENCE, dated 2021** |

### Sources carried forward unchanged from Step 13

**S1** — NWR HQ System Map, plan `CE(P&D)/SM/01/1-F/HQ-26`, corrected up to **01-04-2026**,
SHA-256 `a721da10…0ac16`. **S2** — NWR Ajmer Division System Map, as on **01.04.2026**,
SHA-256 `379760af…cc6a9`. Neither was re-analysed; Step 13's findings on both stand.

---

## 4. WTT FINDINGS

This is the highest-priority section of the report.

### 4.1 Was a current WTT found?

**A WTT was found. Its currentness is PARTIAL, and it is divisionally incomplete for this corridor.**

- **Found:** NWR Working Time Table, self-identified **WTT 2025**, Jodhpur Division edition,
  180 pages, posted by NWR on an official RTI s.4(1)(b) page (§3, S4).
- **Not found, and stated clearly as required:** **the Jaipur Division WTT and the Ajmer
  Division WTT are NOT publicly accessible.** Both divisions' Operating-department RTI pages
  were fetched directly and neither posts a WTT. This matters because **Phulera–Madar is not
  covered by the Jodhpur WTT**.
- **No older timetable has been substituted for a current one.** WTT 2025 is presented as
  WTT 2025, with its effective date recorded as *not printed*.

### 4.2 What the WTT explicitly identifies — JP ↔ FL (Tables 10A / 11)

Header block from PDF pages 64–79 (printed page 41 onward, footer `JU 2025`); the DN and UP
tables are internally consistent and say the same thing in different word order.

> **Reproduction note.** The `pdftotext -layout` dump of these pages is roughly 200 columns
> wide and column-scrambled. The blocks below are reproduced with the original column
> positions **collapsed for legibility**: *the phrases are verbatim, the spatial arrangement
> is not.* A reviewer re-fetching the PDF will see the same words in a different layout.

**FL-JP (DN)** — printed page 41:
```
FL-JP (DN)      MAXIMUM PERMISSIBLE SPEED        SYSTEM OF WORKING            CLASSIFICATION
                     FL-JP 130 KMPH      Absolute Block System & Double line Lock & Block        OF
Electrified Section                                     between FL-HDA                          ROUTE
     Main Line    Permanent Speed Restriction:  Automatic Block Signalling bet HDA-JP            "B"
                        See App. IX-A           Panel Interlocking between BOBS-KKU
    54.75 Kms.                                       & EI at HDA, FL, JOB & JP
                                                       MACLS between FL-JP
```

**JP-FL (UP)**:
```
JP-FL (UP)      MAXIMUM PERMISSIBLE SPEED        SYSTEM OF WORKING            CLASSIFICATION
                     JP-FL 130 KMPH        Automatic Block Signalling bet JP-HDA                 OF
Electrified Section                         Absolute Block System & Double line                ROUTE
     Main Line    Permanent Speed Restriction:    Lock & Block between HDA-FL                    "B"
                        See App. IX-A       Panel Interlocking between KKU-BOBS
    54.75 Kms.                                    & EI at JP,JOB HDA & FL
                                                    MACLS between JP-FL
```

**Explicitly identified by this table:**

| Attribute | Stated? | Value |
|---|---|---|
| Single/double line | **Partially** | **"Double line"** stated for **FL–HDA only**. Nothing stated for HDA–JP. |
| Block sections | **Yes** | Derivable exactly from the station column (§4.3) |
| Section boundaries | **Yes** | FL and JP as termini; HDA as the internal system-of-working boundary |
| Line numbers / running-line identity | **No** | Only two *yard* line numbers appear, in the PSR appendix (§4.5) |
| Station codes | **Yes** | FL, HDA, DHND, JOB, BOBS, SHNX, DNK, BDYK, KKU, JP |
| Control / block arrangements | **Yes** | Absolute Block + Double Line Lock & Block (FL–HDA); Automatic Block Signalling (HDA–JP); Panel Interlocking BOBS–KKU; EI at HDA, FL, JOB, JP; MACLS FL–JP |
| Engineering restrictions relevant to configuration | **Yes** | 130 kmph section speed; PSRs via Appendix IX-A; Route classification "B"; electrified; "Main Line" |

### 4.3 Station sequence, chainage and block sections — JP ↔ FL

From the station column of Table 10A / 11, PDF page 65 (printed page 41):

| # | Station (as printed) | Code | Class as printed | Chainage | Distance to next |
|---:|---|---|---|---:|---:|
| 1 | PHULERA Jn. | `FL` | `[B] [II]` | 295.58 | 8.33 |
| 2 | Hirnoda | `HDA` | `[B] [III]` | 287.25 | 4.20 |
| 3 | Dhindha | `DHND` | `[D]` | 283.05 | 5.44 |
| 4 | Asalpur Jobner | `JOB` | `[B] [III]` | 277.61 | 7.47 |
| 5 | Bobas | `BOBS` | `[B] [III]` | 270.14 | 7.14 |
| 6 | Sheo Singhpura | `SHNX` | `[C]` | 263.00 | 3.48 |
| 7 | Dhanakya | `DNK` | `[B] [III]` | 259.52 | 4.82 |
| 8 | Bindayaka | `BDYK` | `[C]` | 254.70 | 4.93 |
| 9 | Kanakpura | `KKU` | `[B] [III]` | 249.77 | 8.94 |
| 10 | JAIPUR Jn. | `JP` | `[B] [II R]` | 240.83 | — |

**Internal consistency check (performed, not assumed):** the nine inter-station distances sum
to **54.75 km**, exactly the figure printed in the table header, and each equals the
difference of the adjacent chainages to the printed precision. The chain is fully closed.
This is a strong integrity signal for the extraction.

**Two consequences that must be carried forward:**

1. **Step 13's JP–FL decomposition was incomplete.** Step 13 derived JP–KKU–BDYK–JOB–HDA–FL
   (5 open-line sections) from S1 map labels. The WTT shows **9 block sections** and four
   additional stations — **DHND, BOBS, SHNX, DNK** — that appear nowhere in Step 13. Any
   future ingestion built on the Step 13 sequence would have been wrong.
2. **One chainage conflict.** S1 (map) prints `ASALPUR JOBNER(JOB) 277.83`; the WTT prints
   **277.61** and the WTT value is the one that makes the 54.75 km chain close arithmetically.
   Recorded as a **CONFLICT** (§13), WTT preferred, neither adopted into production.

The bracketed class markers (`[B] [II]`, `[D]`, `[C]`, `[B] [II R]`) are recorded **verbatim
and undecoded**. No meaning has been assigned to them and no inference about running lines,
loops or crossing capability has been drawn from them.

### 4.4 Why HDA–JP is **NOT** classified as double line — the decisive negative control

The obvious temptation is to read *"Automatic Block Signalling bet HDA-JP"*, note that the
adjoining FL–HDA is double line, and conclude HDA–JP is double too. **That inference was
tested against Railway Board doctrine and it fails.**

Source **S8**, Indian Railways Signal Engineering Manual, Chapter 20, states verbatim:

- **§20.1.1** — *"Automatic Block System on **Double Line/Single Line**: General"*
- **§20.1.3** — *"Special Requirements of Automatic Block System on **Single Line**. In case of
  single line, Line clear shall be obtained and Direction of Traffic shall be established as
  per GR 9.03"*, quoting **GR 9.03 "Essentials of the Automatic Block System on single line"**

These two clause numbers were read directly in the extract and are **by themselves fully
sufficient** for the negative control. The extract also carries the headings *"Establishing
Direction of Traffic for Automatic Stop Signals on Single Line"*, *"Track indicator for
Automatic Signalling on Single Line"* and the sentence *"An automatic signal on a double
line/single line shall require all tracks to be clear…"*; their clause numbers were **not**
read directly and are therefore **not cited**. The argument does not depend on them.

**Automatic Block Signalling is provided on both single and double line on Indian Railways.**
It therefore carries **no** track-count information. `HDA–JP` stays **UNKNOWN**.

The same discipline disposes of the other tempting inferences, each of which was considered
and rejected:

| Tempting inference | Why rejected |
|---|---|
| "MACLS between FL-JP" implies double line | MACLS is a signalling aspect scheme, not a track-count statement. Nothing in any source found ties it to track count. |
| The PSR appendix lists different kilometrages for UP and DN over the same block sections, so there must be two tracks | The `pdftotext -layout` reconstruction of that appendix is **demonstrably column-scrambled** (values do not align to their rows). Kilometrage-to-section attribution is not reliable. **Not used.** |
| Ajmer Division line capacity gives AII–MDJN a charted capacity of 81 — far the highest in the division — so it is double | Charted capacity is not a track-count statement. AII–MDJN is independently established as double by the WTT; this figure is *consistent with* that and is **not used as evidence for anything**. |
| FL–HDA is double and it is the same main line, so HDA–JP is double | This is plausibility, not evidence. Forbidden by the gate rules. |
| Route KM vs Track KM ratios (Jaipur Div. 1124.397 → 1856.707) imply doubling | Track KM includes loops, yard lines and sidings. The ratio cannot yield a running-line count for any section. **Not used.** |

### 4.5 What the WTT explicitly identifies — AII ↔ MDJN (and adjoining)

Header blocks from PDF pages 80–111 (Tables 11A/12/13/13A, Marwar–Ajmer–Palanpur group),
repeated identically across ~30 pages:

```
{AII-MDJN} Double Line                 {MDJN-AII} Double Line
{PNU-AII}  Double Line SGE Lock & Block (BPAC)    {AII-PNU} Double Line SGE Lock & Block
{DOZ-MDJN Bye Pass} Single Line        {MDJN-DOZ Bye Pass} Single Line
Automatic Block System / Absolute Block System
Electrified Section, Main Line, Classification of route "B"
PNU-MDJN / MDJN-PNU — 130 KMPH
```

Station entry, PDF page 110: **`MADAR Jn.` … `MDJN (B) (III R)`**; page 111 carries `294.57`
against Madar Jn./Ajmer in the MDJN-MJ table.

**Explicit, current, authoritative:**
- **`AII–MDJN` = DOUBLE LINE** (stated in both directions, on ~30 separate pages).
- **`DOZ–MDJN Bye Pass` = SINGLE LINE** — a *separate* bypass line, explicitly distinguished
  from the AII–MDJN double line. It is recorded here precisely so it is never conflated with
  the corridor running line.
- **`PNU–AII` = DOUBLE LINE** — outside the JP–FL–AII corridor, recorded only as corroboration
  that this WTT does state track counts plainly when it knows them.

### 4.6 What the WTT establishes about the FL → MDJN half

**The Jodhpur WTT does not contain section tables for Phulera–Madar.** Verified by exhaustive
code search across all 180 pages: `BNWS`, `NRI`, `DTRA`, `KSG` occur on **exactly one page**
between them (PDF page 148); no Phulera–Kishangarh–Madar station table exists. The only
"Kishangarh" in the document is **"Kishangarh Balawas"** in the ART-beat appendix — a
different station, explicitly not KSG.

Two things the WTT *does* give for that half:

**(a) The corridor is named, and BNWS is a block station on it.** Appendix — *Permanent Speed
Restriction* — PDF page 148 (printed page 145, footer `JU 2025`):

```
JP-FL-MDJN (UP-TRAINS)
1   JP Yard Line No.2
2   JP-KKU
3   BOBS-JOB
4   FL-BNWS

MDJN-FL -JP(DOWN-TRAINS)
1   BNWS-FL
2   JOB-BOBS
3   KKU-JP
4   JP Yard Line No.1 (DN Main)
```

The heading names the corridor **`JP-FL-MDJN`**. The UP list progresses away from Jaipur —
JP yard, JP-KKU, BOBS-JOB, then **FL-BNWS** — so `FL-BNWS` is a block section immediately
beyond Phulera in the Madar direction. The DN list mirrors it. **BNWS corridor membership is
established.** The block-section name `FL-BNWS` also implies **no intermediate block station
between FL and BNWS**.

*(The kilometrage/speed columns of this appendix are column-scrambled in extraction and are
not relied on. Only the block-section names and the corridor heading are used.)*

**(b) Sectional distances.** Appendix XI — *Sectional Distances*, printed page (xiv), footer
`WTT 2025`. The layout reconstruction is imperfect, so the reading is stated with its check:

| Reconstructed pair | Distance | Check |
|---|---:|---|
| Phulera → Jaipur | **54.75 km** | **Independently confirmed** — identical to the Table 10A/11 header and to the sum of the nine inter-station distances |
| Ajmer → Phulera | **79.87 km** | 79.87 + 54.75 = **134.62**, exactly the printed subtotal |
| Ajmer → Jaipur | **134.62 km** | printed subtotal |

Confidence **MEDIUM** (reconstruction from a scrambled layout, arithmetically self-checking).
The appendix prints its own caveat verbatim, which is reproduced here because it governs any
future use: *"These distances are based on RBS and on inputs received from Engineering Dept.
**Not to be used for Technical, Commercial or Financial purposes.**"*

**(c) Corroboration that FL–MDJN is one contiguous IR route.** ART/ARME beat table, PDF
page 173: *"Phulera-Madar-Daurai-Beawar (Excl) (128.41 km)"*, *"Phulera-Madar-Puskar
(98.25 km)"*, *"Phulera-Madar-Adarsh Nagar-Rupaheli (153.12 km)"*. This establishes that
Phulera and Madar are connected by an IR route. **It says nothing about track count or about
which intermediate stations lie on it.**

### 4.7 Exact page/table references for every WTT claim used

| Claim | Table | PDF page | Printed page / footer |
|---|---|---:|---|
| FL–HDA double line | 10A (FL-JP DN) / 11 (JP-FL UP) | 64–79 | p.41 onward, `JU 2025` |
| HDA–JP automatic block | 10A / 11 | 64–79 | p.41 onward, `JU 2025` |
| JP–FL station sequence + chainage | 10A | 65 | p.41, `JU 2025` |
| JP–FL total 54.75 km | 10A / 11 header | 64–79 | p.41 onward |
| AII–MDJN double line | 11A/12/13/13A headers | 80–111 | `JU 2025` |
| DOZ–MDJN bypass single line | same headers | 80–111 | `JU 2025` |
| MADAR Jn. = `MDJN` | 13 (MJ-MDJN DN) | 110 | `JU 2025` |
| `JP-FL-MDJN` corridor + `FL-BNWS` block section | App. IX-A (PSR) | 148 | p.145, `JU 2025` |
| Sectional distances FL/AII/JP | App. XI | 177 | p.(xiv), `WTT 2025` |
| Phulera–Madar ART beats | App. (ART/ARME) | 173 | `WTT 2025` |
| "Kishangarh Balawas" ≠ KSG | App. (ART/ARME) | 173 | `WTT 2025` |

---

## 5. SWR FINDINGS

**No Station Working Rules were found for any station on the corridor — Jaipur, Phulera,
Kishangarh, Madar or Ajmer — nor for any of HDA, DHND, JOB, BOBS, SHNX, DNK, BDYK, KKU.**

Searched: `site:nwr.indianrailways.gov.in` for *"Station Working Rules"* and *SWR*; the
Operating-department RTI s.4(1)(b) pages of NWR HQ and of Ajmer, Jaipur and Jodhpur Divisions;
the Jaipur and Ajmer Division landing pages. The only rule-book material surfaced was the
**NWR Accident Manual 2009** (historical, irrelevant to line configuration) and generic
commercial/parcel/reservation rules.

**Conclusion: SWRs for this corridor are not publicly accessible.** This is a documented
negative, not an incomplete search. Consequently:

- No source states **number of running lines** at any corridor station.
- No source states **line numbers**, **loop vs main-line distinction**, **station limits**, or
  **junction arrangements** at FL, MDJN or AII.
- The station-yard-line vs open-line-running-line distinction has therefore **not** been
  resolved anywhere in this audit, and nothing in this report conflates the two. The only
  yard lines encountered anywhere are `JP Yard Line No.1 (DN Main)` and `JP Yard Line No.2`
  in the WTT PSR appendix — **yard lines, explicitly recorded as such, not open running
  lines, and not counted as anything.**

---

## 6. COMMISSIONING / DOUBLING FINDINGS

**No commissioning or CRS document for any doubling on Jaipur–Phulera or Phulera–Ajmer was found.**

| Item | Source | Date | Classification |
|---|---|---|---|
| Commission of Railway Safety, Western Circle — authorization/inspection for Jaipur–Ajmer | searched `crs.gov.in` | — | **NOT FOUND.** Only directory, jurisdiction and function pages are published; no section authorization located. |
| "Phulera-Ajmer … taken up during 1993-94, targeted for completion by March 1995" | Parliament (`eparlib.sansad.in`), surfaced in search | 1995 | **HISTORICAL.** Relates to gauge conversion, not 2026 topology. **Not used.** |
| "Ajmer-Phulera" in Railway Budget material | `eparlib.sansad.in` | 2009 | **HISTORICAL.** **Not used.** |
| NWR "PROJECTS.pdf" | `.../uploads/files/1605691938718-PROJECTS.pdf` | uploaded 2020-11-18 | **Not current for 2026.** Not retrieved as evidence. |
| PIB "Railway Projects for Doubling and Tripling Of Railway Lines" | pib.gov.in | pre-2020 | **HISTORICAL / aggregate.** **Not used.** |

**No source was found that states an original sanction date, construction status, commissioning
date, or affected section for any doubling project on JP–FL–AII.** No source says "doubling
work in progress" for this corridor either — the absence runs in both directions.

The only track-count statements that survive are the WTT's **direct assertions of present
operating configuration** (`FL–HDA Double Line`, `AII–MDJN Double Line`). Those are statements
about how the line *is worked today*, not commissioning records — which is exactly the
evidence class a running-line count needs, so they are accepted for those two sections and
nothing is extrapolated from them.

**Explicitly preserved from Step 13:** S2's legend distinguishes **`BG DOUBLE LINE`** from
**`BG DOUBLING WIP`** and **`PROP. NEW BG LINE`** as three separate classes. Nothing in this
step conflates them. No sanction, tender, proposal or WIP item anywhere in this audit has been
converted into a running-line count.

---

## 7. DFCCIL / DFC FINDINGS

Official sources only (PIB = Government of India; NWR = zonal railway). DFCCIL's own site was
indexed but the PIB releases carry the same facts with clearer dating and were used as primary.

| Fact | Source | Date | Classification |
|---|---|---|---|
| WDFC **Rewari–Madar** section, ~306 km, built; ~79 km Haryana (Mahendragarh, Rewari), ~227 km Rajasthan (Jaipur, Ajmer, Sikar, Nagaur, Alwar) | S9 (PIB, Min. of Railways) | 27-12-2019 | trial run → **NON-COMMISSIONING EVIDENCE** for status; factual for alignment |
| **Nine newly built DFC stations**: crossing — New Dabla, New Bhagega, New Sri Madhopur, New Pachar Malikpur, New Sakun, **New Kishangarh**; junction — New Rewari, New Ateli, **New Phulera** | S9, S10 | 2019 / 2021 | factual, and the single most important DFC finding here |
| *"Double line electric (2 X 25 KV) track"*, *"Exclusive operation for freight trains"*, high-rise OHE 7.4 m vs IR 5.5 m, TPWS, 32.5 t axle load | S9 | 27-12-2019 | factual |
| PM **dedicated the 306 km Rewari–Madar section of WDFC to the nation on 07-01-2021** | S10 (PIB, PMO) | 05-01-2021 | **COMMISSIONING EVIDENCE (2021)** |
| **"DFCCIL CONNECTION WITH IR"** annotated on the current Jaipur Division system map, with locations including `Km26.40(RE-BKI)`, `Km34.10(SWM-JP)` and `2 NOS.(UP&DN) Km185.2(JP-RGS)` | S3 | uploaded 2025-08-11 | current, factual — establishes that discrete IR↔DFC interface points exist and are enumerated |

### 7.1 DFC separation result

**The DFC is a separate railway and is NOT an IR running line. This is now positively
evidenced, not merely assumed.**

The evidence is the **naming**: DFCCIL's stations on this very stretch are **`New Phulera`**,
**`New Kishangarh`**, `New Ateli`, `New Sakun` — distinct station identities from IR's
`PHULERA(FL)` and `KISHANGARH(KSG)`, built as part of a physically separate, exclusively
freight, differently electrified (2×25 kV, 7.4 m OHE) alignment operated by a different
corporate entity. Combined with S3's explicit enumeration of discrete **"DFCCIL CONNECTION
WITH IR"** points, the DFC is established as **parallel/separate with enumerated interfaces**,
not as an additional IR running line.

This also independently vindicates Step 13 §9: S1's DFC material sits in a **schematic inset**
that repeats corridor station names with *different* chainages (`JAIPUR(JP) 131.27` in the
inset vs `240.83` on the map body). Any proximity-based merge of that inset into the corridor
would have injected DFC geometry into IR topology.

**No DFC infrastructure has been counted as an IR running line anywhere in this report.**

### 7.2 Honest limits on the DFC result

- The commissioning evidence is dated **2021**. No 2026 document confirming continued
  operational status was retrieved. There is likewise no evidence of decommissioning, and S3
  (2025) still annotates live IR↔DFC connections — but the 2021 date is recorded as the
  evidence date, not represented as a 2026 statement.
- **No source states the DFC↔IR interface geometry at Phulera, Kishangarh or Madar
  specifically.** S3's interface list names `RE-BKI`, `SWM-JP` and `JP-RGS` locations. Tokens
  resembling `KTWS`, `FL` and `HDA` appear nearby in the extracted layout, **but column
  association in that dump is not reliable and they are NOT treated as evidence.**
- Whether the DFC runs parallel to, crosses, or detours around the IR line between Phulera and
  Kishangarh is **NOT established** by any source found.

---

## 8. STATION-CODE FINDINGS

### 8.1 Madar — MD vs MDJN

| Source | Authority | Date | Prints |
|---|---|---|---|
| **S4 — NWR WTT 2025** | NWR Operating (divisional operating document) | WTT 2025 | **`MDJN`** — station entry `MADAR Jn. / MDJN (B) (III R)`, and section names `AII-MDJN`, `MDJN-AII`, `MDJN-PNU`, `PNU-MDJN`, `DOZ-MDJN`, `MDJN-DOZ`, `MDJN-MJ`, `MJ-MDJN` |
| **S5 — Ajmer Div. Line Capacity** | NWR Ajmer Div. Operating | July 2025 | **`MDJN`** — rows `DOZ-MDJN`, `AII-MDJN`, `MDJN-AHO`, `MDJN-PHUT` |
| **S2 — Ajmer Div. System Map** | NWR Ajmer Division | as on 01.04.2026 | **`MDJN`** — `MADAR(MDJN)Km.288.37` |
| **S1 — NWR HQ System Map** | NWR HQ, CE(P&D) | corrected to 01-04-2026 | **`MD`** — `MADAR(MD)288.37.0.00` |
| **S3 — Jaipur Div. System Map** | NWR Jaipur Division | uploaded 2025-08-11 | **`MD`** — route classification list `1. RE-BKI-JP-MD` |

**Result: CONFLICT — documented, weighted, NOT resolved, NOT adopted.**

Three authoritative sources print `MDJN`, two print `MD`. The weighting is not merely 3-vs-2:
the `MDJN` sources include **the operating documents** (WTT and line capacity), where a station
code is an operational identifier used to name block sections; the `MD` sources are **both
engineering/planning maps**, where `MD` also appears in an abbreviated route-name string
(`RE-BKI-JP-MD`) that may be a route shorthand rather than a station-code field.

**Balance of evidence favours `MDJN`.** Per the fail-closed rule this is recorded as a
**CONFLICT with a preferred value**, not as a resolution. **Neither code is adopted.** No
authoritative current *master station-code list* was located; no data.gov.in dataset met the
currentness/publisher test.

### 8.2 Ajmer — AII

S1 prints `AJMER` with **no code**. `AII` is evidenced by **S2** (`AJMER(AII)Km.294.57`), and
now independently by **S4** (section names `AII-MDJN`, `MDJN-AII`, `PNU-AII`, `AII-PNU`,
`DOZ-AII`) and **S5** (rows `DOZ-AII`, `AII-MDJN`, `AII-AHO`). **`AII` is corroborated across
three independent authoritative sources.** No conflict.

### 8.3 New codes introduced by the WTT

`HDA` (Hirnoda), `DHND` (Dhindha), `JOB` (Asalpur Jobner), `BOBS` (Bobas), `SHNX` (Sheo
Singhpura), `DNK` (Dhanakya), `BDYK` (Bindayaka), `KKU` (Kanakpura), `FL` (Phulera Jn.),
`JP` (Jaipur Jn.). Four of these — **DHND, BOBS, SHNX, DNK** — were entirely absent from
Step 13.

---

## 9. BNWS / NRI / DTRA FINDINGS

| Station | Step 13 result | Step 14 evidence | Step 14 result |
|---|---|---|---|
| **BNWS** (Bhanwsa) | AMBIGUOUS — UNRESOLVED | **S4, PDF p.148**: block sections **`FL-BNWS`** (UP) and **`BNWS-FL`** (DN) listed under the corridor heading **`JP-FL-MDJN`** | **RESOLVED — ON CORRIDOR.** Confidence **HIGH**. Also establishes: no intermediate block station between FL and BNWS. |
| **NRI** (Naraina) | AMBIGUOUS — UNRESOLVED | **None.** Zero occurrences in S4 (all 180 pages), S5, S7. Not decodable from S3. No SWR, no Jaipur/Ajmer WTT. | **UNRESOLVED.** |
| **DTRA** (Dantra 'H') | AMBIGUOUS — UNRESOLVED | **None.** Zero occurrences in S4, S5, S7. | **UNRESOLVED.** |
| **KSG** (Kishangarh) | assumed on corridor (S1 label) | **None on the IR corridor.** The only "Kishangarh" in S4 is **"Kishangarh Balawas"**, a different station in the ART-beat appendix. `New Kishangarh` in S9/S10 is a **DFC** station, not IR. | **UNRESOLVED** for corridor membership — and flagged, because Step 13 treated KSG as a corridor station on map-label evidence alone. |

**Nothing was resolved on geometric proximity, schematic map position or chainage similarity.**
Step 13's chainage-consistency observation for BNWS/NRI/DTRA (220.39 / 225.28 / 229.76 inside
the Chain-B range) is *still* not treated as membership evidence. BNWS was resolved solely
because the WTT **names a block section that has BNWS as an endpoint, under the corridor
heading** — a topological statement, which is the test Step 13 identified as the correct one.

---

## 10. CHAINAGE / DATUM FINDINGS

### 10.1 What is now established

| Chain | Anchors | Source |
|---|---|---|
| JP–FL chain | JP 240.83 … FL **295.58**, increasing JP→FL, with all nine intermediate chainages and a closed 54.75 km arithmetic check | **S4** (current, authoritative) |
| MDJN/AII chain | MDJN **288.37**, AII **294.57** | **S2** (Step 13), MDJN corroborated by **S4** |
| FL↔AII distance | **79.87 km** (reconstructed, arithmetically checked) | **S4** App. XI, MEDIUM confidence |

### 10.2 Derived observation — clearly labelled as interpretation, not evidence

FL is at 295.58 on its chain and MDJN is at 288.37 on its chain, a difference of 7.21 km,
while the two stations are roughly 73.7 km apart (79.87 − 6.20). **The two values therefore
cannot lie on one monotone kilometrage chain.** This *corroborates* the existence of at least
two distinct kilometrage datums across the corridor.

**This is an inference from measurements, not a statement any source makes.** It is recorded
as interpretation and is **not** entered in the claim matrix as support for any datum claim.

### 10.3 What remains UNRESOLVED

- **No official engineering or WTT source found states the chainage datum, its origin, the
  restart rule, or section-specific kilometering for Phulera or Madar.**
- Step 13's verbatim source readings stand and are **not reconciled**: S1 prints
  `PHULERA(FL)0.00` and `MADAR(MD)288.37.0.00` — restarts to 0.00 at both.
- The WTT's own chainage for FL (295.58) **differs from** S1's printed `FL 0.00`. This is not
  a contradiction — they are evidently different chains — but **no source states the rule that
  relates them**, so nothing is merged.
- **The datums are NOT merged, and the corridor is NOT treated as one continuous chain**,
  notwithstanding that it is operationally continuous.
- **One chainage conflict at JOB:** S1 `277.83` vs S4 `277.61`. WTT preferred on arithmetic
  grounds. Not adopted.

---

## 11. SOURCE-QUALITY MATRIX

| Source | Authority | Date | Currentness | Exact evidence | Covers | Strength | Can close gap? |
|---|---|---|---|---|---|---|---|
| **S4** NWR Working Time Table 2025 | NWR Operating (Jodhpur Div. edn.), RTI s.4(1)(b) | WTT 2025; PDF created 2024-12-14; posted 2025-03-26 | **CURRENT-ish.** Latest WTT publicly posted. **No effective date printed** — the posted extract omits the cover/notice page; cannot be *verified* as in force on 2026-09-13. Older than S1, but **corroborated by it**: S1 (corrected to 01-04-2026) agrees exactly on `JP 240.83`, `KKU 249.77`, `BDYK 254.70`, `HDA 287.25` and on Phulera 295.58, and differs on one value only (`JOB` 277.83 vs 277.61). | Tables 10A/11 pp.64–79; Tables 11A–13A pp.80–111; App. IX-A p.148; App. XI p.177 | JP↔FL (full); AII↔MDJN; corridor naming `JP-FL-MDJN`; `FL-BNWS` | **VERY HIGH** | **PARTIAL** — closes FL–HDA and AII–MDJN running lines, JP–FL sequence/boundaries/distances, BNWS membership. Does **not** cover FL→MDJN, and gives no track count for HDA–JP. |
| **S8** Railway Board IRSEM Ch.20 (2023) | Railway Board, Signal Directorate | 2023 | CURRENT doctrine | §20.1.1, §20.1.3 (GR 9.03), §20.1.4, §20.1.5(g), §20.2 | Definition of Automatic Block System | **VERY HIGH** | **NO** (by design) — it is a **negative control**. It *prevents* an unsound closure of HDA–JP. |
| **S5** Ajmer Div. Line Capacity, July-2025 | NWR Ajmer Div. Operating, RTI s.4(1)(b) | July 2025; posted 2025-08-12 | **CURRENT** | Section list: PNU-KRJD … DOZ-MDJN, AII-MDJN, MDJN-AHO, MDJN-PHUT | Ajmer Division sections | **HIGH** (for divisional scope) / **NOT USABLE** (for track count) | **NO** for running lines. **PARTIAL** for scope: absence of any `FL-` row indicates FL–MDJN is **not** an Ajmer Division section. |
| **S6** NWR HQ "Route and Track KMs" | NWR HQ Engineering, AEN/PLG-II, digitally signed | **as on 01.04.2026**, signed 2026-04-30 | **MOST CURRENT source in this audit** | Division-level BG/MG Route KM and Track KM table | Whole zone, **division granularity only** | **HIGH** authority, **LOW** relevance | **NO** — wrong granularity. Track KM includes loops/sidings; no section-level or running-line inference is possible. |
| **S10** PIB/PMO, Rewari–Madar WDFC dedication | Government of India, PMO | 05-01-2021 (event 07-01-2021) | **2021** — commissioning event, not a 2026 status statement | Full release text | WDFC Rewari–Madar | **HIGH** | **PARTIAL** — supports DFC separation; **does not** bear on any IR running-line count. |
| **S9** PIB/Min. Railways, Rewari–Madar trial run | Government of India | 27-12-2019 | **2019 — HISTORICAL / NON-COMMISSIONING** | DFC station names; "double line electric"; "exclusive freight" | WDFC Rewari–Madar | **MEDIUM** | **PARTIAL** — DFC station naming only. Must not be read as commissioning. |
| **S3** NWR Jaipur Div. System Map | NWR Jaipur Division | uploaded 2025-08-11 | CURRENT-ish | Legend; "DFCCIL CONNECTION WITH IR"; ADEN list incl. FL; route class `RE-BKI-JP-MD` | Jaipur Division | **MEDIUM** | **NO** — station labels not decodable; Step 13's B1/B2 map-classification blockers apply. |
| **S7** NWR Ajmer Div. Main System Map | NWR Ajmer Division | as on 01.04.2025 | CURRENT-ish | Text layer CID-garbled | Ajmer Division | **LOW** | **NO** — and it contains **no** Phulera/Kishangarh/Madar labels, corroborating S5 on divisional scope. |
| **S1** NWR HQ System Map *(Step 13)* | NWR HQ, CE(P&D)/SM/01/1-F/HQ-26 | corrected to **01-04-2026** | **CURRENT** | Step 13 §4, §6 | Whole zone | **HIGH** authority, **NOT USABLE** for track count (Step 13 B1/B2/B5) | **NO** |
| **S2** NWR Ajmer Div. System Map *(Step 13)* | NWR Ajmer Division | as on **01.04.2026** | **CURRENT** | Step 13 §3.2, §11 | Ajmer & south | **HIGH** authority, **NOT USABLE** for corridor | **NO** — no FL→AII coverage (Step 13 B3) |
| Commission of Railway Safety | Min. of Civil Aviation | — | — | **Nothing located** for this corridor | — | **N/A** | **NO — source not found** |
| Jaipur Division WTT | NWR Jaipur Div. | — | — | **Not publicly posted** | would cover FL→MDJN | **N/A** | **NO — source not available** |
| Ajmer Division WTT | NWR Ajmer Div. | — | — | **Not publicly posted** | would cover MDJN area | **N/A** | **NO — source not available** |
| SWRs (JP, FL, KSG, MDJN, AII, +8) | NWR | — | — | **Not publicly posted** | station running lines | **N/A** | **NO — source not available** |
| Parliament Q&A on Phulera–Ajmer | Ministry of Railways via Parliament | 1995, 2009 | **HISTORICAL** | — | gauge conversion era | **NOT USABLE** | **NO** |
| data.gov.in station datasets | — | — | publisher/validity not clear | — | — | **NOT USABLE** | **NO** |

---

## 12. CLAIM-LEVEL EVIDENCE MATRIX

| # | Claim | Evidence source | Exact page/section | Current? | Supported? | Confidence |
|---:|---|---|---|---|---|---|
| 1 | **JP–FL running-line count** (as a single corridor half) | S4 | Tables 10A/11, PDF pp.64–79 | Yes (WTT 2025 caveat) | **PARTIAL** — `FL–HDA` = **2 (Double Line)**, explicit. `HDA–JP` (8 block sections) = **UNKNOWN** | FL–HDA **HIGH**; HDA–JP **NONE** |
| 1a | `FL–HDA` = double line | S4 | *"Absolute Block System & Double line Lock & Block between FL-HDA"*, Tables 10A/11 header, PDF pp.64–79 | Yes | **YES** | **HIGH** |
| 1b | `HDA–DHND`, `DHND–JOB`, `JOB–BOBS`, `BOBS–SHNX`, `SHNX–DNK`, `DNK–BDYK`, `BDYK–KKU`, `KKU–JP` | S4 header says only *"Automatic Block Signalling bet HDA-JP"*; S8 §20.1.1/§20.1.3 shows ABS runs on single **and** double line | S4 pp.64–79; S8 §20.1.1, §20.1.3 | Yes | **NO — UNKNOWN** | **NONE** |
| 2 | **FL–AII running-line count** | — | — | — | **PARTIAL** — `AII–MDJN` = **2 (Double Line)**. `FL→MDJN` = **UNKNOWN** | see 2a/2b |
| 2a | `AII–MDJN` = double line | S4 | *"{AII-MDJN} Double Line"* / *"{MDJN-AII} Double Line"*, PDF pp.80–111 (~30 pages) | Yes | **YES** | **HIGH** |
| 2b | `FL–BNWS`, `BNWS–…–KSG–MDJN` | **No source** — Jodhpur WTT has no FL–MDJN tables; Jaipur/Ajmer WTTs not public; no SWR | S4 code census across 180 pp.; §5 | — | **NO — UNKNOWN** | **NONE** |
| 3 | **Each intermediate section's running-line count** | — | — | — | **NO** except 1a and 2a | **NONE** |
| 4 | **BNWS corridor membership** | S4 | App. IX-A, PDF p.148 — `JP-FL-MDJN (UP-TRAINS)` → `FL-BNWS`; `MDJN-FL -JP(DOWN-TRAINS)` → `BNWS-FL` | Yes | **YES** | **HIGH** |
| 5 | **NRI corridor membership** | none | — | — | **NO — UNKNOWN** | **NONE** |
| 6 | **DTRA corridor membership** | none | — | — | **NO — UNKNOWN** | **NONE** |
| 6a | **KSG corridor membership** *(added — Step 13 had assumed it)* | none (only "Kishangarh Balawas", S4 p.173, a different station; `New Kishangarh` is DFC) | — | — | **NO — UNKNOWN** | **NONE** |
| 7 | **Madar station code** | S4 (`MDJN`, incl. p.110 `MADAR Jn. / MDJN (B) (III R)`); S5 (`MDJN`); S2 (`MDJN`) **vs** S1 (`MD`); S3 (`MD`) | §8.1 | Yes on both sides | **CONFLICT** — `MDJN` preferred on source-type weighting; **neither adopted** | **MEDIUM** for MDJN preference; **CONFLICT** as the recorded state |
| 7a | **Ajmer station code = `AII`** | S4, S5, S2 | §8.2 | Yes | **YES** | **HIGH** |
| 8 | **Phulera datum** | S4 gives FL = 295.58 on the JP chain; S1 prints `FL 0.00` | S4 p.65; Step 13 §6 | Yes | **NO — no source states the datum rule or restart** | **NONE** |
| 9 | **Madar datum** | S4/S2 give MDJN = 288.37; S1 prints `288.37.0.00` | S4 pp.110–111; Step 13 §6 | Yes | **NO — no source states the datum rule or restart** | **NONE** |
| 10 | **DFC relationship** | S9, S10, S3 | §7 | S10 = 2021; S3 = 2025 | **YES for separation** — DFC has its own stations (`New Phulera`, `New Kishangarh`), own alignment, exclusive freight, 2×25 kV/7.4 m OHE, discrete enumerated IR interfaces. **NO** for corridor-local interface geometry. | Separation **HIGH**; local geometry **NONE** |
| 11 | **Commissioned status of any doubling on JP–FL–AII** | **No source found** | §6 | — | **NO** | **NONE** |
| 11a | *WDFC Rewari–Madar commissioned* (DFC, not IR) | S10 | PIB PRID 1686252, 05-01-2021 | **2021** | **YES (2021)** — dedicated to the nation 07-01-2021 | **HIGH**, dated 2021 |
| 12 | **Current section boundaries** | S4 | Table 10A p.65 (JP–FL, 10 stations / 9 block sections); App. IX-A p.148 (`FL-BNWS`) | Yes | **PARTIAL** — **YES** for JP–FL (complete, arithmetically closed) and for the FL–BNWS boundary; **NO** for BNWS→MDJN | JP–FL **HIGH**; FL–BNWS **HIGH**; BNWS→MDJN **NONE** |
| 12a | **JP–FL inter-station distances / total 54.75 km** | S4 | Table 10A p.65 + header; App. XI p.177 | Yes | **YES** — nine distances sum exactly to the printed 54.75 km | **HIGH** |
| 12b | **FL–AII = 79.87 km; JP–AII = 134.62 km** | S4 App. XI | p.(xiv) | Yes | **YES**, with the source's own caveat *"Not to be used for Technical, Commercial or Financial purposes"* | **MEDIUM** (layout reconstruction, arithmetically self-checking) |
| 12c | **FL–MDJN is not an Ajmer Division section** | S5 (no `FL-` row in the full section list); S7 (no Phulera label) | §3, §4.6 | Yes (Jul 2025 / Apr 2025) | **YES** (scope finding) | **MEDIUM** |
| 13 | **Direction-specific track identity (UP-1 / DN-1 etc.)** | — | — | — | **NO — not established, not attempted, not invented** | **NONE** |

---

## 13. REMAINING UNKNOWN / CONFLICT ITEMS

### 13.1 UNKNOWN — running-line count (the blocking gap)

| # | Section | Status | Reason |
|---:|---|---|---|
| 1 | `HDA–DHND` | **UNKNOWN** | WTT states only Automatic Block Signalling; ABS is not track-count-bearing (S8) |
| 2 | `DHND–JOB` | **UNKNOWN** | as above |
| 3 | `JOB–BOBS` | **UNKNOWN** | as above |
| 4 | `BOBS–SHNX` | **UNKNOWN** | as above |
| 5 | `SHNX–DNK` | **UNKNOWN** | as above |
| 6 | `DNK–BDYK` | **UNKNOWN** | as above |
| 7 | `BDYK–KKU` | **UNKNOWN** | as above |
| 8 | `KKU–JP` | **UNKNOWN** | as above |
| 9 | `FL–BNWS` | **UNKNOWN** | Section boundary established; **no source covers its configuration** |
| 10 | `BNWS → … → KSG` | **UNKNOWN**, and the intermediate station set is itself unknown | no WTT/SWR coverage; NRI/DTRA/KSG membership unestablished |
| 11 | `… → MDJN` | **UNKNOWN** | no source coverage |
| — | `FL–HDA` | **RESOLVED = 2 running lines (Double Line)** | S4 explicit |
| — | `MDJN–AII` | **RESOLVED = 2 running lines (Double Line)** | S4 explicit |

**Definition used, stated once so it cannot be misread:** *"Double Line" = **2 running lines on
the open line**, exclusive of station loops, yard lines and sidings.* No source in this audit
states the number of lines **within** any station, and none is claimed (§5).

**Sections with a defensible running-line count: 2. Sections still UNKNOWN: 11 named + an
unknown number of unnamed sections between BNWS and MDJN.**

### 13.2 UNKNOWN — other

- **NRI** corridor membership.
- **DTRA** corridor membership.
- **KSG** corridor membership *(newly opened: Step 13 assumed it from an S1 map label; no
  operating source found confirms it)*.
- **Station set and block-section boundaries between BNWS and MDJN** — entirely unknown.
- **Phulera datum** rule/origin/restart. **Madar datum** rule/origin/restart.
- **Running-line identity / direction-specific track identity** anywhere on the corridor.
- **Station running lines vs loops** at every station — no SWR exists publicly.
- **DFC↔IR interface geometry** specifically at Phulera / Kishangarh / Madar.
- **Any doubling project** on JP–FL–AII: sanction, status, or commissioning.
- **Whether WTT 2025 is the edition in force on 2026-09-13.**

### 13.3 CONFLICT

| # | Conflict | Sources | Disposition |
|---:|---|---|---|
| C1 | **Madar code `MD` vs `MDJN`** | S1, S3 (`MD`) vs S4, S5, S2 (`MDJN`) | **CONFLICT.** `MDJN` preferred on source-type weighting (operating documents over maps). **Neither adopted.** Step 13 finding preserved. |
| C2 | **JOB chainage `277.83` vs `277.61`** | S1 (`277.83`) vs S4 (`277.61`) | **CONFLICT.** S4 preferred — it is the value that closes the 54.75 km chain arithmetically. **Neither adopted.** |
| C3 | **Phulera chainage `295.58` vs `0.00`** | S4 (295.58, JP chain) vs S1 (`FL 0.00`) | **Not a contradiction — two different chains.** But **no source states the rule relating them**, so it is recorded as an open datum question, not a conflict resolution. |

### 13.4 HISTORICAL / NON-COMMISSIONING items — recorded so they are never upgraded

| Item | Marked |
|---|---|
| Phulera–Ajmer "taken up 1993-94, target March 1995" (Parliament) | **HISTORICAL** |
| Ajmer–Phulera in 2009 Railway Budget material | **HISTORICAL** |
| NWR PROJECTS.pdf (2020) | **HISTORICAL** |
| PIB WDFC Rewari–Madar **trial run** (27-12-2019) | **NON-COMMISSIONING EVIDENCE** |
| S2 legend classes `BG DOUBLING WIP`, `PROP. NEW BG LINE` | **NON-COMMISSIONING EVIDENCE** — distinct from `BG DOUBLE LINE` |

**None of the above has been converted into a running-line count.**

---

## 14. EVIDENCE REQUIRED TO CLOSE THE REMAINING GAPS

| Gap | Evidence that would close it | Where it would come from |
|---|---|---|
| Running lines, `HDA–JP` (8 sections) | A WTT/SWR/line-capacity statement of track count for the automatic-block territory, **or** a CRS commissioning record for that stretch | Jaipur Division WTT; SWRs for JP, KKU, BDYK, DNK, SHNX, BOBS, JOB, DHND, HDA; CRS Western Circle |
| Running lines, `FL → MDJN` (the largest gap) | **The Jaipur Division Working Time Table** — section tables for FL–MDJN with System of Working. Single highest-value missing document. | NWR Jaipur Division Operating (not currently posted) |
| Station set & block-section boundaries `BNWS → MDJN` | The same Jaipur Division WTT station column, **or** the divisional block-section register | NWR Jaipur Division Operating |
| NRI / DTRA / KSG corridor membership | A WTT block-section list or block-section register naming them as block-section endpoints — the same topological test that settled BNWS | NWR Jaipur Division Operating |
| Station running lines (vs loops) at FL, MDJN, AII, JP | **Station Working Rules** — they state line configuration on both approaches and distinguish running lines from loops | NWR divisional SWR sets (not publicly posted) |
| Commissioned vs WIP doubling | **CRS sanction / authorization for opening**, naming the exact section and date | Commission of Railway Safety, Western Circle |
| Direction-specific track identity (UP/DN) | WTT / SWR line designations | as above — **must not be synthesised** |
| Phulera & Madar datum rule | Engineering chainage/datum record or WTT kilometering note stating restart points | NWR CE(P&D) / divisional engineering |
| Madar code resolution | An authoritative current master station-code list, or the Jaipur Division WTT station entry for Madar | Railway Board / NWR |
| WTT 2025 currency | The effective-date / correction-slip page of the WTT, or a WTT 2026 | NWR Operating |
| DFC↔IR interface at FL / KSG / MDJN | DFCCIL commissioned-section drawing or an IR–DFC interface schedule naming those points | DFCCIL official |

### 14.1 The single document to ask for — stated precisely

Almost every remaining gap resolves to **one document**, and it is worth naming it exactly
rather than generically, because a vague request will not be answerable.

The FL–JP pages in S4 are annotated **"Table 10 / AII-RE"** (PDF p.64, DN direction) and
**"Table 10 A / RE-AII"** (PDF p.72, UP direction), and they sit under the Jodhpur WTT's
**"OTHER DIVISION"** heading. In other words, **the FL–JP tables this audit relies on are an
extract of a longer Ajmer–Rewari route table**, reprinted by Jodhpur Division because its
trains work over that stretch.

> **What to request:** the **complete `AII–RE` / `RE–AII` route tables as printed in full by the
> owning division** — i.e. the Working Time Table pages carrying the continuous station column
> from **AII → MDJN → … → KSG → … → BNWS → FL → … → JP**, with the *System of Working* column.

One document would then supply, in a single station column and a single header:
running-line configuration for **`FL→MDJN`**; the **station set and block-section boundaries
between BNWS and MDJN**; **NRI / DTRA / KSG membership** by the same topological test that
settled BNWS; and the **Madar station-code field** in an operating document.

*(The WTT's own index is column-scrambled in extraction, so the table numbers above are quoted
as the annotations appearing on the FL–JP pages themselves, not asserted as the definitive
table numbering.)*

It is not publicly posted — the Jaipur and Ajmer Division Operating RTI pages were both
fetched directly and neither carries a WTT (§2.1). Obtaining it is an RTI / official-request
matter, outside this step's scope.

---

## 15. FINAL TOPOLOGY EVIDENCE GATE

# AMBER

**Why not GREEN.** GREEN requires current authoritative evidence sufficient to define the
running-line configuration **and** topology for the corridor. Two of thirteen-plus sections
have a running-line count. The entire Phulera→Madar half is uncovered by any available
operating document. NRI, DTRA and KSG membership is unestablished, so the section list for
that half cannot even be enumerated. Station running lines vs loops are unknown everywhere.
**GREEN was not forced.**

**Why not RED.** Step 13's RED was correct on the evidence then available: zero sections, zero
sequence confirmation, zero membership resolution. That is no longer the state. A current
authoritative operating document now yields: **two explicit running-line counts**
(`FL–HDA` = Double Line, `AII–MDJN` = Double Line); a **complete, arithmetically closed
station sequence, block-section boundary set and distance set for JP–FL**; **BNWS corridor
membership**; **positive DFC/IR separation evidence**; and a **corrected station list** that
exposes four stations Step 13 had missed. Reporting RED would understate real, verifiable
progress.

**AMBER is the honest classification:** authoritative evidence exists and is partially
sufficient, but one or more critical sections and attributes remain unresolved.

---

## 16. ELIGIBILITY FOR A FUTURE REAL_STATIC INGESTION STEP

**Jaipur–Ajmer is NOT eligible for REAL_STATIC topology ingestion. The answer to the headline
question is NO.**

`running_lines_per_section` must remain **unpopulated**. Two known values out of thirteen-plus
sections is not a topology; a partially-populated running-line vector would be more dangerous
than an empty one, because downstream code cannot distinguish "known to be 2" from "defaulted
to 2".

**One narrowly-scoped exception is now defensible and is offered for a later step's decision —
not taken here:**

> A **JP–FL station-sequence and block-section-geometry ingestion** — the 10 stations, their
> codes, the 9 block-section boundaries, the 9 inter-station distances and the closed 54.75 km
> chain — is supported by a single current authoritative source with a passing internal
> arithmetic check.

If any later step acts on that, it must be under these conditions, all of which follow from
findings above:

1. `running_lines_per_section` stays **UNKNOWN** for all nine JP–FL sections except `FL–HDA`,
   and `FL–HDA` is ingested **only** if a running-line value is ingested at all.
2. Provenance is recorded as **NWR WTT 2025**, with the **effective date recorded as unknown**
   and the caveat that it could not be verified as in force.
3. Scope is **JP–FL only**. FL→AII is not ingested in any form.
4. The `JOB` chainage conflict (S1 `277.83` / S4 `277.61`) is carried as a conflict, not silently resolved.
5. Datums are **not merged**. The JP–FL chain is not joined to the MDJN/AII chain.
6. The Madar code conflict stays open; neither `MD` nor `MDJN` is adopted.
7. `KSG`, `NRI`, `DTRA` are **not** placed on the corridor.
8. Nothing DFC-related — `New Phulera`, `New Kishangarh`, or S1's schematic inset — enters IR topology.
9. The synthetic dataset remains labelled SYNTHETIC until a full corridor is ingestible. A
   half-real corridor is not a real pilot and must not be described as one.

**Until the Jaipur Division WTT (or equivalent) is obtained, the corridor cannot be modelled
end-to-end from evidence, and `pashupatastra_realistic_dataset.json` stays SYNTHETIC.**

---

## APPENDIX A — REPOSITORY & DOWNLOAD INTEGRITY

**Downloads.** All source documents were fetched read-only into
`…\scratchpad\step14`, **outside the repository**. No downloaded PDF or HTML was added to Git.
No file was written anywhere under `D:\Pashupatastra\` except this report.

**Network conduct.** Read-only HTTP GETs to public government hosts only
(`nwr.indianrailways.gov.in`, `indianrailways.gov.in`, `pib.gov.in`). No authentication, no
credentials, no form submission, no NTES access, no access-control bypass, no data sent
anywhere, no operational change.

**Protected files — verified unchanged (MD5, before → after):**

| File | MD5 |
|---|---|
| `pashupatastra_realistic_dataset.json` | `5725c611afb1639d2a40bfc6d267a907` |
| `jobs.db` | `7a9d02c2ce37c57a7bd69b69483373fe` |
| `FINAL_BACKEND_SYSTEM_AUDIT.md` | `2805ec9fdbb1646e89664483e59e066a` |
| `PASHUPATASTRA_FINAL_DEMO_PLAYBOOK.md` | `9eb408bf7e4a27deca4f2db36eca17c1` |
| `SIH2026-IDEA-Presentation-Format.pdf` | `cb4ea2e2fac7f256a0b92086e1af549f` |
| `STEP13_RUNNING_LINE_VECTOR_ANALYSIS.md` | `71fbd35cd1983d7668cb5ae727f1d173` |

**Not touched:** SectionRegistry, corridor topology, track IDs, `running_lines_per_section`,
jobs, solver, timetable adapter, provenance, frontend, backend behaviour. No test suite was
run and no backend process was started, so `jobs.db` was never opened.

**Git:** no `git add`, no commit, no push, no branch change, no stash operation.
`stash@{0}` untouched. HEAD remains `d9f0264`.

The verification transcript is in §FINAL VALIDATION of the accompanying response.
