import asyncio, time, tempfile
from pathlib import Path
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk
import os
os.environ["APP_V4_USE_FAKE_MODEL"]="true"
os.environ["APP_V4_DISABLE_RATE_LIMIT"]="true"
from app_v4.settings import Settings
from app_v4.container import build_dependencies

class SlowModel(BaseChatModel):
    def __init__(self): super().__init__()
    @property
    def _llm_type(self): return "slow-probe"
    def _generate(self, messages, **kw):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])
    async def _astream(self, messages, **kw):
        for i in range(1000):
            await asyncio.sleep(0.1)
            yield ChatGenerationChunk(message=AIMessageChunk(content=f"t{i} "))

async def main():
    tmp = Path(tempfile.mkdtemp())
    deps = build_dependencies(Settings(use_fake_model=True, rate_limit_enabled=False, db_path=str(tmp/"a.db"), kill_switch=False))
    deps._model = SlowModel()
    graph = await deps.get_async_graph()
    config = {"configurable":{"thread_id":"c1"}}
    print("=== Test: does aclose() cancel the model task? ===")
    agraph = graph.astream({"messages":[{"role":"user","content":"分析磁盘"}], "thread_id":"c1"},
                           config=config, version="v2", stream_mode=["messages","updates"])
    got = 0
    t0 = time.monotonic()
    try:
        async for event in agraph:
            got += 1
            if got == 2:
                # Simulate client disconnect: close the generator
                print(f"closing generator after {got} events at {time.monotonic()-t0:.2f}s")
                await agraph.aclose()
                break
    except (GeneratorExit, asyncio.CancelledError) as e:
        print(f"caught {type(e).__name__}")
    await asyncio.sleep(0.5)
    print(f"survived aclose, elapsed {time.monotonic()-t0:.2f}s")
    await deps.aclose()

asyncio.run(main())
