"""
Conformance tests: feed input from ../raceflag_specs/conformance/ through the Pi's
message handling and verify the flag-decision sequence matches expected.json.

Skipped (with a clear message) when ../raceflag_specs is not present, so CI Docker
builds that don't mount the specs repo are not broken.
"""
import json
import pytest
from pathlib import Path

from raceflag.f1_listener import F1Listener
from raceflag.replay_manager import _parse_jsonstream_line
from raceflag.state import AppState

SPECS_DIR = Path(__file__).parent.parent.parent / "raceflag_specs"
CONFORMANCE_DIR = SPECS_DIR / "conformance"

_RACE_SESSION_TYPES = {"race", "sprint"}
_IDLE_STATUSES = {"unknown", "break", "finished"}


def _discover_cases():
    if not CONFORMANCE_DIR.exists():
        return []
    return sorted(
        p for p in CONFORMANCE_DIR.iterdir()
        if p.is_dir() and (p / "expected.json").exists()
    )


_CASES = _discover_cases()


def _load_case(case_dir: Path):
    """Return (events, expected_decisions) for a conformance case."""
    input_dir = case_dir / "input"
    events = []
    for stream_file in sorted(input_dir.glob("*.jsonStream")):
        topic = stream_file.stem
        for line in stream_file.read_text(encoding="utf-8").splitlines():
            parsed = _parse_jsonstream_line(line)
            if parsed is not None:
                ts, payload = parsed
                events.append((ts, topic, payload))
    events.sort(key=lambda x: x[0])
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    return events, expected


def _run_case(events):
    """Feed events through F1Listener with race-start promotion; return list of flag decisions."""
    decisions = []
    state = AppState()
    race_started = False

    def on_flag_change(status):
        nonlocal race_started
        if status in _IDLE_STATUSES:
            race_started = False

        effective = status
        if (
            status == "track_clear"
            and not race_started
            and state.session.is_active
            and state.session.session_type.lower() in _RACE_SESSION_TYPES
        ):
            race_started = True
            effective = "race_start"

        decisions.append(effective)

    listener = F1Listener(state=state, on_track_status_change=on_flag_change)
    for _ts, topic, data in events:
        listener._handle_feed(topic, data, is_snapshot=False)
    return decisions


@pytest.mark.skipif(
    not CONFORMANCE_DIR.exists(),
    reason=f"../raceflag_specs not present (expected at {CONFORMANCE_DIR})",
)
@pytest.mark.parametrize(
    "case_dir",
    _CASES,
    ids=[c.name for c in _CASES] if _CASES else [],
)
def test_conformance(case_dir):
    events, expected_decisions = _load_case(case_dir)
    actual = _run_case(events)
    expected_events = [d["event"] for d in expected_decisions]
    assert actual == expected_events, (
        f"Case {case_dir.name}:\n  expected: {expected_events}\n  actual:   {actual}"
    )
