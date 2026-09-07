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