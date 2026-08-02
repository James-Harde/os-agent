"""Diagnostic: mimic exact real-test flow (aclose chain)."""
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
from app_v4.graph.runner import _BackpressureHandler


class _ProbeModel(BaseChatModel):
    _state: dict = PrivateAttr()
    _delay: float = PrivateAttr()

    def __init__(self, state: dict, delay: float) -> None:
        super().__init__()
        self._state = state
        self._delay = delay

    @property
    def _llm_type(self) -> str:
        return "diag-probe"

    def _generate(self, messages, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="sync"))])

    async def _astream(self, messages, **kwargs):
        self._state["started"] += 1
        try:
            for i in range(1000):
                await asyncio.sleep(self._delay)
                self._state["produced"] += 1
                yield ChatGenerationChunk(message=AIMessageChunk(content=f"T{i}"))
        except asyncio.CancelledError:
            self._state["cancelled"] += 1
            raise
        finally:
            self._state["finalized"] += 1
        self._state["completed"] += 1


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    for delay in (0.0, 0.1):
        state = {"started": 0, "produced": 0, "cancelled": 0, "finalized": 0, "completed": 0}
        model = _ProbeModel(state, delay)
        deps = build_dependencies(Settings(use_fake_model=True, rate_limit_enabled=False,
                                        db_path=str(tmp / f"d{delay}.db"), kill_switch=False))
        deps._model = model
        token_q: asyncio.Queue[str] = asyncio.Queue(maxsize=4)
        graph = await deps.get_async_graph()
        config = {"configurable": {"thread_id": f"p{delay}"}, "callbacks": [_BackpressureHandler(token_q)]}
        initial = {"messages": [HumanMessage(content="分析磁盘")], "trace_steps": []}
        deps_token = set_deps(deps)

        driver_err: list[BaseException] = []

        async def drive():
            try:
                async for _ in graph.astream(initial, config, version="v2",
                                             stream_mode=["messages", "updates"]):
                    pass
            except asyncio.CancelledError:
                raise
            except BaseException as e:
                driver_err.append(e)

        driver = asyncio.create_task(drive())

        # mimic event_generator: read 1 token then aclose
        async def event_generator():
            from app_v4.graph.runner import streaming_agent
            agen = streaming_agent("你好 stream-probe-A", deps=deps)
            try:
                async for event in agen:
                    # got one token -> return (mimic client reading 1 token)
                    break
            finally:
                await agen.aclose()

        await asyncio.wait_for(event_generator(), timeout=5)
        await asyncio.sleep(0.3)
        print(f"delay={delay} after aclose+0.3s: state={state}, driver.done={driver.done()}, driver_err={driver_err}")
        reset_deps(deps_token)


if __name__ == "__main__":
    asyncio.run(main())
