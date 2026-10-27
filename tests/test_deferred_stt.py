"""DeferredConnectDeepgramFluxSTTService: connect in background, don't block start."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pipecat.services.deepgram.flux.base import DeepgramFluxSTTBase

from app.config import get_settings
from app.services.deferred_flux_stt import DeferredConnectDeepgramFluxSTTService


def _make_service():
    settings = get_settings()
    return DeferredConnectDeepgramFluxSTTService(
        api_key=settings.deepgram_api_key, sample_rate=16000
    )


def _grandparent(svc):
    mro = type(svc).__mro__
    return mro[mro.index(DeepgramFluxSTTBase) + 1]


def test_start_runs_base_setup_and_defers_connect_to_background():
    svc = _make_service()
    grandparent = _grandparent(svc)
    scheduled: list = []

    def fake_create_task(coro, *a, **k):
        fut = asyncio.ensure_future(coro)
        scheduled.append(fut)
        return fut

    async def noop_bg():
        return None

    async def run():
        with patch.object(grandparent, "start", new_callable=AsyncMock) as gp_start, \
             patch.object(svc, "_background_connect", noop_bg), \
             patch.object(svc, "create_task", side_effect=fake_create_task):
            # start() must return promptly (StartFrame is free to propagate).
            await asyncio.wait_for(svc.start(MagicMock()), timeout=1.0)
            gp_start.assert_awaited_once()        # base setup ran
            assert len(scheduled) == 1            # connect scheduled, not awaited inline
            assert svc._deferred_connect_task is not None
        for f in scheduled:
            await f

    asyncio.run(run())


def test_start_does_not_block_even_if_connect_is_slow():
    svc = _make_service()
    grandparent = _grandparent(svc)
    connect_started = asyncio.Event()
    release = asyncio.Event()

    async def slow_connect():
        connect_started.set()
        await release.wait()

    def fake_create_task(coro, *a, **k):
        return asyncio.ensure_future(coro)

    async def run():
        with patch.object(grandparent, "start", new_callable=AsyncMock), \
             patch.object(svc, "_connect", slow_connect), \
             patch.object(svc, "create_task", side_effect=fake_create_task):
            # Even though _connect blocks forever, start() returns.
            await asyncio.wait_for(svc.start(MagicMock()), timeout=1.0)
            await asyncio.wait_for(connect_started.wait(), timeout=1.0)
            release.set()
            try:
                await asyncio.wait_for(svc._deferred_connect_task, timeout=1.0)
            except Exception:
                pass

    asyncio.run(run())


def test_background_connect_failure_is_surfaced_as_error_frame():
    svc = _make_service()
    errs: list = []

    async def boom():
        raise RuntimeError("flux connect failed")

    async def run():
        with patch.object(svc, "_connect", boom), \
             patch.object(svc, "push_error_frame", new=AsyncMock(side_effect=lambda f: errs.append(f))):
            await svc._background_connect()

    asyncio.run(run())
    assert errs, "a connect failure should push an ErrorFrame"


# --- L-4: lifecycle hardening -------------------------------------------------

def test_double_start_schedules_connect_only_once():
    svc = _make_service()
    grandparent = _grandparent(svc)
    futs: list = []

    def fake_create_task(coro, *a, **k):
        f = asyncio.ensure_future(coro)
        futs.append(f)
        return f

    async def noop_bg():
        return None

    async def run():
        with patch.object(grandparent, "start", new_callable=AsyncMock), \
             patch.object(svc, "_background_connect", noop_bg), \
             patch.object(svc, "create_task", side_effect=fake_create_task):
            await svc.start(MagicMock())
            await svc.start(MagicMock())   # second start must NOT schedule again
            assert len(futs) == 1
        for f in futs:
            await f

    asyncio.run(run())


def test_cancel_deferred_connect_cancels_pending_task():
    svc = _make_service()

    async def run():
        async def slow():
            await asyncio.Event().wait()  # never completes on its own

        task = asyncio.ensure_future(slow())
        svc._deferred_connect_task = task

        async def fake_cancel_task(t, timeout=None):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        with patch.object(svc, "cancel_task", fake_cancel_task):
            await svc._cancel_deferred_connect()
        assert task.cancelled()
        assert svc._deferred_connect_task is None

    asyncio.run(run())


def test_background_connect_cancelled_does_not_push_error_frame():
    svc = _make_service()
    errs: list = []

    async def cancelled_connect():
        raise asyncio.CancelledError()

    async def run():
        with patch.object(svc, "_connect", cancelled_connect), \
             patch.object(svc, "push_error_frame", new=AsyncMock(side_effect=lambda f: errs.append(f))):
            with pytest.raises(asyncio.CancelledError):
                await svc._background_connect()

    asyncio.run(run())
    assert errs == [], "intentional cancellation must not surface an ErrorFrame"


def test_stop_cancels_pending_deferred_connect():
    svc = _make_service()

    async def run():
        with patch.object(svc, "_cancel_deferred_connect", new_callable=AsyncMock) as cdc, \
             patch.object(DeepgramFluxSTTBase, "stop", new_callable=AsyncMock):
            await svc.stop(MagicMock())
            cdc.assert_awaited_once()

    asyncio.run(run())


def test_audio_before_connect_is_dropped_and_warned_once():
    svc = _make_service()
    assert svc._websocket is None          # not connected yet
    assert svc._warned_early_audio is False

    async def drain(audio=b"\x01\x00"):
        return [f async for f in svc.run_stt(audio)]

    assert asyncio.run(drain(b"\x00\x00")) == []
    assert svc._warned_early_audio is False  # Muted WebRTC silence is expected.
    assert asyncio.run(drain()) == []       # base drops audio when not connected
    assert svc._warned_early_audio is True  # warned exactly once
    # A second early frame must not raise and must not reset the warning flag.
    assert asyncio.run(drain()) == []
    assert svc._warned_early_audio is True
