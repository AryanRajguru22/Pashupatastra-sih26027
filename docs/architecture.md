# System Architecture — Pashupatastra

Pashupatastra is an AI-assisted railway maintenance scheduling and disruption recovery system designed for Indian Railways.

```
+-------------------------------------------------------------+
|                 RAILWAY DOMAIN & SYNTHETIC DATA             |
|                       (Darshini)                            |
|        - Corridor Topology & Asset Hierarchy                |
|        - Deterministic Synthetic Scenario Generator         |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|                   AI / ML PRIORITY & RISK SCORER            |
|                          (Ayush)                            |
|        - Asset Criticality & Defect Urgency Scoring         |
|        - Explainable Priority and Risk Metric Attribution   |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|                    CP-SAT OPTIMIZER CORE                    |
|                          (Tyagi)                            |
|        - No-overlap, Headway, Precedence Constraints        |
|        - Committed-Block Stability & Re-optimization        |
|        - Infeasibility Detection & Reason Codes             |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|                  FASTAPI BACKEND & ORCHESTRATION            |
|                          (Aryan)                            |
|        - POST /optimize, GET /health, POST /disrupt         |
|        - Contract Validation & Pipeline Integration         |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|                FRONTEND OPERATIONS CONTROL VIEW             |
|                         (Archit)                            |
|        - Railway Corridor Timeline (Time x Tracks)          |
|        - Plan -> Disrupt -> Recover Visualizer             |
+-------------------------------------------------------------+
                              |
                     ... DISRUPTION ...
                              |
                              v
+-------------------------------------------------------------+
|               DISRUPTION & RECOVERY SIMULATOR               |
|                         (Tirth)                             |
|        - Deterministic Disruption Scenarios                 |
|        - Mutated Optimization Request Generator             |
+-------------------------------------------------------------+
```

---

## The 4-Step Demo Lifecycle

1. **PLAN:** Load corridor candidates $\to$ ML Scorer calculates Priority/Risk $\to$ CP-SAT produces optimal baseline schedule $\to$ Dashboard renders timeline.
2. **DISRUPT:** Inject disruption (e.g. `TRACK_UNAVAILABLE` on UP-1 from 02:00 to 05:00) $\to$ Simulator identifies affected vs. committed blocks.
3. **RECOVER:** Mutated request re-submitted to CP-SAT with committed blocks pinned $\to$ CP-SAT re-routes remaining blocks to alternative tracks/times.
4. **VERIFY:** Confirm zero hard-constraint violations, zero overlap, all precedence dependencies intact, and before/after recovery metrics clearly displayed.
