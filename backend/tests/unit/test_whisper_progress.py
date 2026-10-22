import asyncio
import logging
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from src.media import transcription
from src.media.transcription import TranscriptionAborted, transcribe_with_whisper
from src.repositories.edit_transaction import TaskCancelled
from src.services.video_service import _WhisperProgressBridge, _format_clock, _format_eta


@pytest.fixture
def whisper_module(monkeypatch):
    """A stand-in for ``whisper.transcribe`` exposing the tqdm name Whisper uses."""
    original_tqdm = SimpleNamespace(tqdm=type("RealTqdm", (), {}))
    module = ModuleType("whisper.transcribe")
    module.tqdm = original_tqdm
    monkeypatch.setitem(sys.modules, "whisper.transcribe", module)
    monkeypatch.setattr(transcription, "_prepare_audio_for_transcription", lambda path: path)
    monkeypatch.setattr(transcription, "WHISPER_LOCK_POLL_SECONDS", 0.02)
    module.original = original_tqdm
    return module


def fake_model(windows=3, frames=1000, between=None):
    """Mimics Whisper: opens module.tqdm.tqdm(...) and updates it per window."""

    def transcribe(_path, **_kwargs):
        module = sys.modules["whisper.transcribe"]
        with module.tqdm.tqdm(total=windows * frames, unit="frames", disable=True) as pbar:
            for _ in range(windows):
                if between:
                    between()
                pbar.update(frames)
        return {"segments": [], "text": ""}

    return SimpleNamespace(transcribe=transcribe)


def use_model(monkeypatch, model):
    monkeypatch.setattr(transcription, "_get_whisper_model", lambda _name: model)


def test_progress_reports_audio_seconds_and_restores_tqdm(whisper_module, monkeypatch):
    use_model(monkeypatch, fake_model())
    ticks = iter(range(0, 1000, 5))  # fake clock: every reading is 5 s later
    monkeypatch.setattr(transcription, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    reports = []
    transcribe_with_whisper(
        Path("a.mp4"), on_progress=lambda *report: reports.append(report), task_id="t1"
    )
    # 3 windows of 1000 frames (10 ms each) out of 30 s.
    assert [round(r[0]) for r in reports] == [10, 20, 30]
    assert all(r[1] == pytest.approx(30.0) for r in reports)
    # Speed is observed: 10 s of audio in 5 s of wall time leaves 20 s of audio = 10 s.
    assert reports[0][2] == 5 and reports[0][3] == pytest.approx(10.0)
    assert reports[-1][3] == 0
    assert whisper_module.tqdm is whisper_module.original


def test_tqdm_restored_when_whisper_fails(whisper_module, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("cannot reshape tensor of 0 elements")

    use_model(monkeypatch, SimpleNamespace(transcribe=boom))
    with pytest.raises(RuntimeError):
        transcribe_with_whisper(Path("a.mp4"), on_progress=lambda *_: None)
    assert whisper_module.tqdm is whisper_module.original
    assert transcription._WHISPER_TRANSCRIBE_LOCK.acquire(blocking=False)
    transcription._WHISPER_TRANSCRIBE_LOCK.release()


def test_unexpected_whisper_internals_still_transcribe(whisper_module, monkeypatch, caplog):
    whisper_module.tqdm = object()  # no .tqdm attribute
    result = {"segments": [], "text": "ok"}
    use_model(monkeypatch, SimpleNamespace(transcribe=lambda *_a, **_k: result))
    with caplog.at_level(logging.WARNING):
        assert transcribe_with_whisper(Path("a.mp4"), on_progress=lambda *_: None) is result
    assert "without progress" in caplog.text


def test_without_callbacks_nothing_is_patched(whisper_module, monkeypatch):
    seen = []
    result = {"segments": []}

    def transcribe(*_a, **_k):
        seen.append(sys.modules["whisper.transcribe"].tqdm)
        return result

    use_model(monkeypatch, SimpleNamespace(transcribe=transcribe))
    assert transcribe_with_whisper(Path("a.mp4")) is result
    assert seen == [whisper_module.original]


def test_transcriptions_are_serialized(whisper_module, monkeypatch):
    running = 0
    peak = 0
    guard = threading.Lock()

    def transcribe(*_a, **_k):
        nonlocal running, peak
        with guard:
            running += 1
            peak = max(peak, running)
        time.sleep(0.15)
        with guard:
            running -= 1
        return {"segments": []}

    use_model(monkeypatch, SimpleNamespace(transcribe=transcribe))
    waiting = []
    threads = [
        threading.Thread(
            target=transcribe_with_whisper,
            args=(Path("a.mp4"),),
            kwargs={"on_waiting": waiting.append, "task_id": f"t{i}"},
        )
        for i in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert peak == 1
    assert waiting  # queued jobs were told they are waiting


def test_waiting_job_gives_up_when_stopped(whisper_module, monkeypatch):
    use_model(monkeypatch, fake_model())
    stop = threading.Event()
    assert transcription._WHISPER_TRANSCRIBE_LOCK.acquire(blocking=False)
    try:
        threading.Timer(0.1, stop.set).start()
        with pytest.raises(TranscriptionAborted):
            transcribe_with_whisper(Path("a.mp4"), cancel_event=stop)
    finally:
        transcription._WHISPER_TRANSCRIBE_LOCK.release()


def test_running_job_stops_at_next_window_and_frees_lock(whisper_module, monkeypatch):
    stop = threading.Event()
    windows_done = []
    use_model(monkeypatch, fake_model(windows=5, between=lambda: windows_done.append(1)))

    def on_progress(*_report):
        stop.set()

    with pytest.raises(TranscriptionAborted):
        transcribe_with_whisper(Path("a.mp4"), on_progress=on_progress, cancel_event=stop)
    assert len(windows_done) == 2  # stopped right after the window that set the event
    assert whisper_module.tqdm is whisper_module.original
    assert transcription._WHISPER_TRANSCRIBE_LOCK.acquire(blocking=False)
    transcription._WHISPER_TRANSCRIBE_LOCK.release()


@pytest.mark.asyncio
async def test_bridge_throttles_and_maps_progress_range():
    events = []

    async def callback(progress, message, status, detail=None):
        events.append((progress, message, detail))

    bridge = _WhisperProgressBridge(asyncio.get_running_loop(), callback, threading.Event())

    def worker():
        for done in range(1, 101):  # 100 updates inside well under 2 s
            bridge.on_progress(done * 0.1, 100.0, done * 0.01, 60.0)
        bridge.on_progress(100.0, 100.0, 1.5, 0.0)

    await asyncio.to_thread(worker)
    await asyncio.sleep(0.05)

    assert len(events) < 20  # throttled by 1% steps, not one per update
    progress, message, detail = events[-1]
    assert progress == 50  # transcription ends at the analysis stage's start
    assert "Transcribing 100%" in message
    assert detail["stage"] == "transcribe" and detail["stage_progress"] == 100.0
    assert detail["tick"] is True
    assert all(30 <= e[0] <= 50 for e in events)
    assert "left" in events[0][1]


@pytest.mark.asyncio
async def test_bridge_stops_transcription_when_task_is_cancelled():
    stop = threading.Event()

    async def callback(*_args, **_kwargs):
        raise TaskCancelled()

    bridge = _WhisperProgressBridge(asyncio.get_running_loop(), callback, stop)
    await asyncio.to_thread(bridge.on_waiting, 12)
    await asyncio.sleep(0.05)
    assert stop.is_set() and bridge.user_cancelled
    with pytest.raises(TranscriptionAborted):
        bridge.on_progress(1.0, 10.0, 1.0, 9.0)


@pytest.mark.asyncio
async def test_bridge_reports_waiting_without_losing_the_stage():
    events = []

    async def callback(progress, message, status, detail=None):
        events.append((progress, message, detail))

    bridge = _WhisperProgressBridge(asyncio.get_running_loop(), callback, threading.Event())
    await asyncio.to_thread(bridge.on_waiting, 75)
    await asyncio.sleep(0.05)
    progress, message, detail = events[0]
    assert progress == 30
    assert message == "Waiting for another transcription… (1:15)"
    assert detail["waiting"] is True and detail["stage"] == "transcribe"


def test_time_formatting():
    assert _format_clock(65) == "1:05"
    assert _format_clock(7740) == "2:09:00"
    assert _format_eta(20) == "<1m"
    assert _format_eta(780) == "13m"
    assert _format_eta(4800) == "1h 20m"
