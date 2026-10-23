import json
from types import SimpleNamespace

import pytest

from src.media import transcription
from src.media.transcription import (
    cache_transcript_data,
    format_transcript_for_analysis,
    load_reusable_whisper_transcript,
    _get_transcript_with_whisper,
    _whisper_cache_meta,
)

DURATION = 600.0
WHISPER_RESULT = {
    "text": " Bonjour a tous. On commence.",
    "language": "fr",
    "segments": [
        {
            "start": 0.0,
            "end": 12.345,
            "text": " Bonjour a tous.",
            "words": [
                {"word": " Bonjour", "start": 0.0, "end": 0.5, "probability": 0.9},
                {"word": " a", "start": 0.5, "end": 0.6, "probability": 0.9},
                {"word": " tous.", "start": 0.6, "end": 12.345, "probability": 0.9},
            ],
        },
        {
            "start": 12.345,
            "end": 598.0,
            "text": " On commence.",
            "words": [
                {"word": " On", "start": 12.345, "end": 13.0, "probability": 0.9},
                {"word": " commence.", "start": 13.0, "end": 598.0, "probability": 0.9},
            ],
        },
    ],
}


def make_config(**overrides):
    values = dict(
        whisper_model="medium",
        transcription_language="",
        transcript_cache_trust_legacy=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def video(tmp_path, monkeypatch):
    path = tmp_path / "full.mp4"
    path.write_bytes(b"x" * 1000)
    monkeypatch.setattr(transcription, "ffprobe_duration", lambda _path: DURATION)
    return path


def write_cache(video, config=None, **changes):
    """Write a cache the way a Whisper run does, then apply field changes."""
    config = config or make_config()
    cache_transcript_data(video, WHISPER_RESULT, _whisper_cache_meta(video, config))
    path = video.with_suffix(".transcript_cache.json")
    payload = json.loads(path.read_text())
    for key, value in changes.items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    path.write_text(json.dumps(payload))


def strip_meta(video):
    path = video.with_suffix(".transcript_cache.json")
    payload = json.loads(path.read_text())
    for key in ("provider", "whisper_model", "requested_language", "source_size", "source_duration"):
        payload.pop(key, None)
    path.write_text(json.dumps(payload))


def test_matching_cache_returns_exactly_what_whisper_would(video):
    write_cache(video)
    reused = load_reusable_whisper_transcript(video, make_config())
    assert reused == "\n".join(format_transcript_for_analysis(WHISPER_RESULT))
    assert reused.splitlines()[0] == "[00:00 - 00:12] Bonjour a tous."


def test_cache_records_model_language_and_source(video):
    write_cache(video, make_config(transcription_language="fr"))
    payload = json.loads(video.with_suffix(".transcript_cache.json").read_text())
    assert payload["provider"] == "whisper"
    assert payload["whisper_model"] == "medium"
    assert payload["requested_language"] == "fr"
    assert payload["source_size"] == 1000
    assert payload["source_duration"] == DURATION


def test_reuse_skips_whisper_and_does_not_rewrite_the_cache(video, monkeypatch):
    write_cache(video)
    before = video.with_suffix(".transcript_cache.json").read_bytes()
    monkeypatch.setattr(
        transcription,
        "transcribe_with_whisper",
        lambda *_a, **_k: pytest.fail("Whisper must not run"),
    )
    text = _get_transcript_with_whisper(video, make_config())
    assert text.startswith("[00:00 - 00:12]")
    assert video.with_suffix(".transcript_cache.json").read_bytes() == before


def test_mismatch_runs_whisper_and_refreshes_cache(video, monkeypatch):
    write_cache(video, make_config(whisper_model="small"))
    calls = []

    def fake_whisper(*args, **_kwargs):
        calls.append(args)
        return WHISPER_RESULT

    monkeypatch.setattr(transcription, "transcribe_with_whisper", fake_whisper)
    _get_transcript_with_whisper(video, make_config())
    assert len(calls) == 1
    payload = json.loads(video.with_suffix(".transcript_cache.json").read_text())
    assert payload["whisper_model"] == "medium"


@pytest.mark.parametrize(
    "config, changes",
    [
        (make_config(whisper_model="large"), {}),
        (make_config(transcription_language="fr"), {}),
        (make_config(), {"provider": "assemblyai"}),
        (make_config(), {"source_size": 999}),
        (make_config(), {"source_duration": DURATION + 5}),
        (make_config(), {"words": []}),
        (make_config(), {"utterances": []}),
        (make_config(), {"version": 1}),
    ],
)
def test_cache_is_ignored_when_not_provably_the_same(video, config, changes):
    write_cache(video, **changes)
    assert load_reusable_whisper_transcript(video, config) is None


def test_missing_or_corrupt_cache_is_ignored(video):
    assert load_reusable_whisper_transcript(video, make_config()) is None
    video.with_suffix(".transcript_cache.json").write_text("{not json")
    assert load_reusable_whisper_transcript(video, make_config()) is None


def test_legacy_cache_needs_the_trust_flag(video):
    write_cache(video)
    strip_meta(video)
    assert load_reusable_whisper_transcript(video, make_config()) is None
    reused = load_reusable_whisper_transcript(
        video, make_config(transcript_cache_trust_legacy=True)
    )
    assert reused == "\n".join(format_transcript_for_analysis(WHISPER_RESULT))


def test_legacy_cache_must_cover_the_source(video, monkeypatch):
    write_cache(video)
    strip_meta(video)
    trusted = make_config(transcript_cache_trust_legacy=True)
    # Last word ends at 598 s: 95% of 630 s is 598.5 s, so it is too short.
    monkeypatch.setattr(transcription, "ffprobe_duration", lambda _p: 630.0)
    assert load_reusable_whisper_transcript(video, trusted) is None
    monkeypatch.setattr(transcription, "ffprobe_duration", lambda _p: 629.0)
    assert load_reusable_whisper_transcript(video, trusted) is not None
    # Up to 30 s of overrun is tolerated, not more.
    monkeypatch.setattr(transcription, "ffprobe_duration", lambda _p: 568.0)
    assert load_reusable_whisper_transcript(video, trusted) is not None
    monkeypatch.setattr(transcription, "ffprobe_duration", lambda _p: 567.0)
    assert load_reusable_whisper_transcript(video, trusted) is None


def test_legacy_cache_needs_a_known_duration(video, monkeypatch):
    write_cache(video)
    strip_meta(video)

    def fail(_path):
        raise RuntimeError("no ffprobe")

    monkeypatch.setattr(transcription, "ffprobe_duration", fail)
    trusted = make_config(transcript_cache_trust_legacy=True)
    assert load_reusable_whisper_transcript(video, trusted) is None


def test_legacy_cache_language_must_match_request(video):
    write_cache(video)
    strip_meta(video)
    assert load_reusable_whisper_transcript(
        video, make_config(transcript_cache_trust_legacy=True, transcription_language="en_us")
    ) is None
    assert load_reusable_whisper_transcript(
        video, make_config(transcript_cache_trust_legacy=True, transcription_language="fr")
    ) is not None


def test_legacy_cache_with_speaker_labels_is_not_whisper(video):
    write_cache(video)
    strip_meta(video)
    path = video.with_suffix(".transcript_cache.json")
    payload = json.loads(path.read_text())
    payload["utterances"][0]["speaker"] = "A"
    path.write_text(json.dumps(payload))
    assert load_reusable_whisper_transcript(
        video, make_config(transcript_cache_trust_legacy=True)
    ) is None


def test_reuse_reports_a_single_final_progress_tick(video, monkeypatch):
    write_cache(video)
    monkeypatch.setattr(
        transcription,
        "transcribe_with_whisper",
        lambda *_a, **_k: pytest.fail("Whisper must not run"),
    )
    ticks = []
    _get_transcript_with_whisper(
        video, make_config(), on_progress=lambda *report: ticks.append(report)
    )
    assert ticks == [(DURATION, DURATION, 0.0, 0.0)]


def test_reuse_propagates_a_stop_request_from_the_progress_hook(video):
    write_cache(video)

    def stop(*_report):
        raise transcription.TranscriptionAborted("stopped")

    with pytest.raises(transcription.TranscriptionAborted):
        _get_transcript_with_whisper(video, make_config(), on_progress=stop)


def test_force_rerun_transcribes_again_despite_a_matching_cache(video, monkeypatch):
    write_cache(video)
    calls = []

    def fake_whisper(*args, **kwargs):
        calls.append(kwargs)
        return WHISPER_RESULT

    monkeypatch.setattr(transcription, "transcribe_with_whisper", fake_whisper)
    monkeypatch.setattr(
        transcription,
        "get_config",
        lambda: make_config(transcription_provider="whisper"),
    )

    transcription.get_video_transcript(video, reuse_cache=False)

    assert len(calls) == 1
    assert "reuse_cache" not in calls[0]


def test_whisper_reuse_is_still_the_default(video, monkeypatch):
    write_cache(video)
    monkeypatch.setattr(
        transcription,
        "transcribe_with_whisper",
        lambda *_a, **_k: pytest.fail("Whisper must not run"),
    )
    monkeypatch.setattr(
        transcription,
        "get_config",
        lambda: make_config(transcription_provider="whisper"),
    )

    assert transcription.get_video_transcript(video).startswith("[00:00 - 00:12]")
