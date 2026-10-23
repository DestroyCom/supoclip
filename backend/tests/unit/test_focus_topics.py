from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.ai import (
    TranscriptSegment,
    build_candidate_rerank_prompt,
    build_transcript_analysis_prompt,
)
from src.focus_topics import (
    MAX_FOCUS_TOPICS,
    MAX_FOCUS_TOPIC_CHARS,
    normalize_focus_topics,
)
from src.api.routes import tasks
from src.api.routes.tasks import _merge_task_source_metadata
from src.services.video_service import VideoService

TRANSCRIPT = "[00:12 - 00:21] A strong opening line"


def test_normalize_accepts_separated_string():
    assert normalize_focus_topics("événement fans, Meet & greet;\nconcert") == [
        "événement fans",
        "Meet & greet",
        "concert",
    ]


def test_normalize_cleans_list_and_dedupes_case_insensitively():
    assert normalize_focus_topics(
        ["  Fan  event ", "fan event", "", 3, None, "tour\x00 dates"]
    ) == ["Fan event", "tour dates"]


def test_normalize_empty_or_invalid_means_no_focus():
    for value in (None, "", "  , ;", [], {}, 12, [None, " "]):
        assert normalize_focus_topics(value) == []


def test_normalize_caps_count_and_length():
    topics = normalize_focus_topics([f"topic {i}" for i in range(50)])
    assert len(topics) == MAX_FOCUS_TOPICS
    assert len(normalize_focus_topics("x" * 500)[0]) == MAX_FOCUS_TOPIC_CHARS


def test_default_prompt_is_unchanged_without_focus_topics():
    default = build_transcript_analysis_prompt(transcript=TRANSCRIPT)

    assert build_transcript_analysis_prompt(
        transcript=TRANSCRIPT, focus_topics=None
    ) == default
    assert build_transcript_analysis_prompt(
        transcript=TRANSCRIPT, focus_topics=[]
    ) == default
    assert "focus topic" not in default.lower()
    assert "Choose 2-5 segments total." in default


def test_focus_topics_are_injected_into_analysis_prompt():
    prompt = build_transcript_analysis_prompt(
        transcript=TRANSCRIPT, focus_topics=["événement pour les fans", "concert"]
    )

    assert "Creator focus topics" in prompt
    assert "- événement pour les fans" in prompt
    assert "- concert" in prompt
    assert "Match by meaning, not exact words" in prompt
    assert "different language than the transcript" in prompt
    assert "empty most_relevant_segments list" in prompt
    assert "Choose 2-5 segments total." not in prompt
    assert "only from the focus topics" in prompt


def test_focus_topics_reach_the_rerank_prompt_only_when_set():
    candidate = TranscriptSegment(
        start_time="00:12",
        end_time="00:40",
        text="hello",
        relevance_score=0.9,
        reasoning="r",
    )
    plain = build_candidate_rerank_prompt([candidate, candidate])

    assert "focus topic" not in plain.lower()
    focused = build_candidate_rerank_prompt(
        [candidate, candidate], focus_topics=["concert"]
    )
    assert focused.startswith(plain)
    assert "- concert" in focused


def test_task_metadata_stores_focus_topics_only_when_present():
    assert "focus_topics" not in _merge_task_source_metadata({}, focus_topics=[])
    assert _merge_task_source_metadata({}, focus_topics=["concert"])[
        "focus_topics"
    ] == ["concert"]
    # A later settings edit (no topics passed) keeps the stored ones.
    assert _merge_task_source_metadata(
        {"focus_topics": ["concert"]}, add_subtitles=True
    )["focus_topics"] == ["concert"]


@pytest.fixture
def creation(monkeypatch):
    billing = {"max_youtube_duration_seconds": 5400, "max_upload_duration_seconds": 5400}
    monkeypatch.setattr(tasks.BillingService, "assert_can_create_task", AsyncMock(return_value=billing))
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    save_metadata = AsyncMock()
    monkeypatch.setattr(tasks, "_save_task_source_metadata", save_metadata)
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": 600}))
    service = SimpleNamespace(
        video_service=VideoService(),
        create_task_with_source=AsyncMock(return_value="task-1"),
    )
    monkeypatch.setattr(tasks, "TaskService", lambda _db: service)
    queue = SimpleNamespace(enqueue_processing_job=AsyncMock(return_value="job-1"))

    def make_request(extra):
        body = {"source": {"url": "https://youtu.be/abcdefghijk"}, **extra}
        return SimpleNamespace(
            json=AsyncMock(return_value=body),
            app=SimpleNamespace(state=SimpleNamespace(queue_adapter=queue)),
        )

    return make_request, queue, save_metadata


@pytest.mark.asyncio
async def test_create_task_without_topics_enqueues_exactly_as_before(creation):
    make_request, queue, save_metadata = creation

    await tasks.create_task(make_request({}), db=None)

    assert "focus_topics" not in queue.enqueue_processing_job.await_args.kwargs
    assert "focus_topics" not in save_metadata.await_args.args[1]


@pytest.mark.asyncio
async def test_create_task_forwards_and_persists_focus_topics(creation):
    make_request, queue, save_metadata = creation

    await tasks.create_task(
        make_request({"focus_topics": "événement fans, concert"}), db=None
    )

    assert queue.enqueue_processing_job.await_args.kwargs["focus_topics"] == [
        "événement fans",
        "concert",
    ]
    assert save_metadata.await_args.args[1]["focus_topics"] == [
        "événement fans",
        "concert",
    ]
