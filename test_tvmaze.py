from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

# Define dummy tmdb_get before importing main
with patch('main.TMDB_API_KEY', 'dummy_key'):
    import main

def test_tvmaze_mapping():
    print("Testing Ted Lasso mapping (should be streaming-only, so is_network=False)...")
    with patch('main.tmdb_get') as mock_tmdb_get:
        mock_tmdb_get.return_value = {"imdb_id": "tt10986410"}
        mapping = main.get_tvmaze_episode_mapping(97546, "Ted Lasso")
        
        print(f"Ted Lasso episodes fetched: {len(mapping)}")
        if mapping:
            sample_key = list(mapping.keys())[0]
            sample_val = mapping[sample_key]
            print(f"Sample episode {sample_key}: {sample_val}")
            assert sample_val["is_network"] is False, "Ted Lasso should NOT be a network show"
            assert "web_channel" in sample_val, "web_channel should be present in mapping"
            print("✓ Ted Lasso test passed!")
        else:
            print("✗ Ted Lasso returned no episodes")

    print("\nTesting Saturday Night Live mapping (should be network, so is_network=True)...")
    with patch('main.tmdb_get') as mock_tmdb_get:
        mock_tmdb_get.return_value = {"imdb_id": "tt0072562"}
        mapping = main.get_tvmaze_episode_mapping(1667, "Saturday Night Live")
        
        print(f"SNL episodes fetched: {len(mapping)}")
        if mapping:
            sample_key = list(mapping.keys())[0]
            sample_val = mapping[sample_key]
            print(f"Sample episode {sample_key}: {sample_val}")
            assert sample_val["is_network"] is True, "SNL should be a network show"
            assert sample_val["airstamp"] is not None, "SNL should have an airstamp"
            assert sample_val["airtime"] is not None, "SNL should have an airtime"
            print("✓ SNL test passed!")
        else:
            print("✗ SNL returned no episodes")

    print("\nTesting fallback to single search by name (when TMDB external IDs lookup fails/empty)...")
    with patch('main.tmdb_get') as mock_tmdb_get:
        mock_tmdb_get.side_effect = Exception("TMDB down")
        mapping = main.get_tvmaze_episode_mapping(60625, "Rick and Morty")
        
        print(f"Rick and Morty episodes fetched: {len(mapping)}")
        if mapping:
            sample_key = list(mapping.keys())[0]
            sample_val = mapping[sample_key]
            print(f"Sample episode {sample_key}: {sample_val}")
            assert sample_val["is_network"] is True, "Rick and Morty should be a network show"
            print("✓ Rick and Morty fallback test passed!")
        else:
            print("✗ Rick and Morty returned no episodes")


def test_is_between_12am_and_9am():
    print("\nTesting is_between_12am_and_9am helper...")
    tz = main.CALENDAR_TZ
    d = date(2026, 8, 4)

    # 12:00 AM (00:00) -> should be True
    dt_12am = datetime.combine(d, main.dt_time(0, 0), tzinfo=tz)
    assert main.is_between_12am_and_9am(dt_12am) is True, "12:00 AM should be in 12am-9am window"

    # 2:00 AM -> should be True
    dt_2am = datetime.combine(d, main.dt_time(2, 0), tzinfo=tz)
    assert main.is_between_12am_and_9am(dt_2am) is True, "2:00 AM should be in 12am-9am window"

    # 7:00 AM -> should be True
    dt_7am = datetime.combine(d, main.dt_time(7, 0), tzinfo=tz)
    assert main.is_between_12am_and_9am(dt_7am) is True, "7:00 AM should be in 12am-9am window"

    # 9:00 AM -> should be True (inclusive)
    dt_9am = datetime.combine(d, main.dt_time(9, 0), tzinfo=tz)
    assert main.is_between_12am_and_9am(dt_9am) is True, "9:00 AM should be in 12am-9am window"

    # 9:01 AM -> should be False
    dt_901am = datetime.combine(d, main.dt_time(9, 1), tzinfo=tz)
    assert main.is_between_12am_and_9am(dt_901am) is False, "9:01 AM should NOT be in 12am-9am window"

    # 12:00 PM (noon) -> should be False
    dt_12pm = datetime.combine(d, main.dt_time(12, 0), tzinfo=tz)
    assert main.is_between_12am_and_9am(dt_12pm) is False, "12:00 PM should NOT be in 12am-9am window"

    # 8:00 PM (20:00) -> should be False
    dt_8pm = datetime.combine(d, main.dt_time(20, 0), tzinfo=tz)
    assert main.is_between_12am_and_9am(dt_8pm) is False, "8:00 PM should NOT be in 12am-9am window"

    # 11:59 PM (23:59) -> should be False
    dt_1159pm = datetime.combine(d, main.dt_time(23, 59), tzinfo=tz)
    assert main.is_between_12am_and_9am(dt_1159pm) is False, "11:59 PM should NOT be in 12am-9am window"

    print("✓ is_between_12am_and_9am test passed!")


def test_ted_lasso_apple_tv_drop_time():
    print("\nTesting Ted Lasso Apple TV+ drop time resolution (8pm Central Tuesday)...")
    ep = {
        "show_id": 97546,
        "show_name": "Ted Lasso",
        "season_number": 4,
        "episode_number": 1,
        "episode_name": "Pilot 4",
        "air_date": "2026-08-04",  # Tuesday
        "overview": "Ted returns",
        "watch_link": "https://tv.apple.com/us/show/ted-lasso/123",
        "provider": "Apple TV",
        "web_channel": "Apple TV",
        "drop_time": None,
        "airstamp": "2026-08-05T12:00:00+00:00",
        "airtime": "",
        "runtime": 43,
        "is_network": False,
    }
    date_obj = date(2026, 8, 4)  # Tuesday
    dt_start, dt_end, use_time_block = main.resolve_episode_time(ep, date_obj)

    assert use_time_block is True, "Ted Lasso should be a timed event"
    dt_start_central = dt_start.astimezone(main.CALENDAR_TZ)
    dt_end_central = dt_end.astimezone(main.CALENDAR_TZ)

    assert dt_start_central.strftime("%A") == "Tuesday", f"Expected Tuesday, got {dt_start_central.strftime('%A')}"
    assert dt_start_central.hour == 20, f"Expected 8pm (20:00), got {dt_start_central.hour}"
    assert dt_start_central.minute == 0, f"Expected minute 0, got {dt_start_central.minute}"
    assert dt_end_central - dt_start_central == timedelta(minutes=43), "Expected 43 minute runtime"
    print(f"✓ Ted Lasso resolves to {dt_start_central.strftime('%A, %B %d, %Y at %I:%M %p %Z')}")


def test_early_morning_caveat_all_day_events():
    print("\nTesting early morning caveat (12am-9am -> all-day event)...")
    date_obj = date(2026, 9, 30)

    # 1. Episode with airstamp at 2:00 AM Central (e.g. 07:00 UTC during CDT)
    ep_2am = {
        "show_id": 100,
        "show_name": "Late Night Streaming",
        "season_number": 1,
        "episode_number": 1,
        "air_date": "2026-09-30",
        "airtime": "02:00",
        "airstamp": "2026-09-30T07:00:00+00:00",  # 02:00 CDT
        "runtime": 60,
        "is_network": False,
        "provider": "Netflix",
        "web_channel": "Netflix",
    }
    _, _, use_time_block = main.resolve_episode_time(ep_2am, date_obj)
    assert use_time_block is False, "2:00 AM release should be an all-day event"

    # 2. Episode with default TVmaze airstamp at 12:00 UTC (7:00 AM CDT)
    ep_7am = {
        "show_id": 101,
        "show_name": "Generic Streamer",
        "season_number": 1,
        "episode_number": 1,
        "air_date": "2026-09-30",
        "airtime": "",
        "airstamp": "2026-09-30T12:00:00+00:00",  # 07:00 CDT
        "runtime": 30,
        "is_network": False,
        "provider": "Disney+",
        "web_channel": "Disney+",
    }
    _, _, use_time_block = main.resolve_episode_time(ep_7am, date_obj)
    assert use_time_block is False, "7:00 AM release should be an all-day event"

    # 3. Episode airing at 9:00 AM Central (09:00)
    ep_9am = {
        "show_id": 102,
        "show_name": "Morning Show",
        "season_number": 1,
        "episode_number": 1,
        "air_date": "2026-09-30",
        "airtime": "09:00",
        "airstamp": "2026-09-30T14:00:00+00:00",  # 09:00 CDT
        "runtime": 30,
        "is_network": True,
        "provider": "Network",
    }
    _, _, use_time_block = main.resolve_episode_time(ep_9am, date_obj)
    assert use_time_block is False, "9:00 AM release should be an all-day event"

    # 4. Episode airing at 9:30 AM Central (09:30) -> should be a timed event
    ep_930am = {
        "show_id": 103,
        "show_name": "Mid-Morning Show",
        "season_number": 1,
        "episode_number": 1,
        "air_date": "2026-09-30",
        "airtime": "09:30",
        "airstamp": "2026-09-30T14:30:00+00:00",  # 09:30 CDT
        "runtime": 30,
        "is_network": True,
        "provider": "Network",
    }
    dt_start, _, use_time_block = main.resolve_episode_time(ep_930am, date_obj)
    assert use_time_block is True, "9:30 AM release should be a timed event"
    assert dt_start.astimezone(main.CALENDAR_TZ).hour == 9
    assert dt_start.astimezone(main.CALENDAR_TZ).minute == 30

    print("✓ Early morning caveat tests passed!")


def test_network_show_time_resolution():
    print("\nTesting network show time resolution (SNL at 10:29pm Central)...")
    date_obj = date(2026, 12, 19)
    # SNL airs Dec 19 at 11:29pm ET / 10:29pm Central (Dec 20 04:29 UTC)
    ep_snl = {
        "show_id": 1667,
        "show_name": "Saturday Night Live",
        "season_number": 52,
        "episode_number": 9,
        "air_date": "2026-12-19",
        "airtime": "23:29",
        "airstamp": "2026-12-20T04:29:00+00:00",
        "runtime": 90,
        "is_network": True,
        "provider": "Prime Video",
    }
    dt_start, dt_end, use_time_block = main.resolve_episode_time(ep_snl, date_obj)
    assert use_time_block is True, "SNL should be a timed event"
    dt_central = dt_start.astimezone(main.CALENDAR_TZ)
    assert dt_central.hour == 22 and dt_central.minute == 29, f"Expected 22:29 Central, got {dt_central.strftime('%H:%M')}"
    print(f"✓ SNL resolves to {dt_central.strftime('%A, %B %d, %Y at %I:%M %p %Z')}")


def test_custom_drop_time():
    print("\nTesting custom drop_time from shows.json...")
    date_obj = date(2026, 10, 1)

    # 1. Evening custom drop time (e.g. 19:30)
    ep_evening = {
        "show_id": 200,
        "show_name": "Custom Evening Show",
        "season_number": 1,
        "episode_number": 1,
        "air_date": "2026-10-01",
        "drop_time": "19:30",
        "runtime": 45,
    }
    dt_start, _, use_time_block = main.resolve_episode_time(ep_evening, date_obj)
    assert use_time_block is True
    dt_central = dt_start.astimezone(main.CALENDAR_TZ)
    assert dt_central.hour == 19 and dt_central.minute == 30

    # 2. Overnight custom drop time (e.g. 03:00) -> caveat converts to all day
    ep_night = {
        "show_id": 201,
        "show_name": "Custom Night Show",
        "season_number": 1,
        "episode_number": 1,
        "air_date": "2026-10-01",
        "drop_time": "03:00",
        "runtime": 45,
    }
    _, _, use_time_block = main.resolve_episode_time(ep_night, date_obj)
    assert use_time_block is False, "03:00 drop_time should be treated as all-day event"

    print("✓ Custom drop_time tests passed!")


def test_calendar_event_generation():
    print("\nTesting build_calendar integration...")
    episodes = [
        {
            "show_id": 97546,
            "show_name": "Ted Lasso",
            "season_number": 4,
            "episode_number": 1,
            "episode_name": "Premiere",
            "air_date": "2026-08-04",
            "overview": "Ted is back",
            "watch_link": "https://tv.apple.com/ted-lasso",
            "provider": "Apple TV",
            "web_channel": "Apple TV",
            "airstamp": "2026-08-05T12:00:00+00:00",
            "airtime": "",
            "runtime": 43,
            "is_network": False,
        },
        {
            "show_id": 1667,
            "show_name": "Saturday Night Live",
            "season_number": 52,
            "episode_number": 9,
            "episode_name": "Host TBA",
            "air_date": "2026-12-19",
            "overview": "Live comedy",
            "watch_link": "https://nbc.com/snl",
            "provider": "NBC",
            "airstamp": "2026-12-20T04:29:00+00:00",
            "airtime": "23:29",
            "runtime": 90,
            "is_network": True,
        },
        {
            "show_id": 202555,
            "show_name": "Daredevil: Born Again",
            "season_number": 2,
            "episode_number": 1,
            "episode_name": "Blind Justice",
            "air_date": "2026-03-24",
            "overview": "Matt Murdock",
            "watch_link": "https://disneyplus.com",
            "provider": "Disney+",
            "web_channel": "Disney+",
            "airstamp": "2026-03-24T12:00:00+00:00",
            "airtime": "",
            "runtime": 50,
            "is_network": False,
        }
    ]

    cal, kept = main.build_calendar(episodes)
    assert kept == 3, f"Expected 3 kept events, got {kept}"

    events = list(cal.walk("VEVENT"))
    assert len(events) == 3

    # Check Ted Lasso event (timed event at 8pm Central)
    ted_event = next(e for e in events if "Ted Lasso" in str(e.get("summary")))
    dtstart = ted_event.get("dtstart").dt
    assert isinstance(dtstart, datetime), "Ted Lasso should have datetime dtstart"
    dtstart_central = dtstart.astimezone(main.CALENDAR_TZ)
    assert dtstart_central.strftime("%A") == "Tuesday"
    assert dtstart_central.hour == 20

    # Check SNL event (timed event at 10:29pm Central)
    snl_event = next(e for e in events if "Saturday Night Live" in str(e.get("summary")))
    dtstart = snl_event.get("dtstart").dt
    assert isinstance(dtstart, datetime), "SNL should have datetime dtstart"
    dtstart_central = dtstart.astimezone(main.CALENDAR_TZ)
    assert dtstart_central.hour == 22 and dtstart_central.minute == 29

    # Check Daredevil event (all-day event on 2026-03-24)
    dd_event = next(e for e in events if "Daredevil" in str(e.get("summary")))
    dtstart = dd_event.get("dtstart").dt
    assert isinstance(dtstart, date) and not isinstance(dtstart, datetime), "Daredevil should be date-only (all-day)"
    assert dtstart == date(2026, 3, 24)

    print("✓ Calendar generation tests passed successfully!")


if __name__ == "__main__":
    test_tvmaze_mapping()
    test_is_between_12am_and_9am()
    test_ted_lasso_apple_tv_drop_time()
    test_early_morning_caveat_all_day_events()
    test_network_show_time_resolution()
    test_custom_drop_time()
    test_calendar_event_generation()
