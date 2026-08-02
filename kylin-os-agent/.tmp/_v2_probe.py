"""Temporary inline probe: validate v2 bounded-queue backpressure + cancel.

Deleted after validation. Not a production file.
"""
import asyncio
import tempfile
from pathlib import Path
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import PrivateAttr

from app_v4.container import build_dependencies, set_deps, reset_deps
from app_v4.settings import Settings


class _ProbeModel(BaseChatModel):
    _total: int = PrivateAttr()
    _state: dict = PrivateAttr()

    def __init__(self, total: int, state: dict) -> None:
        super().__init__()
        self._total = total
        self._state = state

    @property
    def _llm_type(self) -> str:
        return "v2-probe"

    def _generate(self, messages, **kwargs) -> ChatResult:
        self._state["sync_calls"] += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="sync"))])

    async def _astream(self, messages, **kwargs):
        self._state["started"] += 1
        try:
            for i in range(self._total):
                await asyncio.sleep(0)
                self._state["produced"] += 1
                yield ChatGenerationChunk(message=AIMessageChunk(content=f"T{i}"))
        except asyncio.CancelledError:
            self._state["cancelled"] += 1
            raise
        finally:
            self._state["finalized"] += True
        self._state["completed"] += 1


class _Handler(AsyncCallbackHandler):
    def __init__(self, q: "asyncio.Queue[str | None]") -> None:
        super().__init__()
        self.q = q

    async def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        await self.q.put(token)  # blocks when full -> backpressure


def _deps(tmp: Path, name: str, model):
    deps = build_dependencies(Settings(use_fake_model=True, rate_limit_enabled=False,
                                    db_path=str(tmp / f"{name}.db"), kill_switch=False))
    deps._model = model
    return deps


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())

    # ---- backpressure ----
    state = {"started": 0, "produced": 0, "cancelled": 0, "finalized": 0, "completed": 0, "sync_calls": 0}
    model = _ProbeModel(80, state)
    deps = _deps(tmp, "a", model)
    token_q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=4)
    handler = _Handler(token_q)
    graph = await deps.get_async_graph()
    config = {"configurable": {"thread_id": "probe"}, "callbacks": [handler]}
    initial = {"messages": [HumanMessage(content="分析磁盘")], "trace_steps": []}

    driver_err: list[BaseException] = []
    deps_token = set_deps(deps)

    async def drive() -> None:
        try:
            async for chunk in graph.astream(initial, config, version="v2",
                                             stream_mode=["messages", "updates"]):
                pass
        except BaseException as e:
            driver_err.append(e)
            raise
        finally:
            with __import__("contextlib").suppress(Exception):
                await token_q.put(None)

    driver = asyncio.create_task(drive())
    tok = await asyncio.wait_for(token_q.get(), timeout=6)
    print(f"first token={tok} produced={state['produced']} state={state} driver_err={driver_err}")
    if driver_err:
        raise driver_err[0]
    assert tok == "T0", f"expected T0 got {tok}"
    await asyncio.sleep(0.3)
    snapshot = state["produced"]
    print(f"after pause: produced={snapshot}")
    n = 1
    while True:
        t = await asyncio.wait_for(token_q.get(), timeout=6)
        if t is None:
            break
        n += 1
    await asyncio.wait_for(driver, timeout=6)
    reset_deps(deps_token)
    print(f"RES backpressure produced={state['produced']} drained={n} state={state} driver_err={driver_err}")
    # two LLM nodes (route + decide) each emit the same token count
    assert state["completed"] == 2, "two LLM nodes complete"
    assert state["produced"] % 80 == 0 and state["produced"] >= 80, "tokens produced"
    assert 1 <= snapshot < state["produced"], f"backpressure failed: snapshot={snapshot}"
    print("BACKPRESSURE_OK")

    # ---- cancellation ----
    state2 = {"started": 0, "produced": 0, "cancelled": 0, "finalized": 0, "completed": 0, "sync_calls": 0}
    model2 = _ProbeModel(1000, state2)
    deps2 = _deps(tmp, "b", model2)
    q2: asyncio.Queue[str | None] = asyncio.Queue(maxsize=4)
    g2 = await deps2.get_async_graph()
    cfg2 = {"configurable": {"thread_id": "probe2"}, "callbacks": [_Handler(q2)]}
    init2 = {"messages": [HumanMessage(content="分析磁盘")], "trace_steps": []}

    deps2_token = set_deps(deps2)

    async def drive2() -> None:
        try:
            async for _ in g2.astream(init2, cfg2, version="v2",
                                      stream_mode=["messages", "updates"]):
                pass
        except asyncio.CancelledError:
            with __import__("contextlib").suppress(Exception):
                await q2.put(None)
            raise

    driver2 = asyncio.create_task(drive2())
    t = await asyncio.wait_for(q2.get(), timeout=6)
    print(f"cancel-probe first token={t} produced={state2['produced']}")
    assert t == "T0", f"expected T0 got {t}"
    driver2.cancel()
    try:
        await asyncio.wait_for(driver2, timeout=3)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    await asyncio.sleep(0.3)
    reset_deps(deps2_token)
    print(f"RES cancel state2={state2}")
    assert state2["cancelled"] >= 1, "model must receive CancelledError"
    assert state2["finalized"] >= 1, "model finally must run"
    assert state2["completed"] == 0, "model must NOT complete after cancel"
    print("CANCEL_OK")


if __name__ == "__main__":
    asyncio.run(main())
