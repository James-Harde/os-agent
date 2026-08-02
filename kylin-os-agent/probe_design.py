import asyncio, tempfile, time
from pathlib import Path
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk
import os
os.environ["APP_V4_USE_FAKE_MODEL"]="true"
os.environ["APP_V4_DISABLE_RATE_LIMIT"]="true"
from app_v4.settings import Settings
from app_v4.container import build_dependencies

class ProbeModel(BaseChatModel):
    def model_post_init(self, __context):
        object.__setattr__(self, "_produced", 0)
        object.__setattr__(self, "_cancelled", False)
    @property
    def _llm_type(self): return "probe"
    def _generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])
    async def _astream(self, messages, **kw):
        try:
            for i in range(1000):
                await asyncio.sleep(0.02)
                object.__setattr__(self, "_produced", self._produced + 1)
                yield ChatGenerationChunk(message=AIMessageChunk(content="t%d " % i))
        except asyncio.CancelledError:
            object.__setattr__(self, "_cancelled", True)
            raise

class BPHandler(AsyncCallbackHandler):
    def __init__(self, maxsize=2):
        super().__init__()
        self.run_inline = False
        self.queue = asyncio.Queue(maxsize=maxsize)
    async def on_llm_new_token(self, token, **kw):
        await self.queue.put(token)  # blocks when full -> backpressure

async def run_backpressure():
    tmp = Path(tempfile.mkdtemp())
    deps = build_dependencies(Settings(use_fake_model=True, rate_limit_enabled=False, db_path=str(tmp/"a.db"), kill_switch=False))
    m = ProbeModel(); deps._model = m
    graph = await deps.get_async_graph()
    handler = BPHandler(maxsize=2)
    config = {"configurable":{"thread_id":"bp"}, "callbacks":[handler]}
    print("=== BACKPRESSURE TEST ===")
    stream = graph.astream({"messages":[{"role":"user","content":"分析磁盘"}], "thread_id":"bp"},
                           config=config, version="v2", stream_mode=["messages","updates"])
    plateau = None
    after_pause = None
    try:
        async for event in stream:
            if event["type"] == "messages":
                chunk, meta = event["data"]
                txt = getattr(chunk, "content", "")
                if txt:
                    # drain queue to release backpressure
                    try:
                        handler.queue.get_nowait()
                    except Exception:
                        pass
                    if plateau is None:
                        # first token: now pause consumer
                        await asyncio.sleep(0.4)
                        plateau = m._produced
                        await asyncio.sleep(0.3)
                        after_pause = m._produced
                        print("plateau=%d after_pause=%d" % (plateau, after_pause))
                        break
    finally:
        await stream.aclose()
    await asyncio.sleep(0.3)
    print("FINAL produced=%d cancelled=%s" % (m._produced, m._cancelled))
    await deps.aclose()

asyncio.run(run_backpressure())
