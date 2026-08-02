import asyncio, tempfile
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

class SlowModel(BaseChatModel):
    def model_post_init(self, __context):
        object.__setattr__(self, "_produced", 0)
    @property
    def _llm_type(self):
        return "slow-probe"
    def _generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])
    async def _astream(self, messages, **kw):
        for i in range(50):
            await asyncio.sleep(0.02)
            object.__setattr__(self, "_produced", self._produced + 1)
            yield ChatGenerationChunk(message=AIMessageChunk(content="t%d " % i))

class Recorder(AsyncCallbackHandler):
    def __init__(self):
        super().__init__()
        self.run_inline = False
        self.tokens = []
    async def on_llm_new_token(self, token, **kw):
        self.tokens.append(token)

async def main():
    tmp = Path(tempfile.mkdtemp())
    deps = build_dependencies(Settings(use_fake_model=True, rate_limit_enabled=False, db_path=str(tmp/"a.db"), kill_switch=False))
    m = SlowModel()
    deps._model = m
    rec = Recorder()
    # Direct ainvoke WITH callbacks config
    import langchain_core.messages as mm
    resp = await m.ainvoke(
        [mm.HumanMessage(content="hi")],
        config={"callbacks": [rec]}
    )
    print("ainvoke with callbacks -> tokens received:", len(rec.tokens), "model produced:", m._produced)
    await deps.aclose()

asyncio.run(main())
