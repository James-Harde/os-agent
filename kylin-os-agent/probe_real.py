import asyncio, tempfile
from pathlib import Path
from langchain_core.callbacks import AsyncCallbackHandler
import os
os.environ["APP_V4_USE_FAKE_MODEL"]="true"
os.environ["APP_V4_DISABLE_RATE_LIMIT"]="true"
from app_v4.settings import Settings
from app_v4.container import build_dependencies

class Recorder(AsyncCallbackHandler):
    def __init__(self):
        super().__init__()
        self.run_inline = False
        self.tokens = []
        self.stream_events = []
    async def on_llm_new_token(self, token, **kw):
        self.tokens.append(token)
    async def on_stream_event(self, event, **kw):
        self.stream_events.append(event)

async def main():
    tmp = Path(tempfile.mkdtemp())
    deps = build_dependencies(Settings(use_fake_model=True, rate_limit_enabled=False, db_path=str(tmp/"a.db"), kill_switch=False))
    graph = await deps.get_async_graph()
    rec = Recorder()
    config = {"configurable":{"thread_id":"pr1"}, "callbacks": [rec]}
    print("=== REAL graph v2 astream(messages,updates) + custom callback ===")
    evts = []
    async for event in graph.astream(
        {"messages":[{"role":"user","content":"分析磁盘"}], "thread_id":"pr1"},
        config=config, version="v2", stream_mode=["messages","updates"]):
        evts.append(event)
    print("total events:", len(evts))
    print("on_llm_new_token count:", len(rec.tokens))
    print("on_stream_event count:", len(rec.stream_events))
    # show first few event structures
    for e in evts[:5]:
        print("  evt:", repr(e)[:100])
    await deps.aclose()

asyncio.run(main())
