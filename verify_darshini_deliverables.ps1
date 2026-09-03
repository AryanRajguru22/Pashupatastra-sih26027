# PowerShell verification script for Darshini deliverables
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "PASHUPATASTRA -- DARSHINI DELIVERABLES VERIFICATION" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan

$script:passed = 0
$script:total = 0

function Assert-Check {
    param(
        [string]$Description,
        [bool]$Condition
    )
    $script:total++
    if ($Condition) {
        $script:passed++
        Write-Host "[PASS] $Description" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] $Description" -ForegroundColor Red
    }
}

# 1. Check DOMAIN_SPECIFICATION.md
$docPath = "c:\Users\Hp\Documents\phasupatashtra\DOMAIN_SPECIFICATION.md"
Assert-Check -Description "DOMAIN_SPECIFICATION.md exists" -Condition (Test-Path $docPath)

$docContent = Get-Content $docPath -Raw
Assert-Check -Description "Domain doc has Section 1 (Definitions)" -Condition ($docContent.Contains("SECTION 1: Railway Domain Definitions"))
Assert-Check -Description "Domain doc defines Multi-Asset Topology" -Condition ($docContent.Contains("Can a single track host multiple assets? YES"))
Assert-Check -Description "Domain doc has Section 2 (7-Feature Scoring Mapping)" -Condition ($docContent.Contains("SECTION 2: 7-Feature ML Scoring Domain Data Mapping"))
Assert-Check -Description "Domain doc defines 5 Disruption Scenarios" -Condition ($docContent.Contains("SECTION 4: 5 Standardized Disruption Scenarios"))
Assert-Check -Description "Domain doc defines Golden Scenario" -Condition ($docContent.Contains("SECTION 5: The Golden Scenario Master Blueprint"))

# 2. Check Models & Feature Adapter
$modelsPath = "c:\Users\Hp\Documents\phasupatashtra\backend\app\data\models.py"
Assert-Check -Description "models.py exists" -Condition (Test-Path $modelsPath)

$adapterPath = "c:\Users\Hp\Documents\phasupatashtra\backend\app\data\feature_adapter.py"
Assert-Check -Description "feature_adapter.py exists" -Condition (Test-Path $adapterPath)

# 3. Check JSON Fixtures
$goldenPath = "c:\Users\Hp\Documents\phasupatashtra\backend\app\data\fixtures\golden_scenario.json"
Assert-Check -Description "golden_scenario.json exists" -Condition (Test-Path $goldenPath)

$goldenJson = Get-Content $goldenPath -Raw | ConvertFrom-Json
Assert-Check -Description "Golden scenario has 12 candidates" -Condition ($goldenJson.initial_request.candidates.Count -eq 12)
Assert-Check -Description "Golden scenario has 2 tracks" -Condition ($goldenJson.initial_request.tracks.Count -eq 2)
Assert-Check -Description "Golden scenario has 6 possession windows" -Condition ($goldenJson.initial_request.possession_windows.Count -eq 6)
Assert-Check -Description "Golden scenario has committed locks" -Condition ($goldenJson.committed_locks.Count -ge 2)
Assert-Check -Description "Golden scenario has disruption event" -Condition ($goldenJson.disruption_event.disruption_type -eq "TRACK_UNAVAILABLE")

# Check candidate scoring features in Golden Scenario
$firstCandidate = $goldenJson.initial_request.candidates[0]
$sf = $firstCandidate.metadata.scoring_features
Assert-Check -Description "Candidate metadata contains scoring_features" -Condition ($null -ne $sf)
Assert-Check -Description "Candidate has asset_criticality" -Condition ($null -ne $sf.asset_criticality)
Assert-Check -Description "Candidate has defect_severity" -Condition ($null -ne $sf.defect_severity)
Assert-Check -Description "Candidate has days_overdue" -Condition ($null -ne $sf.days_overdue)
Assert-Check -Description "Candidate has failure_probability" -Condition ($null -ne $sf.failure_probability)
Assert-Check -Description "Candidate has train_impact" -Condition ($null -ne $sf.train_impact)
Assert-Check -Description "Candidate has maintenance_duration" -Condition ($null -ne $sf.maintenance_duration)
Assert-Check -Description "Candidate has historical_failure_rate" -Condition ($null -ne $sf.historical_failure_rate)

# 4. Check Disruption Scenarios Fixture
$disrPath = "c:\Users\Hp\Documents\phasupatashtra\backend\app\data\fixtures\disruption_scenarios.json"
Assert-Check -Description "disruption_scenarios.json exists" -Condition (Test-Path $disrPath)

$disrJson = Get-Content $disrPath -Raw | ConvertFrom-Json
Assert-Check -Description "disruption_scenarios has 5 scenarios" -Condition ($disrJson.scenarios.Count -eq 5)

$disrTypes = @($disrJson.scenarios | ForEach-Object { $_.disruption_event.disruption_type })
Assert-Check -Description "Scenario 1 is TRACK_UNAVAILABLE" -Condition ($disrTypes -contains "TRACK_UNAVAILABLE")
Assert-Check -Description "Scenario 2 is EMERGENCY_WORK" -Condition ($disrTypes -contains "EMERGENCY_WORK")
Assert-Check -Description "Scenario 3 is POSSESSION_CURTAILMENT" -Condition ($disrTypes -contains "POSSESSION_CURTAILMENT")
Assert-Check -Description "Scenario 4 is ASSET_CONDITION_DETERIORATION" -Condition ($disrTypes -contains "ASSET_CONDITION_DETERIORATION")
Assert-Check -Description "Scenario 5 is INFEASIBLE_SCENARIO" -Condition ($disrTypes -contains "INFEASIBLE_SCENARIO")

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "VERIFICATION RESULT: $script:passed / $script:total Checks Passed" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
