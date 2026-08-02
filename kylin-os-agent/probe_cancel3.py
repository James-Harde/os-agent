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
    def model_post_init(self, __context):
        object.__setattr__(self, "_produced", 0)
        object.__setattr__(self, "_cancelled", False)
    @property
    def _llm_type(self):
        return "slow-probe"
    def _generate(self, messages, **kw):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])
    async def _astream(self, messages, **kw):
        try:
            for i in range(1000):
                await asyncio.sleep(0.05)
                object.__setattr__(self, "_produced", self._produced + 1)
                yield ChatGenerationChunk(message=AIMessageChunk(content="t%d " % i))
        except asyncio.CancelledError:
            object.__setattr__(self, "_cancelled", True)
            raise

async def main():
    tmp = Path(tempfile.mkdtemp())
    deps = build_dependencies(Settings(use_fake_model=True, rate_limit_enabled=False, db_path=str(tmp/"a.db"), kill_switch=False))
    m = SlowModel()
    deps._model = m
    graph = await deps.get_async_graph()
    config = {"configurable":{"thread_id":"c1"}}
    print("=== Test: aclose() after model produces -> cancel propagation ===")
    agraph = graph.astream({"messages":[{"role":"user","content":"分析磁盘"}], "thread_id":"c1"},
                           config=config, version="v2", stream_mode=["messages","updates"])
    loop = asyncio.get_running_loop()
    async def watcher():
        # wait until model produced >=3, then close the generator
        while m._produced < 3:
            await asyncio.sleep(0.02)
        print("model produced=%d, calling aclose" % m._produced)
        await agraph.aclose()
    task = loop.create_task(watcher())
    got = 0
    try:
        async for event in agraph:
            got += 1
    except (GeneratorExit, asyncio.CancelledError):
        pass
    await asyncio.sleep(0.6)
    print("model produced=%d cancelled=%s total_events=%d" % (m._produced, m._cancelled, got))
    await deps.aclose()

asyncio.run(main())
