"""Call tracing must observe execution without changing its contract."""

import asyncio
import ast
import inspect
import logging
from pathlib import Path
import re
from unittest.mock import patch

import pytest

from backend.bl_haos.call_trace import trace_call, trace_scope


def _messages(caplog):
    return [record.getMessage() for record in caplog.records if "call " in record.getMessage()]


def test_disabled_tracing_preserves_values_without_creating_spans(caplog):
    @trace_call
    def identity(value: object) -> object:
        return value

    value = object()
    with caplog.at_level(logging.INFO), patch(
        "backend.bl_haos.call_trace.trace_scope", side_effect=AssertionError("Tracing is disabled")
    ):
        assert identity(value) is value
    assert not _messages(caplog)


def test_nested_calls_have_parent_spans_and_elapsed_time(caplog):
    @trace_call
    def child(value: int) -> int:
        return value + 1

    @trace_call
    def parent(value: int) -> int:
        return child(value)

    with caplog.at_level(logging.DEBUG):
        assert parent(4) == 5

    messages = _messages(caplog)
    assert len(messages) == 4
    root = re.search(r"span=(\d+) parent=none", messages[0])
    assert root
    assert f"parent={root[1]}" in messages[1]
    assert "call exit" in messages[2]
    assert "call exit" in messages[3]
    assert all("elapsed_ms=" in message for message in messages[2:])


def test_arguments_results_and_exception_details_are_not_logged(caplog):
    secret = "private-token-and-signed-media-url"

    class Private:
        def __repr__(self):
            raise AssertionError("Tracing must not inspect values")

    @trace_call
    def identity(value: object, *, token: str) -> object:
        return value

    error = ValueError(secret)

    @trace_call
    def fail():
        raise error

    value = Private()
    with caplog.at_level(logging.DEBUG):
        assert identity(value, token=secret) is value
        with pytest.raises(ValueError) as raised:
            fail()

    assert raised.value is error
    assert secret not in caplog.text
    assert "error_type=ValueError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_async_calls_remain_coroutines_and_trace_execution_not_creation(caplog):
    @trace_call
    async def identity(value: object) -> object:
        """Keep the callable's public metadata."""
        await asyncio.sleep(0)
        return value

    assert inspect.iscoroutinefunction(identity)
    assert inspect.signature(identity) == inspect.signature(identity.__wrapped__)
    assert identity.__doc__ == "Keep the callable's public metadata."
    value = object()
    with caplog.at_level(logging.DEBUG):
        pending = identity(value)
        assert not _messages(caplog)
        assert await pending is value
    assert len(_messages(caplog)) == 2


@pytest.mark.asyncio
async def test_async_cancellation_is_propagated_and_context_is_reset(caplog):
    ready = asyncio.Event()

    @trace_call
    async def wait():
        ready.set()
        await asyncio.Future()

    @trace_call
    def next_call():
        return None

    with caplog.at_level(logging.DEBUG):
        task = asyncio.create_task(wait())
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        next_call()

    messages = _messages(caplog)
    assert any("call cancel" in message for message in messages)
    assert "parent=none" in messages[-2]
    assert not any("call error" in message for message in messages)


@pytest.mark.asyncio
async def test_concurrent_tasks_do_not_share_unrelated_parent_spans(caplog):
    @trace_call
    def child():
        return None

    @trace_call
    async def parent():
        await asyncio.sleep(0)
        child()

    with caplog.at_level(logging.DEBUG):
        await asyncio.gather(parent(), parent())

    entries = [message for message in _messages(caplog) if "call enter" in message]
    roots = [re.search(r"span=(\d+) parent=none", message)[1] for message in entries[:2]]
    assert len(set(roots)) == 2
    assert f"parent={roots[0]}" in entries[2]
    assert f"parent={roots[1]}" in entries[3]


def test_descriptors_signatures_and_exception_context_are_preserved(caplog):
    class Example:
        @classmethod
        @trace_call
        def create(cls, value: int = 3) -> int:
            return value

        @staticmethod
        @trace_call
        def convert(value: str) -> int:
            return int(value)

        @property
        @trace_call
        def value(self) -> int:
            return 7

    with caplog.at_level(logging.DEBUG):
        assert Example.create() == 3
        assert Example.convert("4") == 4
        assert Example().value == 7
        with pytest.raises(RuntimeError):
            with trace_scope(logging.getLogger(__name__), "lifespan"):
                raise RuntimeError("private exception detail")
        Example.create()

    assert str(inspect.signature(Example.create)) == "(value: int = 3) -> int"
    assert "private exception detail" not in caplog.text
    assert "parent=none" in _messages(caplog)[-2]


def test_every_shipped_function_has_call_tracing():
    root = Path(__file__).resolve().parents[1] / "backend" / "bl_haos"
    missing = []
    for path in root.rglob("*.py"):
        if path.name == "call_trace.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(isinstance(item, ast.Name) and item.id == "trace_call" for item in node.decorator_list):
                continue
            if node.name == "lifespan" and any(
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Name)
                and item.func.id == "trace_scope"
                for item in ast.walk(node)
            ):
                continue
            missing.append(f"{path.relative_to(root)}:{node.lineno}: {node.name}")
    assert not missing, "\n".join(missing)
