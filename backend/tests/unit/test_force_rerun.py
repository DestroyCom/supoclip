"""Force total rerun: every stored result is skipped, and only when asked."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import youtube_source as source
from src.api.routes import tasks
from src.config import Config, set_config_override
from src.services import video_service as module
from src.services.task_service import TaskService
from src.services.video_service import VideoService

VIDEO_ID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def build_service() -> TaskService:
    service = TaskService(db=AsyncMock(), config=Config())
    service._load_performance_context = AsyncMock(return_value=None)
    service._processing_transaction = lambda _task_id: nullcontext()
    service._task_run_guard = lambda _task_id: nullcontext()
    service.task_repo.get_task_by_id = AsyncMock(return_value={"status": "queued"})
    service.cache_repo.get_cache = AsyncMock(
        return_value={"transcript_text": "old transcript", "analysis_json": "{}"}
    )
    service.cache_repo.upsert_cache = AsyncMock()
    service.task_repo.update_task_runtime_metadata = AsyncMock()
    service.task_repo.update_task_status = AsyncMock()
    service.task_repo.update_task_clips = AsyncMock()
    service.clip_repo.delete_clips_by_task = AsyncMock(return_value=0)
    # No segments stops the task right after the cache refresh we want to see.
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "segments_to_render": [],
            "video_path": "/tmp/source.mp4",
            "transcript": "new transcript",
            "analysis_json": "{}",
        }
    )
    return service


async def run_task(service: TaskService, **kwargs) -> None:
    with pytest.raises(ValueError, match="No usable clip segments"):
        await service.process_task(
            task_id="task-1", url=URL, source_type="youtube", **kwargs
        )


@pytest.mark.asyncio
async def test_default_run_reuses_the_stored_results():
    service = build_service()

    await run_task(service)

    service.cache_repo.get_cache.assert_awaited_once()
    kwargs = service.video_service.process_video_complete.await_args.kwargs
    assert kwargs["cached_transcript"] == "old transcript"
    assert kwargs["cached_analysis_json"] == "{}"
    assert "force_rerun" not in kwargs


@pytest.mark.asyncio
async def test_forced_run_ignores_stored_results_but_refreshes_them():
    service = build_service()

    await run_task(service, force_rerun=True)

    service.cache_repo.get_cache.assert_not_awaited()
    kwargs = service.video_service.process_video_complete.await_args.kwargs
    assert kwargs["cached_transcript"] is None
    assert kwargs["cached_analysis_json"] is None
    assert kwargs["force_rerun"] is True
    assert service.cache_repo.upsert_cache.await_args.kwargs["transcript_text"] == (
        "new transcript"
    )
    first_call = service.task_repo.update_task_runtime_metadata.await_args_list[0]
    assert first_call.kwargs["cache_hit"] is False


def test_purge_forgets_every_stored_download(tmp_path, monkeypatch):
    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr(source, "_storage", lambda: None)
    set_config_override(config)
    try:
        directory = source.cache_dir_for(VIDEO_ID, config)
        (directory / "work").mkdir(parents=True)
        (directory / "work" / "part.mp4").write_bytes(b"part")
        (directory / "full.mp4").write_bytes(b"full")
        (directory / "full.transcript_cache.json").write_text("{}")
        (directory / source.MANIFEST_NAME).write_text(
            '{"version": 1, "video_id": "%s", "full": "full.mp4"}' % VIDEO_ID
        )
        other = source.cache_dir_for("zzzzzzzzzzz", config)
        other.mkdir(parents=True)
        (other / "full.mp4").write_bytes(b"keep")

        source.purge_youtube_cache(VIDEO_ID)

        assert sorted(p.name for p in directory.iterdir()) == [".lock"]
        assert source.cached_full_media(VIDEO_ID) is None
        assert (other / "full.mp4").exists()
        source.purge_youtube_cache("unknownvid1")  # nothing stored: no error
    finally:
        set_config_override(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [True, False])
async def test_forced_pipeline_purges_before_looking_for_a_cached_download(
    tmp_path, monkeypatch, force
):
    config = SimpleNamespace(
        temp_dir=str(tmp_path), max_video_duration=10800, youtube_download_provider="yt_dlp"
    )
    monkeypatch.setattr(module, "get_config", lambda: config)
    order = []

    async def info(*_args, **_kwargs):
        return {"duration": 600}

    async def download(*_args, **_kwargs):
        order.append("download")
        return tmp_path / "video.mp4"

    async def stop(*_args, **_kwargs):
        raise RuntimeError("stop here")

    monkeypatch.setattr(module, "async_get_youtube_video_info", info)
    monkeypatch.setattr(module, "purge_youtube_cache", lambda _id: order.append("purge"))
    monkeypatch.setattr(
        module, "cached_full_media", lambda _id: order.append("lookup")
    )
    monkeypatch.setattr(VideoService, "download_video", download)
    monkeypatch.setattr(VideoService, "_get_file_duration", lambda _path: 600)
    monkeypatch.setattr(VideoService, "generate_transcript", stop)

    kwargs = {"force_rerun": True} if force else {}
    with pytest.raises(RuntimeError, match="stop here"):
        await VideoService.process_video_complete(URL, "youtube", **kwargs)

    assert order == (["purge"] if force else []) + ["lookup", "download"]


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
        body = {"source": {"url": URL}, **extra}
        return SimpleNamespace(
            json=AsyncMock(return_value=body),
            app=SimpleNamespace(state=SimpleNamespace(queue_adapter=queue)),
        )

    return make_request, queue, save_metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{}, {"force_rerun": False}, {"force_rerun": "true"}, {"force_rerun": 1}])
async def test_create_task_only_forces_on_an_explicit_true(creation, extra):
    make_request, queue, save_metadata = creation

    await tasks.create_task(make_request(extra), db=None)

    assert "force_rerun" not in queue.enqueue_processing_job.await_args.kwargs
    assert "force_rerun" not in save_metadata.await_args.args[1]


@pytest.mark.asyncio
async def test_create_task_forwards_force_rerun_without_storing_it(creation):
    make_request, queue, save_metadata = creation

    await tasks.create_task(make_request({"force_rerun": True}), db=None)

    assert queue.enqueue_processing_job.await_args.kwargs["force_rerun"] is True
    # Not persisted: a later resume reuses the fresh results.
    assert "force_rerun" not in save_metadata.await_args.args[1]
