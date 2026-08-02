import asyncio, time
from pathlib import Path
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessageChunk
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk

import os
os.environ["APP_V4_USE_FAKE_MODEL"]="true"
os.environ["APP_V4_DISABLE_RATE_LIMIT"]="true"
from app_v4.settings import Settings
from app_v4.container import build_dependencies

class ProbeModel(BaseChatModel):
    def __init__(self): super().__init__()
    @property
    def _llm_type(self): return "probe"
    def _generate(self, messages, **kw):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])
    async def _astream(self, messages, **kw):
        for i in range(50):
            await asyncio.sleep(0.05)
            yield ChatGenerationChunk(message=AIMessageChunk(content=f"t{i} "))

class BPHandler(AsyncCallbackHandler):
    def __init__(self, maxsize=2):
        super().__init__()
        self.run_inline = False
        self.q = asyncio.Queue(maxsize=maxsize)
        self.tokens = []
    async def on_llm_new_token(self, token, **kw):
        await self.q.put(token)
        self.tokens.append(token)

async def main():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    deps = build_dependencies(Settings(use_fake_model=True, rate_limit_enabled=False, db_path=str(tmp/"a.db"), kill_switch=False))
    deps._model = ProbeModel()
    graph = await deps.get_async_graph()
    handler = BPHandler(maxsize=2)
    config = {"configurable":{"thread_id":"probe-1"}, "callbacks":[handler]}
    print("=== v2 astream event format + backpressure probe ===")
    produced_before_pause = None
    agraph = graph.astream({"messages":[{"role":"user","content":"分析磁盘"}], "thread_id":"probe-1"},
                           config=config, version="v2", stream_mode=["messages","updates"])
    i = 0
    start = time.monotonic()
    async for event in agraph:
        print(f"evt[{i}]", repr(event)[:100])
        i += 1
        if i == 3:
            await asyncio.sleep(0.5)
            produced_before_pause = len(handler.tokens)
            print(f"--- after 0.5s pause, tokens produced={produced_before_pause} ---")
    total = len(handler.tokens)
    elapsed = time.monotonic()-start
    print(f"total tokens={total} elapsed={elapsed:.2f}s")
    await deps.aclose()

asyncio.run(main())
