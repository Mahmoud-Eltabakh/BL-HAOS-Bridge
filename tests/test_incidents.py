"""Persistent monitoring is bounded, private, and independent of DEBUG logs."""

import asyncio
from datetime import datetime, timezone
import json
import logging
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from backend.bl_haos.health import HealthRegistry, HealthState, SpeakerState
from backend.bl_haos.incidents import DAILY_BYTES, IncidentRecorder
from backend.bl_haos.main import app
from backend.bl_haos import main


def make_recorder(tmp_path, clock=None):
    health = HealthRegistry()
    health.set_lifecycle(HealthState.HEALTHY)
    return IncidentRecorder(tmp_path, health, lambda: 1, clock or (lambda: 1792267200.0))


async def test_start_stop_records_and_detaches_handler(tmp_path):
    recorder = make_recorder(tmp_path)
    await recorder.start()
    try:
        assert recorder.status()["running"]
        assert recorder in logging.getLogger("bl_haos").handlers
        logging.getLogger("bl_haos.bluetooth.reconnect").warning(
            "Tripping circuit breaker for private-address token=secret https://private/"
        )
        await recorder.sample()
    finally:
        await recorder.stop()
    assert not recorder.status()["running"]
    assert recorder not in logging.getLogger("bl_haos").handlers
    entries = await recorder.recent()
    assert entries[0]["kind"] == "monitor_started"
    assert entries[1]["patterns"] == {"reconnect_exhausted": 1}
    text = json.dumps(entries)
    assert "secret" not in text
    assert "private-address" not in text
    assert "https://" not in text


async def test_sample_detects_health_changes_and_suppresses_unchanged_state(tmp_path):
    now = [1792267200.0]
    recorder = make_recorder(tmp_path, lambda: now[0])
    await recorder.sample()
    await recorder.sample()
    assert len(await recorder.recent()) == 1
    recorder.health.observe_speaker("aa:bb:cc:dd:ee:ff", SpeakerState.CONNECTED)
    await recorder.sample()
    recorder.health.observe_speaker("aa:bb:cc:dd:ee:ff", SpeakerState.DISCONNECTED)
    await recorder.sample()
    entries = await recorder.recent()
    assert len(entries) == 3
    assert "aa:bb:cc:dd:ee:ff" not in json.dumps(entries)
    assert list(entries[-1]["context"]["speakers"].values())[0]["state"] == "disconnected"
    assert "speaker_not_connected" in entries[-1]["findings"]
    assert "health_or_connection_changed" in entries[-1]["findings"]
    now[0] += 3600
    await recorder.sample()
    assert len(await recorder.recent()) == 4


async def test_only_monitor_files_older_than_30_utc_dates_are_pruned(tmp_path):
    now = datetime(2026, 10, 17, tzinfo=timezone.utc).timestamp()
    old = tmp_path / "incidents-2026-09-17.jsonl"
    oldest_kept = tmp_path / "incidents-2026-09-18.jsonl"
    unrelated = tmp_path / "unrelated.jsonl"
    invalid = tmp_path / "incidents-9999-99-99.jsonl"
    for path in (old, oldest_kept, unrelated, invalid):
        path.write_text("{}\n")
    await make_recorder(tmp_path, lambda: now).sample()
    assert not old.exists()
    assert all(path.exists() for path in (oldest_kept, unrelated, invalid))


async def test_exact_daily_byte_cap_and_next_day_resume(tmp_path):
    now = [1792267200.0]
    recorder = make_recorder(tmp_path, lambda: now[0])
    await recorder.sample()
    target = next(tmp_path.glob("*.jsonl"))
    target.write_bytes(b"x" * DAILY_BYTES)
    await recorder.sample(force=True)
    assert target.stat().st_size == DAILY_BYTES
    assert recorder.status()["dropped_observations"] == 1
    now[0] += 86400
    await recorder.sample(force=True)
    assert len(list(tmp_path.glob("*.jsonl"))) == 2
    assert (await recorder.recent())[0]["dropped_observations"] == 1


async def test_storage_failure_is_visible_and_recovers(tmp_path, caplog):
    recorder = make_recorder(tmp_path)
    with patch.object(recorder, "_append", side_effect=PermissionError):
        await recorder.sample()
    assert recorder.status()["storage_error"] == "PermissionError"
    assert "Incident storage failed" in caplog.text
    await recorder.sample()
    assert recorder.status()["storage_error"] is None
    assert recorder.status()["dropped_observations"] == 1


async def test_history_is_bounded_to_last_100_records(tmp_path):
    recorder = make_recorder(tmp_path)
    for index in range(105):
        recorder._append({"index": index})
    result = await recorder.recent()
    assert len(result) == 100
    assert result[0]["index"] == 5
    assert result[-1]["index"] == 104


async def test_parallel_samples_do_not_interleave_partial_json(tmp_path):
    recorder = make_recorder(tmp_path)
    await asyncio.gather(*(recorder.sample(force=True) for _ in range(10)))
    assert len(await recorder.recent()) == 10


def test_incident_api_is_disabled_by_default():
    with TestClient(app) as client:
        response = client.get("/api/incidents")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "status": None, "observations": []}


def test_enabled_lifespan_runs_recorder_and_serves_history(tmp_path, monkeypatch):
    created = []

    def factory(directory, health, client_count):
        recorder = IncidentRecorder(tmp_path, health, client_count)
        created.append(recorder)
        return recorder

    monkeypatch.setenv("BLHAOS_INCIDENT_MONITOR", "true")
    monkeypatch.setattr(main, "IncidentRecorder", factory)
    with TestClient(app) as client:
        response = client.get("/api/incidents")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["status"]["running"] is True
        assert data["status"]["retention_days"] == 30
        assert data["observations"][0]["kind"] == "monitor_started"
        with patch.object(created[0], "_read_recent", side_effect=PermissionError):
            assert client.get("/api/incidents").status_code == 503
    assert not created[0].status()["running"]
    assert created[0] not in logging.getLogger("bl_haos").handlers


async def test_background_task_samples_without_external_requests(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.bl_haos.incidents.SAMPLE_SECONDS", 0.005)
    recorder = make_recorder(tmp_path)
    await recorder.start()
    try:
        recorder.health.observe_speaker("aa:bb:cc:dd:ee:ff", SpeakerState.DISCONNECTED)
        async with asyncio.timeout(2):
            while len(await recorder.recent()) < 2:
                await asyncio.sleep(0.005)
        assert "speaker_not_connected" in (await recorder.recent())[-1]["findings"]
    finally:
        await recorder.stop()


async def test_stop_waits_for_inflight_disk_write_before_final_flush(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.bl_haos.incidents.SAMPLE_SECONDS", 0.005)
    recorder = make_recorder(tmp_path)
    await recorder.start()
    entered = Event()
    release = Event()
    append = recorder._append

    def delayed_append(entry):
        entered.set()
        assert release.wait(timeout=3)
        return append(entry)

    recorder.health.observe_speaker("aa:bb:cc:dd:ee:ff", SpeakerState.DISCONNECTED)
    with patch.object(recorder, "_append", side_effect=delayed_append):
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.005)
            stopping = asyncio.create_task(recorder.stop())
            await asyncio.sleep(0.01)
            assert not stopping.done()
            release.set()
            await stopping
    assert len(await recorder.recent()) == 3
