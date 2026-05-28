"""Tests for hook connection amplification during agent check-ins and
task result processing.

When handle_agent_request or _process_agent_packet runs hooks inside the
DB session block, each async hook opens a SECOND pool connection via
_run_async_hook while the caller still holds connection #1.  With
pool_size=1, max_overflow=0, this causes an immediate QueuePool timeout
— the exact production failure mode where agent activity exhausts the
pool under load.

The fix: move hooks.run_hooks() outside the ``with SessionLocal.begin()``
block so that connection #1 is released before the hook acquires
connection #2.
"""

from __future__ import annotations

import contextlib
import logging

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import empire.server.core.hooks as hooks_module
from empire.server.core.db.base import engine as original_engine
from empire.server.core.hooks import hooks as hooks_instance

log = logging.getLogger(__name__)

HOOK_NAME = "test_hook_connection_amplification"


@contextlib.contextmanager
def _registered_hook(event, hook):
    hooks_instance.register_hook(event, HOOK_NAME, hook)
    try:
        yield
    finally:
        hooks_instance.unregister_hook(HOOK_NAME, event)


@pytest.fixture
def constrained_engine():
    """A single-connection pool to expose connection amplification."""
    connect_args = (
        {"check_same_thread": False} if "sqlite" in str(original_engine.url) else {}
    )
    engine = create_engine(
        original_engine.url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=2,
        connect_args=connect_args,
    )
    yield engine
    engine.dispose()


def test_callback_hook_runs_when_not_competing_for_connections(
    session_local, constrained_engine, monkeypatch
):
    """Baseline: an async callback hook succeeds when it doesn't have to
    compete for a pool connection (hooks fire after session closes).

    This is the desired behavior after the fix.
    """
    CS = sessionmaker(bind=constrained_engine)
    monkeypatch.setattr(hooks_module, "SessionLocal", CS)

    hook_ran = False

    async def _callback_hook(db, agent_id):
        nonlocal hook_ran
        db.execute(text("SELECT 1"))
        hook_ran = True

    # Session closes FIRST, then hooks fire — no connection competition
    with CS.begin() as db:
        db.execute(text("SELECT 1"))

    dummy = CS()
    dummy.close()

    with _registered_hook(hooks_instance.AFTER_AGENT_CALLBACK_HOOK, _callback_hook):
        hooks_instance.run_hooks(
            hooks_instance.AFTER_AGENT_CALLBACK_HOOK, dummy, "fake-agent-id"
        )

    assert hook_ran, "Hook should run when not competing for connections"


def test_callback_hook_fails_when_called_inside_session_block(
    session_local, constrained_engine, monkeypatch
):
    """An async callback hook FAILS (silently) when called inside an open
    session block with pool_size=1.

    This is a characterization test proving the production bug exists:
    run_hooks() catches the QueuePool timeout internally, so the hook
    never runs but no exception reaches the caller.
    """
    CS = sessionmaker(bind=constrained_engine)
    monkeypatch.setattr(hooks_module, "SessionLocal", CS)

    hook_ran = False

    async def _callback_hook(db, agent_id):
        nonlocal hook_ran
        db.execute(text("SELECT 1"))
        hook_ran = True

    with _registered_hook(hooks_instance.AFTER_AGENT_CALLBACK_HOOK, _callback_hook):  # noqa: SIM117
        with CS.begin() as db:
            db.execute(text("SELECT 1"))  # holds connection #1
            hooks_instance.run_hooks(
                hooks_instance.AFTER_AGENT_CALLBACK_HOOK, db, "fake-agent-id"
            )

    # Bug: the hook silently failed due to pool timeout
    assert not hook_ran, (
        "Expected the hook to fail (pool timeout) when called inside a "
        "session block with pool_size=1 — the bug should be present on "
        "unpatched code."
    )


def test_handle_agent_request_callback_hook_runs_with_constrained_pool(
    models, agent, session_local, main, constrained_engine, monkeypatch
):
    """handle_agent_request must allow callback hooks to run even with a
    single-connection pool.

    This is the key TDD test: after the fix, hooks fire outside the
    session block, so they can acquire the (now released) connection.
    Currently fails because hooks fire inside the session block.
    """
    CS = sessionmaker(bind=constrained_engine)
    monkeypatch.setattr(hooks_module, "SessionLocal", CS)

    import empire.server.core.agent_communication_service as acs_module  # noqa: PLC0415

    monkeypatch.setattr(acs_module, "SessionLocal", CS)

    hook_ran = False

    async def _callback_hook(db, *args):
        nonlocal hook_ran
        # Must do DB work to force a real pool connection checkout.
        # Without this, SQLAlchemy's lazy connection means the fresh
        # session in _run_async_hook never actually hits the pool.
        if isinstance(db, Session):
            db.execute(text("SELECT 1"))
        hook_ran = True

    # Ensure the agent has a hostname so the callback hook branch fires
    with session_local.begin() as db:
        agent_obj = db.query(models.Agent).filter_by(session_id=agent).first()
        if not agent_obj.hostname:
            agent_obj.hostname = "test-host"

    with _registered_hook(hooks_instance.AFTER_AGENT_CALLBACK_HOOK, _callback_hook):
        main.agentcommsv2.handle_agent_request(
            agent, "powershell", b"staging_key_placeholder"
        )

    assert hook_ran, (
        "AFTER_AGENT_CALLBACK_HOOK did not fire during handle_agent_request "
        "with a constrained pool.  Either the hook is called inside the "
        "session block (pool timeout) or the agent's hostname is not set."
    )


def _create_task(main, session_local, models, agent_id):
    """Create a shell task for an agent."""
    with session_local.begin() as db:
        agent_obj = db.query(models.Agent).filter_by(session_id=agent_id).first()
        task, err = main.agenttasksv2.create_task_shell(db, agent_obj, "echo test")
        assert task is not None, f"Failed to create task: {err}"
        return task.id


def test_process_agent_packet_returns_tasking_for_external_hook_dispatch(
    models, agent, session_local, main
):
    """_process_agent_packet must return the tasking object so that
    _handle_agent_response can fire AFTER_TASKING_RESULT_HOOK outside
    the session block.

    Previously _process_agent_packet fired the hook internally (inside
    the caller's session) and returned None.  This caused 2x connection
    usage because _run_async_hook opens a fresh session while the
    caller still holds connection #1.

    After the fix, the method returns the tasking and does NOT fire
    AFTER_TASKING_RESULT_HOOK itself — the caller fires it after
    closing the session.
    """
    task_id = _create_task(main, session_local, models, agent)

    with session_local.begin() as db:
        result = main.agentcommsv2._process_agent_packet(
            db, agent, "TASK_SHELL", task_id, b"test output"
        )

    assert result is not None, (
        "_process_agent_packet returned None instead of the tasking object. "
        "It must return the tasking so _handle_agent_response can fire "
        "AFTER_TASKING_RESULT_HOOK outside the session block."
    )


def test_tasking_result_hook_not_fired_inside_process_agent_packet(
    models, agent, session_local, main
):
    """_process_agent_packet must NOT fire AFTER_TASKING_RESULT_HOOK itself.

    The hook must be fired by the caller (_handle_agent_response) after
    the session block closes, to avoid holding two pool connections
    simultaneously.
    """
    hook_fired_inside = False

    async def _spy_hook(db, tasking):
        nonlocal hook_fired_inside
        hook_fired_inside = True

    task_id = _create_task(main, session_local, models, agent)

    with (
        _registered_hook(hooks_instance.AFTER_TASKING_RESULT_HOOK, _spy_hook),
        session_local.begin() as db,
    ):
        main.agentcommsv2._process_agent_packet(
            db, agent, "TASK_SHELL", task_id, b"test output"
        )

    assert not hook_fired_inside, (
        "AFTER_TASKING_RESULT_HOOK fired inside _process_agent_packet. "
        "It must be fired by the caller after the session closes to avoid "
        "holding two pool connections simultaneously."
    )


def test_agent_checkin_hook_uses_none_session_convention(
    session_local, constrained_engine, monkeypatch
):
    """AFTER_AGENT_CHECKIN_HOOK must use the None session convention so
    _run_async_hook provides a fresh session without competing for the
    caller's connection.

    With pool_size=1, if the caller passes its live db session, the
    hook's _run_async_hook opens a second connection and times out.
    Passing None lets _run_async_hook use the single connection after
    the caller releases it (or independently if no caller holds one).
    """
    CS = sessionmaker(bind=constrained_engine)
    monkeypatch.setattr(hooks_module, "SessionLocal", CS)

    hook_ran = False

    async def _checkin_hook(db, agent_obj):
        nonlocal hook_ran
        if isinstance(db, Session):
            db.execute(text("SELECT 1"))
        hook_ran = True

    # Simulate the FIXED pattern: pass None so _run_async_hook provides
    # a fresh session without competing for a connection.
    with _registered_hook(hooks_instance.AFTER_AGENT_CHECKIN_HOOK, _checkin_hook):
        hooks_instance.run_hooks(
            hooks_instance.AFTER_AGENT_CHECKIN_HOOK, None, "fake-agent"
        )

    assert hook_ran, (
        "AFTER_AGENT_CHECKIN_HOOK did not run with the None session "
        "convention.  _run_async_hook should provide a fresh session."
    )


def test_after_tasking_hook_uses_none_session_in_add_task(
    models, agent, session_local, main, constrained_engine, monkeypatch
):
    """AFTER_TASKING_HOOK in add_task must use the None session convention.

    add_task is called from FastAPI handlers inside a get_db() session.
    If it passes the caller's db to the hook, _run_async_hook opens a
    second connection while the caller holds the first — 2x amplification.

    Using None lets _run_async_hook provide a fresh session independently.
    With pool_size=1, the hook should still fire because _run_async_hook
    manages its own connection lifecycle.
    """
    CS = sessionmaker(bind=constrained_engine)
    monkeypatch.setattr(hooks_module, "SessionLocal", CS)

    hook_ran = False

    async def _tasking_hook(db, task):
        nonlocal hook_ran
        if isinstance(db, Session):
            db.execute(text("SELECT 1"))
        hook_ran = True

    with (
        _registered_hook(hooks_instance.AFTER_TASKING_HOOK, _tasking_hook),
        session_local.begin() as db,
    ):
        agent_obj = db.query(models.Agent).filter_by(session_id=agent).first()
        main.agenttasksv2.create_task_shell(db, agent_obj, "echo tdd-test")

    assert hook_ran, (
        "AFTER_TASKING_HOOK did not fire during add_task with a constrained "
        "pool.  add_task must use the None session convention so "
        "_run_async_hook provides a fresh session without competing for "
        "the caller's connection."
    )
