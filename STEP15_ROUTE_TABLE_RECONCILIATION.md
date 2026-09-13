# STEP 15 — COMPLETE AII–RE ROUTE-TABLE RECOVERY & TOPOLOGY RECONCILIATION

**Status:** evidence/research gate only. No production data, code, topology, SectionRegistry,
`running_lines_per_section`, track IDs, jobs, solver, timetable adapter, provenance, frontend,
dataset or `jobs.db` was touched. No commit. No push. This file is the only repository artifact.

**Audit date (all retrievals):** 2026-09-13
**Scope:** AII ↔ MDJN ↔ … ↔ FL ↔ … ↔ JP (the Jaipur–Ajmer part of the AII–RE route), NWR.
**Predecessors:** Steps 12–14 APPROVED. Step 14 gate: AMBER.

Evidence-class vocabulary used throughout: **EXPLICIT · STRONGLY CORROBORATED · PARTIAL ·
AMBIGUOUS · CONFLICT · UNKNOWN**. No numeric confidence percentages are used.

"Double Line" = **2 running lines on the open line**, exclusive of loops, yard lines and sidings.

---

## 1. EXECUTIVE VERDICT

**Final topology evidence gate: AMBER** (unchanged class, materially stronger evidence).
**REAL_STATIC ingestion: NOT ELIGIBLE.**

**The complete AII–RE / RE–AII route table was NOT recovered.** No publicly posted official
document carries the continuous station column AII → MDJN → … → FL → … → JP. The owning
division (Jaipur) posts no WTT; the Ajmer Division posts none; both Jodhpur Division editions
(2025 and 2021) reprint only the **FL ↔ JP window** of that table under "OTHER DIVISION".

What Step 15 did establish that Step 14 did not:

| # | Finding | Class |
|---:|---|---|
| 1 | A second, **historical** operating source was found on the same NWR page Step 14 used: the **JU-2021 Working Time Table**, hidden behind a link mislabelled **"ACCIDENT MANUAL"**. Image-only; every reading below is from rendered pages. | — |
| 2 | **NRI is on the corridor, adjacent to BNWS**: JU-2021 PSR appendix names block section **"Bhanwsa-Naraina"** / **"Naraina-Bhanwsa"** under heading **"JP-FL-MD"**. | EXPLICIT (2021, historical) → current membership STRONGLY CORROBORATED |
| 3 | **Datums are now explained, not merged.** WTT 2025 column headers state them: Table 10/10A **"Distance From AF"** (JP–FL chain, FL 295.58); Table 12/12A **"Distance From RE Via Chord"** (MDJN 288.37, AII 294.57). S1 prints at Phulera **"F.RE.215.12, F.AF.295.58"** and WTT 2025 Sectional Distance gives Phulera–Ringus 66.73 + Ringus–Rewari 148.39 = **215.12** exactly. | EXPLICIT |
| 4 | The CRS rows **"Phulera–Madar 68.859"** and **"Kanakpura–Phulera 48.622"** are in table **E – ELECTRIFICATION OF RAILWAY LINES**. | EXPLICIT — **electrification only; NOT doubling evidence** |
| 5 | **FL–HDA = 2** and **AII–MDJN = 2** re-verified on rendered pages of WTT 2025. | EXPLICIT, CONSISTENT with Step 14 |
| 6 | **KSG** is placed between MDJN and JP by three independent official sources (2021 WTT SoW "EI at KSG"; NCR circular 29-09-2025 stop order MDJN→KSG→JP; S1 label on the RE chain). No block-section statement names it. | STRONGLY CORROBORATED (not EXPLICIT) |
| 7 | "Kishangarh Balawas" is positively placed on the **Rewari–Bhiwani** section (NWR Construction, Jul-2026), so it is not KSG. | EXPLICIT |
| 8 | No doubling project on JP–FL or FL–AII appears in NWR Construction's current project list (as on 31-08-2026) or FY24-25 / FY25-26 commissioning lists. | EXPLICIT absence — **proves nothing about current track count** |
| 9 | S1 carries **seven further labels** between FL and MD that Step 13 never recorded (SAKHUN, SALI 'H', GEHLOTA, TILONIYA 'H', MANDAWARIYA, GEGAL AKHRI, LADPURA). | Map labels only — membership **UNKNOWN** |
| 10 | Step 14 page/table references contain systematic offsets (§4.4). Corrected here. | — |

**Running lines remain known for only two sections.** HDA–JP (8 block sections) has
2021-historical "Double line Lock & Block" but the current 2025 edition narrows that statement
to FL–HDA and places HDA–JP under Automatic Block Signalling, which is not track-count-bearing.
The entire FL→MDJN half has **no current** running-line statement at all.

---

## 2. SOURCES SEARCHED

All retrievals were read-only public HTTP GETs. No authentication, forms, NTES or internal systems.
Downloads were stored in the session scratchpad **outside the repository**
(`…\scratchpad\step15`). A PDF rendering library (PyMuPDF) was installed **into that scratchpad
directory only** (`pip --target`) during the first part of this step, before the no-install
instruction was given; no system or project environment was changed and nothing further was installed.

| Target | Result |
|---|---|
| NWR Jaipur Division → RTI 4(1)(b) → Operating (`id=0,1,291,360,481`, reviewed 05-06-2025) | Org chart, duty list, CUG, tech-op, CVC, RTI. **No WTT, no line capacity.** |
| NWR Ajmer Division → Operating (`…353,452`, reviewed 12-08-2025) | System map, line capacity, achievements. **No WTT.** |
| NWR Jodhpur Division → Operating (`…355,465`, reviewed 26-03-2025) | WTT 2025 (S4, Step 14) **and** a second file `1661945473567-WORKING TIME TABLE.pdf` behind link text **"ACCIDENT MANUAL"** → JU-2021 WTT (S11) |
| NWR HQ → Operating (`…304,375`, reviewed 04-08-2026) | `1752495555226-NWR.pdf` = NWR system map, AutoCAD, 25-04-2025 (S12) |
| NWR HQ → Construction (`…304,383`, reviewed 07-09-2026) | Projects (31-08-2026), Achievements FY24-25 & FY25-26, Ongoing works Jul-26, Project Map |
| NWR HQ → Engineering (`…304,373`, reviewed 29-06-2026) | Level Crossing and Bridge lists — **division summaries only** (rendered; no section data) |
| Commission of Railway Safety `crs.gov.in/?page_id=714` | English annual reports 2014-15, 2015-16, 2018-19, 2019-20, 2020-21, 2021-22, 2022-23, 2023-24, 2024-25 downloaded |
| Indian Railways zonal portals (search) | NCR circular for train 19623/19624 (S15) |
| DFCCIL (`dfccil.com`) | WDFC station list (S16); cement-siding press release (not relevant to interfaces) |
| Web search for FL–MDJN WTT, Jaipur/Ajmer WTTs, CRS doubling authorisation, station-code master | No further official document found |

**Non-authoritative leads seen in search results and NOT used for any conclusion:** Wikipedia
(Bhanwsa/Dantra/Naraina/Madar station articles, which assert track counts), indiarailinfo
(station pages; a mirror of an NWR "sanctioned works" map), Scribd (copy of an NWR project map),
Grokipedia, Yatra, railtravels.org, deshgujarat. None closes any gap.

**Limitation:** CRS reports 2020-21, 2022-23 and 2023-24 have no text layer and were **not**
searched page-by-page. CRS reports before 2014-15 and 2016-17/2017-18 are not posted. The
doubling-commissioning search via CRS is therefore **incomplete**, stated as such.

---

## 3. SOURCES USED (with identifiers)

| ID | Title | Publisher / location | Date / version | SHA-256 | Currentness |
|---|---|---|---|---|---|
| **S4** | NWR Working Time Table, Jodhpur Division edition ("JU 2025" / "WTT 2025") | NWR Jodhpur Div. Operating, `uploads/files/1742970525725-9 - Working Time Table.pdf` | PDF created 2024-12-14; posted 2025-03-26; **no w.e.f. printed** | `4cac6fa3f6bc34c7e6d91bfbb4710b513f55bf185b288a524051db8cce05950b` (**re-verified = Step 14**) | OPERATIONAL/CURRENT-ish (latest posted; in-force status unverifiable) |
| **S11** | NWR Working Time Table, Jodhpur Division edition ("JU-2021") | same page, `uploads/files/1661945473567-WORKING TIME TABLE.pdf`, link text **"ACCIDENT MANUAL"** | modDate 2021-10-03; producer iLovePDF; 151 pp; **0 text characters** | `7dbf75395cb97c7f09be85d426314154291093dcd0f10d2a2681684ce9d1f90f` | **HISTORICAL** (superseded by S4) |
| **S1** | NWR HQ System Map, corrected to 01-04-2026 (Step 13) | NWR HQ CE(P&D) | created 2026-04-24 | `a721da1026e811fc8f8475211210a5c5f28903e47941a698994a99785fe0ac16` | CURRENT (map — not a route table) |
| **S12** | NWR System Map (HQ Operating copy) | `uploads/files/1752495555226-NWR.pdf` | AutoCAD 2023; created 2025-04-25 | `77290a55f175dca8a2112e9b63e96acc966a9cc94f705c1d1e2e7914015b865f` | CURRENT-ish; same corridor labels as S1 |
| **S2** | NWR Ajmer Div. System Map, 01-04-2026 (Step 13) | NWR Ajmer | 2026 | `379760af9329cd30670c24ebd062c53db02c7d5f2f2a623fddcdfe7b299cc6a9` | CURRENT (map) |
| **S13a** | CRS Annual Report 2018-19 (English) | `crs.gov.in/wp-content/uploads/2024/03/AR-2018-19-Eng-1.pdf` | FY 2018-19 | `d1cdf92d4fc05fd5bb140f1a9932ae9b5d6852c1bba530b99dfcd598cf0ae7da` | HISTORICAL; electrification COMMISSIONED 2019 |
| **S13b** | CRS Annual Report 2019-20 | `…/2024/03/AR-2019-20.pdf` | FY 2019-20 | `3a15d9544676b9b2b47767fd3d56e6cab441d0d95383f88c3381a45c2c45fced` | HISTORICAL; electrification COMMISSIONED 2019 |
| **S14a** | NWR Construction — Ongoing NL/GC/DL projects | `…/1788759375011-Projects (1).pdf` | "As on 31.08.2026" | `f1437ff00c09fb7487894aa341b65c1d607f1b9790870ddad0e4f27c0fd0aae6` | CURRENT |
| **S14b** | NWR Construction — Ongoing works contracts | `…/1787038285013-Ongoing works July 26.pdf` | created 2026-08-17 | `9954f0c5cd951a9ae230d020af479e744823c4cfeb1d13c9fb3e1529c03e2b91` | CURRENT |
| **S14c** | NWR Construction — Major achievements FY 2024-25 | `…/1768216963118-5. Major Achievements of FY24-25 010625.pdf` | "As on 01.01.2026" | `27a6a2de441fafe8a1766deb9238f357dccd44aba9e8a818be10e021bee5de66` | CURRENT |
| **S14d** | NWR Construction — Achievements FY 2025-26 | `…/1776333838160-5.Achievements…2025-2026.pdf` | "As on 31.03.2026" | `9078f81a6977410cab6cde6090bea6f2e3578a4c8b6c2bb789c3319063959533` | CURRENT |
| **S15** | NCR Operating circular No. Optg/New Trains/NCR/25 — train 19623/19624 Madar Jn–Darbhanga | `ncr.indianrailways.gov.in/cris//uploads/files/1759918492835-New train 19623 exp.pdf` | 29-09-2025; w.e.f. 03-10-2025 | `cdb3eeddf1b8ea537e8aff45408364100bb012a468acf03872eac6c05fb4e72f` | CURRENT (passenger circular — codes/stop order only) |
| **S16** | DFCCIL "Layout of locations on WDFC" | `dfccil.com/images/uploads/img/WDFC---LAYOUTS_VZFU.pdf` | created 2020-10-22 | `c22d91df7a0fcbc36cf8a5613cf07623646e352c1e75d5038898bec86926a7a3` | HISTORICAL list of DFC stations |
| S8, S9, S10 | IRSEM Ch.20; PIB WDFC 2019 / 2021 | as Step 14 | as Step 14 | as Step 14 | as Step 14 |

---

## 4. ROUTE-TABLE SOURCES FOUND, PAGES INSPECTED, RENDERED INSPECTIONS

### 4.1 Complete route table — NOT FOUND

**S4 index, PDF p.3 (rendered, clean table):** under **"OTHER DIVISION"**:
`PHULERA JN. - JAIPUR JN. | Table 10 | pp.61-68` and `JAIPUR JN. - PHULERA JN. | Table 10A | pp.69-76`;
`MARWAR JN. - AJMER JN. - MARWAR JN. | Table 12 | pp.107-108`. **No Phulera–Madar table exists
in the Jodhpur WTT.** The FL–JP pages carry the owning table's labels "Table 10 / AII-RE" and
"Table 10 A / RE-AII" but only the FL↔JP station window.

**S11 index, PDF p.3 (rendered):** identical structure — "OTHER DIVISION: PHULERA JN.-JAIPUR JN.
Table 10 pp.53-60; JAIPUR JN.-PHULERA JN. Table 10A pp.61-68". Rendered thumbnails of PDF
pp.56–76 show every Table 10/10A page with the same ten-station FL↔JP column. **The 2021 edition
does not extend the station column past Phulera either.**

### 4.2 Pages actually inspected

| Source | PDF page | Printed | Method | What was read |
|---|---:|---|---|---|
| S4 | 3 | — | rendered 110 dpi | Division index / OTHER DIVISION |
| S4 | 65 | 61 | rendered 110 dpi + 300 dpi crops | Table 10 AII-RE, FL-JP (DN): header, SoW, station column, "Distance From AF" |
| S4 | 73 | 69 | rendered 110 dpi | Table 10A RE-AII, JP-FL (UP): header, SoW, station column |
| S4 | 81 | 77 | rendered header crop | Table 11 PNU-ABR (DN) SoW incl. `{AII-MDJN} Double Line` |
| S4 | 81–95, 96–110, 111, 112 | 77–108 | text search (32 pages) | `{AII-MDJN} Double Line` (16 pp) / `{MDJN-AII} Double Line` (16 pp) |
| S4 | 111 | 107 | text | Table 12 MJ-MDJN (DN): `{AII-MDJN} Double Line`, "Distance From RE Via Chord" |
| S4 | 112 | 108 | rendered 110 dpi | Table 12A MDJN-MJ (UP): SoW, MADAR Jn. `MDJN (B) (III R)` 288.37, AII 294.57, "Distance From RE Via Chord" |
| S4 | 149 | 145 | rendered 120 dpi | App. IX-A PSR: `JP-FL-MDJN (UP-TRAINS)` / `MDJN-FL -JP(DOWN-TRAINS)` |
| S4 | 178 | (xiv) | rendered 110 dpi | Sectional Distance, BG |
| S11 | 1–6 | — | rendered 40 dpi | foreword, index, appendix index |
| S11 | 56–76 | 52–72 | rendered thumbnails | Table 10/10A extent |
| S11 | 58 | 53 | rendered 150/250 dpi | Table 10 AII-RE FL-JP header, SoW, station column |
| S11 | 66 | 61 | rendered 200 dpi crop | Table 10A RE-AII JP-FL header, SoW, station column |
| S11 | 138–151 | 134–147 | rendered thumbnails | appendix extent |
| S11 | 140 | 136 | rendered 130 dpi | Mega block (not relevant) |
| S11 | 143 | 139 | rendered 120 dpi | PSR: `JP-FL-MD (UP-TRAINS)` / `MD-FL -JP(DOWN-TRAINS)` |
| S1, S12 | 1 | — | text layer with coordinates; 600 dpi crops at Phulera | FL–MD labels, `F.RE.215.12,F.AF.295.58`, JP `F.SWM-131.27,AF-240.83` |
| S13a | 63, 66, 67 | — | rendered 75 dpi | Table E Electrification, SN 94 |
| S13b | 69–72 | 57–60 | rendered 75 dpi | Table E Electrification, SN 57, 58 |
| S14a–d, S15, S16 | all | — | text | project / commissioning / stop-order / DFC station lists |
| Eng. LC & Bridge lists | 1 | — | rendered 100 dpi | division summaries only — no section data |

### 4.3 Rendered vs. text

S11 has no text layer: **all S11 readings are visual**. For S4 every table fact used below was
read from a rendered page, not from `pdftotext` output. The previously column-scrambled S4 PSR
appendix and Sectional Distance appendix are now read from renders and are legible.

### 4.4 Corrections to Step 14 page/table references (rendered S4)

| Step 14 said | Rendered S4 shows |
|---|---|
| JP↔FL = "Tables 10A/11", "PDF pp.64–79", "printed page 41" | **Table 10 (FL-JP DN, "AII-RE")** PDF 65–72, printed 61–68; **Table 10A (JP-FL UP, "RE-AII")** PDF 73–80, printed 69–76 |
| AII–MDJN headers "Tables 11A/12/13/13A, pp.80–111" | **Tables 11, 11A, 12, 12A**, PDF 81–112 (32 pages) |
| MADAR Jn. entry "p.110" | **PDF 111 (Table 12) and 112 (Table 12A)** |
| PSR appendix "p.148" | **PDF 149**, printed 145 |
| Sectional distances "p.177", MEDIUM confidence | **PDF 178**, printed (xiv); now read from render → EXPLICIT |

The **substance** of every Step 14 claim checked here is confirmed; only the locators change.

---

## 5. RUNNING-LINE / SYSTEM-OF-WORKING EVIDENCE (directly stated)

### 5.1 WTT 2025 (S4) — CURRENT operating statements

**Table 10 FL-JP (DN), PDF 65 (rendered):**
> Absolute Block System & Double line Lock & Block between FL-HDA · Automatic Block Signalling bet HDA-JP · Panel Interlocking between BOBS-KKU · & EI at HDA, FL, JOB & JP · MACLS between FL-JP — Electrified Section, Main Line, 54.75 Kms, 130 KMPH, Route "B"

**Table 10A JP-FL (UP), PDF 73 (rendered):**
> Automatic Block Signalling bet JP-HDA · Absolute Block System & Double line Lock & Block between HDA-FL · Panel Interlocking between KKU-BOBS · & EI at JP, JOB HDA & FL · MACLS between JP-FL

**Table 11 PNU-ABR (DN), PDF 81 (rendered) — repeated on Tables 11/11A/12/12A:**
> Absolute Block System: {PNU-AII} Double Line SGE Lock & Block (BPAC) Block Instrument · {DOZ-MDJN Bye Pass} Single Line UFSBI (BPAC) Tokenless Block Instr. · Automatic Block System: {AII-MDJN} Double Line

**Table 12A MDJN-MJ (UP), PDF 112 (rendered):**
> Automatic Block System {MDJN-AII} Double Line · Absolute Block System {AII-PNU} Double Line SGE Lock & Block (BPAC) Block Instrument · {MDJN-DOZ Bye Pass} Single Line UFSBI (BPAC) Block Instrument

**Result:**
- **FL–HDA = 2 — EXPLICIT, CURRENT, CONSISTENT with Step 14.**
- **AII–MDJN = 2 — EXPLICIT, CURRENT, CONSISTENT with Step 14.**
- **DOZ–MDJN Bye Pass = 1** — a separate bypass line, not the corridor running line.
- **HDA–JP:** system of working = Automatic Block Signalling. **Running lines UNKNOWN.** IRSEM
  Ch.20 §20.1.1/§20.1.3 (S8) provides Automatic Block on single **and** double line, so ABS
  carries no track count. No double-line inference is drawn from it.

### 5.2 JU-2021 WTT (S11) — HISTORICAL operating statements

**Table 10 AII-RE, FL-JP, DN TRAINS, PDF 58 (rendered):**
> FL-JP · BG Main Line · Controlled Section 134.20 Kms · Hectometer Posts 10 Per Kms · 110 kmph ·
> **SYSTEM OF WORKING: Absolute Block System · Double line Lock & Block · Panel Interlocking between KKU-MDJN & EI at KSG, FL, JOB & JP · MACLS between MDJN-JP** · Route "B" (Electrified)

**Table 10A RE-AII, JP-FL, PDF 66 (rendered):** same SoW text; "Cont. Sect. Km,134.20".

**Assessment:**
- "Double line Lock & Block" is **unqualified** in 2021. Its scope is **AMBIGUOUS**: it certainly
  covers the table's own FL–JP section; the adjacent lines ("PI between KKU-MDJN", "MACLS between
  MDJN-JP", "EI at KSG", controlled section 134.20 km ≈ JP–AII 134.62) suggest a header written
  for the wider controlled section, but the double-line clause itself names no endpoints.
- **FL–JP double line in 2021: EXPLICIT, HISTORICAL.**
- **FL–MDJN double line in 2021: AMBIGUOUS** (scope not stated).
- The 2025 edition **narrows** the double-line clause to FL–HDA and moves HDA–JP to ABS. A 2021
  statement cannot be carried forward as a current running-line count. The narrative "double
  throughout in 2021, HDA–JP re-signalled to ABS by 2025" is plausible but is **inference, not
  evidence**, and is not used to close any section.

### 5.3 Electrification is not doubling (S13a, S13b)

| Source | Table (rendered heading) | SN | Date | Section | Railway col. | KMs |
|---|---|---:|---|---|---|---:|
| CRS AR 2018-19, PDF 66 | **E- ELECTRIFICATION OF RAILWAY LINES** (heading PDF 63) | 94 | 25.03.19 | Phulera - Madar | WR | 68.859 |
| CRS AR 2019-20, printed 58 | **E- ELECTRIFICATION OF RAILWAY LINES** (heading printed 57) | 57 | 27.11.19 | Kanakpura-Phulera | WR | 48.622 |
| CRS AR 2019-20, printed 59 | same table | 58 | 27.11.19 | Phulera-Madar | WR | 68.859 |

These are **electrification authorisations** (COMMISSIONED electrification, 2019). They carry
**no track-count information and are NOT doubling-commissioning evidence.** They are consistent
with S4's "Electrified Section". The duplicate Phulera–Madar entry (2018-19 and 2019-20, identical
km) is recorded verbatim and **not interpreted** (e.g. not read as "one line each").

### 5.4 Doubling status (S14a–d)

- S14a (as on 31-08-2026) lists 15 ongoing doubling/bypass projects; **none is on JP–FL or FL–AII**
  (nearest: Ajmer–Chanderiya, Jaipur–Sawai Madhopur, Ringus–Sikar).
- S14c/S14d commissioning FY24-25 and FY25-26: **none on the corridor**. S14c records
  *Phulera–Degana* doubling commissioned 27-04-2024 — a different line (Phulera–Merta Road).
- S14b ongoing contracts mentioning Phulera are all **Phulera–Degana** works.
- **No sanction, WIP or commissioning record for any JP–FL–AII doubling was found.** The absence of
  a current project is not proof that the corridor is already double (it is equally consistent with
  no project ever being sanctioned). Classified **UNKNOWN** for commissioning status.

---

## 6. STATION-ORDER EVIDENCE — RECONSTRUCTED SEQUENCES

### 6.1 JP ↔ FL (EXPLICIT, current — S4 Tables 10/10A, rendered)

| # | Station (printed) | Code | Class DN (T10) | Class UP (T10A) | Distance From AF | Intersectional |
|---:|---|---|---|---|---:|---:|
| 1 | PHULERA Jn. | FL | [B] [II] | [B], [II R] | 295.58 | 8.33 |
| 2 | Hirnoda | HDA | [B] [III] | [B], [III] | 287.25 | 4.20 |
| 3 | Dhindha | DHND | [D] | [D] | 283.05 | 5.44 |
| 4 | Asalpur Jobner | JOB | [B] [III] | [B], [III] | 277.61 | 7.47 |
| 5 | Bobas | BOBS | [B] [III] | [B], [III] | 270.14 | 7.14 |
| 6 | Sheo Singhpura | SHNX | [C] | [D] | 263 | 3.48 |
| 7 | Dhanakya | DNK | [B] [III] | [B], [III] | 259.52 | 4.82 |
| 8 | Bindayaka | BDYK | [C] | [D] | 254.7 | 4.93 |
| 9 | Kanakpura | KKU | [B] [III] | [B], [III] | 249.77 | 8.94 |
| 10 | JAIPUR Jn. | JP | [B] [II R] | [B], [II R] | 240.83 | — |

Distances sum to 54.75 km (header). UP table prints chainages rounded (241 … 296). The class
markers differ between DN and UP for SHNX, BDYK and FL — recorded verbatim, **undecoded, not
used**. JU-2021 (S11 PDF 58) shows the same ten stations and codes, plus rows
"IBS DN KKU/JP 247" (DN) and "IBS UP JP-KKU 246.2" (UP) — intermediate block signals, **not used
for any track-count inference**.

### 6.2 FL → MDJN (PARTIAL)

| Order evidence | Source | Class |
|---|---|---|
| `FL-BNWS` (UP) / `BNWS-FL` (DN) under `JP-FL-MDJN` / `MDJN-FL -JP`, km 215/21–216/1 | S4 PDF 149 (rendered) | **EXPLICIT, current** — FL and BNWS adjacent block stations |
| `Phulera Yard (FL-BNWS) 215/5-216/0`, `Bhanwsa-Naraina 224/3-224/8` (UP); `Naraina-Bhanwsa 224/9-224/3`, `FL-Yard 216/0-215/5` (DN), under `JP-FL-MD` / `MD-FL -JP` | S11 PDF 143, printed 139 (rendered) | **EXPLICIT, historical 2021** — BNWS and NRI adjacent block stations |
| `EI at KSG` inside the AII-RE Table 10/10A SoW (with PI KKU-MDJN, MACLS MDJN-JP) | S11 PDF 58, 66 | **EXPLICIT that KSG is inside the controlled section**; position/adjacency not stated |
| Stop order `MADAR JN MDJN → KISHANGARH KSG → JAIPUR JN JP`; "MDJN-JP-MDJN = JP DIV" | S15 (29-09-2025) | **Current corroboration** of KSG between MDJN and JP; a passenger circular, not a block-section list |
| Map label chain between FL and MD (see 6.4) | S1 / S12 | map labels only — **not membership evidence** |

### 6.3 Reconstructed sequences

**AII → RE (DN direction of Table 10 "AII-RE"):**

```
AII ─(2, EXPLICIT)─ MDJN ─[ UNKNOWN station set; KSG STRONGLY CORROBORATED somewhere inside ]─
    NRI ─(block section, 2021 EXPLICIT)─ BNWS ─(block section, 2025 EXPLICIT)─ FL
    ─(2, EXPLICIT)─ HDA ─ DHND ─ JOB ─ BOBS ─ SHNX ─ DNK ─ BDYK ─ KKU ─ JP ─[beyond JP: not in scope, not recovered]→ RE
```

**RE → AII (UP, Table 10A "RE-AII"):** the reverse of the above; S4 Table 10A and Table 12A
give identical station sets in reverse. **No direction-specific difference in station set was
found.**

**Complete sequence recovered: NO.** Established continuously: **JP → FL → BNWS → NRI** (FL–BNWS
current; BNWS–NRI 2021) and **MDJN → AII**. **Gap: NRI → … → MDJN**, including the block stations,
their order, and KSG's neighbours.

### 6.4 Map-only candidates between FL and MD (S1, corroborated identically by S12)

Labels, in chainage order on the RE chain: `PHULERA(FL) 0.00 / F.RE.215.12` · `BHANWSA (BNWS) 220.39` ·
`NARAINA (NRI) 225.28` · `DANTRA 'H' (DTRA) 229.76` · `SAKHUN (SK) 237.24` · `SALI 'H' (SALI) 244.09` ·
`GEHLOTA (GLRA) 251.14` · `TILONIYA 'H' (TL) 255.48` · `MANDAWARIYA (MNDV) 260.37` ·
`KISHANGARH (KSG) 269.618` · `GEGAL AKHRI (GEK) 275.59` · `LADPURA (LR) 281.242` · unlabeled `285.77` ·
`MADAR (MD) 288.37.0.00`.

These form a monotone sequence and are **consistent** with the S4/S11 PSR kilometres (FL 215–216,
BNWS–NRI 224). **Per the Step 15 rule they are NOT added to the route.** Additional caution:
DFCCIL stations on the same stretch mirror IR names (**New Sakhun**, New Kishangarh, New Phulera
Jn — S16), and `GEGAL AKHRI` also appears in S1's IR/DFC schematic inset, so a map label may
belong to DFC geometry.

---

## 7. CHAINAGE / DATUM EVIDENCE

### 7.1 Stated datums

| Datum | Statement | Source | Class |
|---|---|---|---|
| **AF chain** (JP–FL) | Column header **"Distance From AF"**; JP 240.83 … FL 295.58 | S4 T10 PDF 65 (300 dpi crop), T10A PDF 73 | EXPLICIT |
| AF at Jaipur | `JAIPUR(JP) … F.SWM-131.27,AF-240.83` | S1 | EXPLICIT (map annotation) |
| **RE chain** (MDJN–AII–MJ) | Column header **"Distance From RE Via Chord"**; MADAR Jn. 288.37, AJMER Jn. 294.57, intersectional 6.20 | S4 T12 PDF 111, T12A PDF 112 (rendered) | EXPLICIT |
| RE at Phulera | `PHULERA(FL)0.00` / **`F.RE.215.12,F.AF.295.58`** | S1 main body (600 dpi crop); identical in S12 | EXPLICIT (map annotation) |
| Arithmetic check of F.RE | Phulera–Ringus **66.73** + Ringus–Rewari **148.39** = 215.12 (printed group total **215.12**) | S4 Sectional Distance PDF 178 (rendered) | EXPLICIT |
| JP is not on the RE-via-Alwar datum | Jaipur–Bandikui 90.32 + Bandikui–Alwar 60.37 + Alwar–Rewari 74.21 = **224.9** ≠ 240.83 | S4 PDF 178 | EXPLICIT |
| JP–SWM | Jaipur–Sawai Madhopur **131.27** = S1 `F.SWM-131.27` | S4 PDF 178, S1 | EXPLICIT |
| Operating use of both datums | 2025 PSR: `JP-KKU 241/101–242/9`, `BOBS-JOB 275/11–276/03` (AF) but `FL-BNWS 215/21–216/1` (RE); 2021 PSR: `FL-BNWS 215/5–216/0`, `Bhanwsa-Naraina 224/3–224/8` (RE), `JP-Yard 241/3–240/6` (AF) | S4 PDF 149; S11 PDF 143 | EXPLICIT |
| Local 0.00 at FL | Phulera–Degana works "from Km.34.50 to Km. 00.00", Phulera incl. | S14b p.11 | EXPLICIT for the FL–Degana line origin |

### 7.2 Phulera result

**Phulera carries three distinct kilometrage references, and they are NOT one scale:**
`295.58` from AF (JP–FL route), `215.12` from RE via Ringus (continuing toward BNWS/MDJN), and
`0.00` as origin of the Phulera–Degana line. The discontinuity at Phulera is a **route-datum
change**, now EXPLICIT. **295.58 and 215.12 must never be added, subtracted or joined.**

### 7.3 Madar result

**MDJN = 288.37 "From RE Via Chord" — EXPLICIT (S4 T12A).** S1 also prints `288.37.0.00`; the
`0.00` is a second reference originating at Madar. S1 labels on the Madar–Nasirabad–Bijainagar
side (`ADARSH NAGAR 5.34`, `HATUNDI 10.48`, `NASIRABAD 23.36` … `BIJAINAGAR 65.13`) are consistent
with that branch origin, but that association is from map labels only → **PARTIAL**.

### 7.4 Unreconciled residuals and conflicts (kept, not resolved)

| # | Item | Values | Disposition |
|---:|---|---|---|
| D1 | FL–AII distance | S4 App. XI Ajmer–Phulera **79.87** vs RE-chain difference 294.57 − 215.12 = **79.45** | **CONFLICT (0.42 km).** "Via Chord" (T12A) and "via Ringus" (Sectional Distance) may not be the same path; App. XI also states it is not for technical use. Not reconciled. |
| D2 | JP–FL datum label | S4 2025 **"Distance From AF"** vs S11 2021 **"Distance From RE (Chord)"** for the same JP 240.8 / FL 295.6 values | **CONFLICT.** 2025 label is corroborated by S1 `AF-240.83` and by RE-via-Alwar = 224.9 ≠ 240.83. 2021 label recorded as inconsistent; **neither label is adopted into data.** |
| D3 | Phulera RE value in S1 inset | S1 main body `F.RE.215.12` vs S1 IR/DFC schematic inset `F.RE.515.12` | **CONFLICT inside S1.** Main body agrees with S4 arithmetic; inset not used. |
| D4 | JOB chainage (Step 14 C2) | S1 `277.83` vs S4 `277.61` (S11 `277.6`) | **CONFLICT carried forward.** Not adopted. |

---

## 8. SECTION-BY-SECTION EVIDENCE TABLE

"Chainage" shows the section's endpoints on the **stated** datum. Running Lines are **current**.

| Section | Station A | Station B | Chainage | Source | System of Working | Running Lines | Evidence Class | Confidence | Notes |
|---|---|---|---:|---|---|---:|---|---|---|
| JP–KKU | JP | KKU | AF 240.83–249.77 | S4 T10/10A PDF 65/73 | Automatic Block Signalling | UNKNOWN | PARTIAL | Boundary high; lines none | 2021: "Double line Lock & Block" (HISTORICAL); ABS not track-count-bearing |
| KKU–BDYK | KKU | BDYK | AF 249.77–254.70 | S4 | ABS; PI BOBS–KKU | UNKNOWN | PARTIAL | as above | as above |
| BDYK–DNK | BDYK | DNK | AF 254.70–259.52 | S4 | ABS; PI | UNKNOWN | PARTIAL | as above | as above |
| DNK–SHNX | DNK | SHNX | AF 259.52–263.00 | S4 | ABS; PI | UNKNOWN | PARTIAL | as above | as above |
| SHNX–BOBS | SHNX | BOBS | AF 263.00–270.14 | S4 | ABS; PI | UNKNOWN | PARTIAL | as above | as above |
| BOBS–JOB | BOBS | JOB | AF 270.14–277.61 | S4 | ABS | UNKNOWN | PARTIAL | as above | JOB chainage CONFLICT D4 |
| JOB–DHND | JOB | DHND | AF 277.61–283.05 | S4 | ABS | UNKNOWN | PARTIAL | as above | as above |
| DHND–HDA | DHND | HDA | AF 283.05–287.25 | S4 | ABS | UNKNOWN | PARTIAL | as above | as above |
| **HDA–FL** | HDA | FL | AF 287.25–295.58 | S4 T10/10A PDF 65/73 (rendered) | **Absolute Block & Double line Lock & Block** | **2** | **EXPLICIT** | High | CONSISTENT with Step 14; 2021 also double |
| FL–BNWS | FL | BNWS | RE 215.12–(215/21–216/1 PSR) | S4 PDF 149; S11 PDF 143 | not stated | UNKNOWN | PARTIAL | Adjacency high; lines none | Block section EXPLICIT (2025 & 2021) |
| BNWS–NRI | BNWS | NRI | RE (PSR 224/3–224/8) | S11 PDF 143 | not stated | UNKNOWN | PARTIAL | Adjacency: historical | Block section EXPLICIT 2021 only |
| NRI–…–MDJN | NRI | MDJN | RE 225.28(map)–288.37 | none (map S1 only) | 2021: PI KKU–MDJN, MACLS MDJN–JP, EI at KSG; DL clause scope AMBIGUOUS | UNKNOWN | UNKNOWN | None | Station set, order, block boundaries all unknown; KSG inside (STRONGLY CORROBORATED) |
| **MDJN–AII** | MDJN | AII | RE 288.37–294.57 | S4 T11/11A/12/12A, PDF 81–112 (rendered 81, 112) | **Automatic Block System, Double Line** | **2** | **EXPLICIT** | High | CONSISTENT with Step 14 |
| DOZ–MDJN Bye Pass | DOZ | MDJN | — | S4 same headers | UFSBI (BPAC), Single Line | 1 | EXPLICIT | High | Separate bypass; **not** corridor running line |

**Defensible current running-line counts: 2 sections (HDA–FL, MDJN–AII).**

---

## 9. ROUTE RECONCILIATION TABLE

Positions count from JP along the established sequence; "—" = position not established.

| Position | Station | Code | AII–RE Table | S1 | S2 | Step 13 | Step 14 | Final Evidence Status |
|---:|---|---|---|---|---|---|---|---|
| 1 | Jaipur Jn. | JP | S4 T10/10A (window) | JP 240.83 (`AF`) | — | JP | JP | EXPLICIT |
| 2 | Kanakpura | KKU | S4 T10/10A | 249.77 | — | KKU | KKU | EXPLICIT |
| 3 | Bindayaka | BDYK | S4 T10/10A | 254.70 | — | BDYK | BDYK | EXPLICIT |
| 4 | Dhanakya | DNK | S4 T10/10A | 259.52 | — | **absent** | DNK | EXPLICIT (Step 13 incomplete) |
| 5 | Sheo Singhpura | SHNX | S4 T10/10A | 263.00 | — | **absent** | SHNX | EXPLICIT (Step 13 incomplete) |
| 6 | Bobas | BOBS | S4 T10/10A | 270.14 | — | **absent** | BOBS | EXPLICIT (Step 13 incomplete) |
| 7 | Asalpur Jobner | JOB | S4 T10/10A 277.61 | **277.83** | — | JOB | JOB | EXPLICIT; chainage CONFLICT D4 |
| 8 | Dhindha | DHND | S4 T10/10A | 283.05 | — | **absent** | DHND | EXPLICIT (Step 13 incomplete) |
| 9 | Hirnoda | HDA | S4 T10/10A | 287.25 | — | HDA | HDA | EXPLICIT |
| 10 | Phulera Jn. | FL | S4 T10/10A | `0.00`, `F.RE.215.12`, `F.AF.295.58` | — | FL (datum restart) | FL | EXPLICIT; three datums (§7.2) |
| 11 | Bhanwsa | BNWS | S4 PSR `FL-BNWS`; S11 PSR | 220.39 | — | label, unresolved | ON CORRIDOR | **EXPLICIT — CONSISTENT** |
| 12 | Naraina | NRI | S11 PSR `Bhanwsa-Naraina` (2021) | 225.28 | — | label, unresolved | UNRESOLVED | **EXPLICIT (2021) / current STRONGLY CORROBORATED** |
| — | Dantra 'H' | DTRA | none | 229.76 | — | label, unresolved | UNRESOLVED | **UNKNOWN** |
| — | Sakhun | SK | none | 237.24 | — | not recorded | not recorded | UNKNOWN (DFC "New Sakhun" name collision) |
| — | Sali 'H' | SALI | none | 244.09 | — | not recorded | not recorded | UNKNOWN |
| — | Gehlota | GLRA | none | 251.14 | — | not recorded | not recorded | UNKNOWN |
| — | Tiloniya 'H' | TL | none | 255.48 | — | not recorded | not recorded | UNKNOWN |
| — | Mandawariya | MNDV | none | 260.37 | — | not recorded | not recorded | UNKNOWN |
| — | Kishangarh | KSG | S11 SoW "EI at KSG" (2021) | 269.618 | — | assumed on corridor | UNKNOWN | **STRONGLY CORROBORATED** (position/neighbours UNKNOWN) |
| — | Gegal Akhri | GEK | none | 275.59 (also in IR/DFC inset) | — | not recorded | not recorded | UNKNOWN |
| — | Ladpura | LR | none | 281.242 | — | not recorded | not recorded | UNKNOWN |
| — | Madar Jn. | MDJN / MD | S4 T12/12A `MDJN` 288.37 | `MADAR(MD)288.37.0.00` | `MADAR(MDJN)` | MD/MDJN conflict | CONFLICT, MDJN preferred | EXPLICIT station; **code CONFLICT retained** |
| — | Ajmer Jn. | AII | S4 T12/12A 294.57 | `AJMER` (no code) | `AJMER(AII)` 294.57 | AII | AII | EXPLICIT |

No disagreement above has been silently reconciled.

---

## 10. BNWS / NRI / DTRA / KSG RESULTS

| Station | Evidence | Result |
|---|---|---|
| **BNWS** | S4 PDF 149 (2025, rendered): `FL-BNWS` / `BNWS-FL` under `JP-FL-MDJN`; S11 PDF 143 (2021): `Phulera Yard (FL-BNWS)`, `Bhanwsa-Naraina` | **ON ROUTE — EXPLICIT. CONSISTENT with Step 14.** Adjacent to FL (no block station between). |
| **NRI** | S11 PDF 143 (2021, rendered): `Bhanwsa-Naraina 224/3-224/8` under `JP-FL-MD (UP-TRAINS)` and `Naraina-Bhanwsa` under `MD-FL -JP(DOWN-TRAINS)`. Not in 2025 PSR (which lists only four PSRs). S1 2026 label `NARAINA (NRI) 225.28` on the RE chain. | **ON ROUTE — EXPLICIT for 2021; current membership STRONGLY CORROBORATED.** Adjacent to BNWS (2021). Upgraded from Step 14 UNRESOLVED. |
| **DTRA** | S1/S12 label only. No operating, CRS, construction or circular source names it. | **UNKNOWN** (unchanged). |
| **KSG** | (a) S11 Table 10/10A SoW "EI at KSG" alongside "PI between KKU-MDJN" — 2021, historical; (b) S15 NCR circular 29-09-2025: MADAR JN `MDJN` → KISHANGARH `KSG` → JAIPUR JN `JP`, "MDJN-JP-MDJN = JP DIV"; (c) S1 label `KISHANGARH(KSG) 269.618` on the RE chain. **Identity:** "Kishangarh Balawas" is explicitly on the **Rewari–Bhiwani** section (S14b p.15), and the S4 ART appendix "Kishangarh Balawas" therefore does not refer to KSG; DFC "New Kishangarh" is a separate DFC station (S16). | **STRONGLY CORROBORATED between MDJN and JP — NOT EXPLICIT.** No block-section statement names KSG; its neighbours and position are **UNKNOWN**. KSG must not be placed in a section list until a route table or block-section list names it. |

---

## 11. MADAR CODE RESULT

| Source | Type | Date | Code printed |
|---|---|---|---|
| S4 WTT 2025 | operating | 2025 | **MDJN** (station entry `MADAR Jn. MDJN (B) (III R)`; `AII-MDJN`, `JP-FL-MDJN`) |
| S15 NCR circular | operating/commercial | 29-09-2025 | **MDJN** (code column; "MDJN-JP-MDJN") |
| S5 Ajmer Line Capacity | operating | Jul-2025 | MDJN |
| S2 Ajmer Div. map | engineering map | 01-04-2026 | MDJN |
| S11 WTT 2021 | operating | 2021 | **both**: `MDJN` in Table 10 SoW ("KKU-MDJN", "MDJN-JP"), **`MD`** in PSR heading `JP-FL-MD` / `MD-FL -JP` |
| S1 / S12 NWR maps | engineering map | 2025 / 2026 | **MD** (`MADAR(MD)288.37.0.00`; route list `KANAKPURA-PHULERA-MADAR`, `MD-PUSHKAR`) |
| S3 Jaipur Div. map | engineering map | 2025 | MD (`RE-BKI-JP-MD`) |

**Correction to Step 14 §8.1:** Step 14 argued `MD` appears only in engineering maps. S11 shows an
**operating document** using `MD` (2021). That argument is withdrawn.

**Result: CONFLICT retained.** `MDJN` is **preferred for current operating-document context**
(S4 2025 and S15 2025 are the two most recent operating sources and both print `MDJN`), **but an
authoritative cross-source conflict remains** (current NWR HQ map 2026 prints `MD`). No
authoritative station-code master was located. Production station data is **not** changed.

---

## 12. DFC SEPARATION

- **S16 (DFCCIL, 2020):** WDFC stations under CGM Jaipur include **New Phulera Jn**, **New Sakhun**,
  **New Kishangarh**; under CGM Ajmer **New Saradhana**, **New Bangurgram Jn** … — distinct DFC
  station identities.
- **S9/S10 (PIB 2019/2021, Step 14):** Rewari–Madar WDFC dedicated 07-01-2021; exclusive freight,
  2×25 kV, separate alignment.
- **S1/S12:** a separate panel titled **"SCHEMATIC JUNCTION ARRANGMENT FOR IR & DFC"** with lines
  labelled `IR TRACK` and `DFC TRACK`, and stations `NEW PHULERA`-type names (`NEW ATELI`,
  `NEW PACHAR MALIKPUR`, `NEW KISHANGARH`, `NEW BANGURGRAM`, `NEW MARWAR JN.`, `NEW PALANPUR JN.`).
- **Result: DFC is separate infrastructure and is NOT an IR running line — EXPLICIT.** No DFC
  track is counted anywhere in this report.
- **Name collisions recorded as a hazard:** IR `SAKHUN (SK)` vs DFC `New Sakhun`; IR `SARADHNA (SDH)`
  vs DFC `New Saradhana`; IR `KSG` vs DFC `New Kishangarh`; IR `FL` vs DFC `New Phulera Jn`.
- **Interface geometry at Phulera / Kishangarh / Madar: UNKNOWN.** The schematic is not a
  dimensioned interface schedule and its Phulera annotation (`F.RE.515.12`) conflicts with the main
  map body (D3).

---

## 13. PSR / SECONDARY EVIDENCE ASSESSMENT

| Item | Step 14 status | Step 15 rendered inspection | Use |
|---|---|---|---|
| S4 PSR appendix UP/DN kilometrages (PDF 149) | REJECTED (scrambled) | Now legible: UP `JP-KKU 241/101–242/9`, DN `KKU-JP 242/10–241/110`; UP `BOBS-JOB 275/11–276/03`, DN `JOB-BOBS 276/2–275/10`; UP `FL-BNWS 215/21–216/1`, DN `BNWS-FL 216/2–215/30`; yard lines JP Line No.2 (UP) / Line No.1 (DN Main) | **Used for block-section names and datum only.** Different UP/DN restriction limits do **not** establish two running lines — REJECTED as line-count evidence. |
| S11 PSR (PDF 143) | — | Legible; `Bhanwsa-Naraina` | Block-section adjacency (historical) and RE datum. Not line count. |
| S4 Sectional Distance (PDF 178) | MEDIUM (scrambled) | Legible | Distances and datum arithmetic. Source caveat: not for technical use. |
| Ajmer Div. line capacity "AII–MDJN 81" (S5) | not used | not re-rendered | **REJECTED** as line-count evidence; redundant (AII–MDJN is EXPLICIT double). |
| NWR Route-KM / Track-KM ratio (S6) | not used | division granularity only | **REJECTED.** |
| IBS UP 246.2 / IBS DN 247 (S11) | — | legible | **Not used** — suggestive of directional working but not a track-count statement. |
| Engineering LC / Bridge lists | — | rendered: division-total summaries only | No section information. Not used. |

---

## 14. CLAIM MATRIX

| Claim | Best Evidence | Exact Page | Current? | Result | Evidence Class |
|---|---|---:|---|---|---|
| 1. Complete AII–RE station sequence | S4 T10 (FL–JP window); S4 T12 (MDJN–AII); S4/S11 PSR (FL–BNWS–NRI) | S4 65, 111, 149; S11 58, 143 | Partly | **NOT RECOVERED** — gap NRI→MDJN | PARTIAL |
| 2. Complete RE–AII station sequence | S4 T10A; T12A; PSR | S4 73, 112, 149; S11 66, 143 | Partly | **NOT RECOVERED** — same gap | PARTIAL |
| 3. FL–JP running-line configuration | S4 T10/10A SoW | S4 65, 73 | Yes (2025) | FL–HDA = **2**; HDA–JP **UNKNOWN** (ABS); 2021 "Double line" historical only | PARTIAL |
| 4. FL–MDJN running-line configuration | none current; S11 SoW scope ambiguous | S11 58, 66 | No | **UNKNOWN** | UNKNOWN (2021: AMBIGUOUS) |
| 5. AII–MDJN running-line configuration | S4 T11/11A/12/12A `{AII-MDJN} Double Line` | S4 81–112 (rendered 81, 112) | Yes | **2** — CONSISTENT with Step 14 | EXPLICIT |
| 6. BNWS membership | S4 PSR `FL-BNWS`; S11 PSR | S4 149; S11 143 | Yes | ON ROUTE, adjacent to FL — CONSISTENT | EXPLICIT |
| 7. NRI membership | S11 PSR `Bhanwsa-Naraina`; S1 label | S11 143 | 2021 (+2026 map) | ON ROUTE, adjacent to BNWS | EXPLICIT (historical) / STRONGLY CORROBORATED (current) |
| 8. DTRA membership | S1 label only | S1 | — | **UNKNOWN** | UNKNOWN |
| 9. KSG membership | S11 "EI at KSG"; S15 stop order; S1 label | S11 58, 66; S15 p.1 | 2021 + 2025 | Between MDJN and JP; position/neighbours unknown | STRONGLY CORROBORATED |
| 10. Madar code | S4 & S15 `MDJN` vs S1 `MD`; S11 both | S4 112; S15 p.1; S11 58/143 | Both sides current | MDJN preferred in operating context; conflict retained | CONFLICT |
| 11. Phulera datum relationship | S4 "Distance From AF"; S1 `F.RE.215.12,F.AF.295.58`; S4 66.73+148.39=215.12 | S4 65, 178; S1 | Yes | Route-datum change at FL: AF 295.58 / RE 215.12 / local 0.00 — **not one scale** | EXPLICIT (residual D1, D2, D3 CONFLICT) |
| 12. Madar datum | S4 T12A "Distance From RE Via Chord" 288.37; S1 `288.37.0.00` | S4 111, 112; S1 | Yes | 288.37 on RE chain EXPLICIT; 0.00 branch origin PARTIAL | EXPLICIT / PARTIAL |
| 13. DFC separation | S16; S9/S10; S1 IR/DFC schematic | S16 p.1; S1 inset | 2020–2026 | Separate; not IR running line; interface geometry UNKNOWN | EXPLICIT (separation) / UNKNOWN (interfaces) |
| 14. Current commissioning status of relevant doubling | S14a–d (none listed); CRS rows are electrification | S14a p.1; S13a PDF 66; S13b printed 58–59 | Yes | **No doubling sanction/WIP/commissioning found**; CRS rows = electrification only | UNKNOWN |

---

## 15. REMAINING UNKNOWN / CONFLICT ITEMS

**UNKNOWN — running lines (current):** JP–KKU, KKU–BDYK, BDYK–DNK, DNK–SHNX, SHNX–BOBS,
BOBS–JOB, JOB–DHND, DHND–HDA (8); FL–BNWS; BNWS–NRI; every section NRI→MDJN (count unknown).

**UNKNOWN — topology:** station set, order and block boundaries NRI→MDJN; DTRA, SK, SALI, GLRA,
TL, MNDV, GEK, LR membership; KSG position and neighbours; IR/DFC interface geometry; commissioning
history of any doubling on JP–FL–AII; whether S4 is the edition in force on 2026-09-13; line
identity (UP/DN) anywhere — **not invented**.

**CONFLICT:** C1 Madar code MD/MDJN · D1 FL–AII 79.87 vs 79.45 · D2 JP–FL datum label AF (2025) vs
RE (Chord) (2021) · D3 S1 `F.RE.215.12` vs inset `515.12` · D4 JOB 277.83 vs 277.61 · undecoded class
markers differing between S4 DN and UP tables (SHNX, BDYK, FL).

**HISTORICAL — never to be upgraded to current:** S11 "Double line Lock & Block" (2021); S11 NRI
adjacency (2021, corroborated only); CRS electrification 2019; DFC trial run 2019.

---

## 16. EVIDENCE REQUIRED TO CLOSE THE REMAINING GAPS

| Gap | Evidence that closes it |
|---|---|
| NRI→MDJN station set, KSG position, DTRA and other candidates | **Jaipur Division WTT, full Table 10/10A "AII-RE"/"RE-AII"** (station column AII…JP) — or the Jaipur Division block-section register |
| FL→MDJN running lines | Same Jaipur Division WTT SoW header for the FL–MDJN window; or SWRs for FL, KSG, MDJN; or a CRS doubling authorisation naming the sections |
| HDA–JP running lines | Current SoW/SWR explicitly stating single/double line for HDA–JP; or CRS doubling authorisation |
| Madar code | Authoritative current station-code master (Railway Board / CRIS) |
| D1 distance residual | Engineering chainage register stating the "RE via Chord" path |
| S4 currency | S4 cover/notice page with w.e.f., or WTT 2026 |
| IR/DFC interfaces | DFCCIL interface schedule / connection drawings at Phulera, Kishangarh, Madar |
| CRS completeness | Text-searchable CRS annual reports 2020-21, 2022-23, 2023-24, and pre-2014 reports |

These documents are not publicly posted; obtaining them is an RTI / official-request matter
outside this step.

---

## 17. FINAL TOPOLOGY EVIDENCE GATE

# AMBER

**Not GREEN:** no complete authoritative route table; current running-line counts exist for only
two sections; the NRI→MDJN station set is unknown; HDA–JP and FL→MDJN running lines have no current
explicit statement.

**Not RED:** current authoritative operating evidence exists and is internally consistent for
JP–FL topology, FL–BNWS adjacency, AII–MDJN and FL–HDA double line, and both route datums; NRI and
KSG placement advanced; no contradiction undermines the two established running-line counts.

---

## 18. REAL_STATIC INGESTION ELIGIBILITY

**NOT ELIGIBLE.** `running_lines_per_section` stays **unpopulated**.
`pashupatastra_realistic_dataset.json` stays **SYNTHETIC**.

Implications recorded for a later ingestion step (no Section IDs created here):
- Only **consecutive authoritative stations** may become candidate sections. Today that is the nine
  JP–FL block sections, FL–BNWS, and MDJN–AII; BNWS–NRI only with a historical-source flag.
- Section identity must be **direction-independent**; the canonical resource identity remains
  **track_id + section_id**. UP-1/DOWN-1 must **not** be invented.
- Chainages must carry their **datum** (AF / RE-via-chord / local) and must never be merged across FL.
- DFC stations and geometry must never enter IR topology.
- Step 14's narrowly scoped JP–FL geometry option (stations, block boundaries, distances; running lines
  UNKNOWN except FL–HDA) remains the only candidate, and remains a later step's decision.

---

## 19. EXACT REMAINING BLOCKERS

1. Complete Jaipur Division AII–RE / RE–AII route table not publicly available.
2. Station set and block boundaries NRI → MDJN unknown (DTRA, KSG position, seven map candidates).
3. No current running-line statement for HDA–JP (8 sections) — ABS is not track-count-bearing.
4. No current running-line statement for any FL → MDJN section.
5. No doubling sanction/WIP/commissioning record for the corridor (CRS rows found are electrification).
6. Madar code conflict MD / MDJN.
7. Distance/datum residuals D1–D4.
8. S4 effective date unverifiable.
9. IR/DFC interface geometry unknown.

---

## APPENDIX A — REPOSITORY INTEGRITY

Baseline captured at the start of Step 15 (MD5, identical to Step 14 Appendix A):

| File | MD5 |
|---|---|
| `pashupatastra_realistic_dataset.json` | `5725c611afb1639d2a40bfc6d267a907` |
| `jobs.db` | `7a9d02c2ce37c57a7bd69b69483373fe` |
| `FINAL_BACKEND_SYSTEM_AUDIT.md` | `2805ec9fdbb1646e89664483e59e066a` |
| `PASHUPATASTRA_FINAL_DEMO_PLAYBOOK.md` | `9eb408bf7e4a27deca4f2db36eca17c1` |
| `SIH2026-IDEA-Presentation-Format.pdf` | `cb4ea2e2fac7f256a0b92086e1af549f` |
| `STEP13_RUNNING_LINE_VECTOR_ANALYSIS.md` | `71fbd35cd1983d7668cb5ae727f1d173` |
| `STEP14_AUTHORITATIVE_SOURCE_AUDIT.md` | `aea40f7450b94e6e0f9d3c2e7254badf` |

`stash@{0}` = `3e657d7c6096ea2a13044d4100682e9f2f369e0d`; HEAD = `d9f0264`. The working tree
already contained the pre-existing `M`/`??` entries listed in the session git snapshot; "only the
Step 15 report changed" is measured against that baseline. Post-write verification is in the
accompanying response.
