from backend.app.data.train_adapter import (
    generate_possession_windows_from_trains,
    normalize_train,
    occupied_train_windows,
)


def test_normalize_train_with_delay():
    train = normalize_train(
        {
            "train_no": "12951",
            "track_id": "UP-1",
            "scheduled_start_minute": 120,
            "scheduled_end_minute": 150,
            "delay_minutes": 20,
        }
    )

    assert train.effective_start_minute == 140
    assert train.effective_end_minute == 170


def test_live_times_override_scheduled_plus_delay():
    train = normalize_train(
        {
            "train_no": "12952",
            "track_id": "UP-1",
            "scheduled_start_minute": 120,
            "scheduled_end_minute": 150,
            "delay_minutes": 30,
            "live_start_minute": 135,
            "live_end_minute": 165,
        }
    )

    assert train.effective_start_minute == 135
    assert train.effective_end_minute == 165


def test_safety_buffer_creates_occupied_interval():
    occupied = occupied_train_windows(
        [
            {
                "train_no": "12001",
                "track_id": "UP-1",
                "scheduled_start_minute": 100,
                "scheduled_end_minute": 160,
            }
        ],
        safety_buffer_minutes=10,
    )

    assert occupied["UP-1"] == [(90, 170)]


def test_possession_window_is_the_gap_between_trains():
    windows = generate_possession_windows_from_trains(
        [
            {
                "train_no": "12001",
                "track_id": "UP-1",
                "scheduled_start_minute": 100,
                "scheduled_end_minute": 160,
            },
            {
                "train_no": "12002",
                "track_id": "UP-1",
                "scheduled_start_minute": 300,
                "scheduled_end_minute": 340,
            },
        ],
        horizon_minutes=480,
        safety_buffer_minutes=10,
        minimum_window_minutes=30,
    )

    assert len(windows) == 3

    assert (windows[0].track_id, windows[0].start_minute, windows[0].end_minute) == (
        "UP-1",
        0,
        90,
    )

    assert (windows[1].track_id, windows[1].start_minute, windows[1].end_minute) == (
        "UP-1",
        170,
        290,
    )

    assert (windows[2].track_id, windows[2].start_minute, windows[2].end_minute) == (
    "UP-1",
    350,
    480,
    )


def test_delay_changes_possession_window():
    on_time = generate_possession_windows_from_trains(
        [
            {
                "train_no": "12001",
                "track_id": "UP-1",
                "scheduled_start_minute": 100,
                "scheduled_end_minute": 160,
            }
        ],
        horizon_minutes=300,
        safety_buffer_minutes=10,
    )

    delayed = generate_possession_windows_from_trains(
        [
            {
                "train_no": "12001",
                "track_id": "UP-1",
                "scheduled_start_minute": 100,
                "scheduled_end_minute": 160,
                "delay_minutes": 30,
            }
        ],
        horizon_minutes=300,
        safety_buffer_minutes=10,
    )

    assert on_time[1].start_minute == 170
    assert delayed[1].start_minute == 200



import json
from pathlib import Path


REAL_DATASET = Path("pashupatastra_realistic_dataset.json")


def test_real_project_dataset_generates_possession_windows():
    with REAL_DATASET.open("r", encoding="utf-8") as f:
        data = json.load(f)

    trains = data["trains"]

    assert len(trains) == 24

    windows = generate_possession_windows_from_trains(
        trains,
        horizon_minutes=1440,
        safety_buffer_minutes=10,
        minimum_window_minutes=30,
    )

    assert windows
    assert {window.track_id for window in windows} == {"UP-1", "DOWN-1"}

    for window in windows:
        assert window.start_minute < window.end_minute
        assert 0 <= window.start_minute < 1440
        assert 0 < window.end_minute <= 1440
        assert window.window_type == "TRAIN_GAP"


def test_section_qualified_track_id():
    from backend.app.data.train_adapter import section_track_id

    assert section_track_id("UP-1", 7.0, 32.0) == "UP-1:7-32"


def test_real_dataset_generates_section_qualified_windows():
    import json

    from backend.app.data.train_adapter import (
        generate_section_possession_windows_from_trains,
    )

    with open(
        "pashupatastra_realistic_dataset.json",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    windows = generate_section_possession_windows_from_trains(
        data["trains"],
        data["corridor"]["stations"],
        horizon_minutes=1440,
        safety_buffer_minutes=10,
        minimum_window_minutes=20,
    )

    assert windows

    track_ids = {window.track_id for window in windows}

    assert any(track.startswith("UP-1:") for track in track_ids)
    assert any(track.startswith("DOWN-1:") for track in track_ids)

    for window in windows:
        assert ":" in window.track_id
        assert "-" in window.track_id.split(":", 1)[1]
        assert window.start_minute < window.end_minute


def test_maintenance_jobs_map_to_sections():
    import json

    from backend.app.data.train_adapter import (
        qualified_track_for_job,
    )

    with open(
        "pashupatastra_realistic_dataset.json",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    stations = data["corridor"]["stations"]

    qualified_ids = [
        qualified_track_for_job(job, stations)
        for job in data["maintenance_jobs"]
    ]

    assert len(qualified_ids) == 15
    assert all(":" in track_id for track_id in qualified_ids)

    assert (
        qualified_ids[2]
        == "UP-1:7-32"
    )  # BLK-103 at ~24 km