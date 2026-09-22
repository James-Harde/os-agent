"""欢迎区应为四种场景路由各提供一个可执行示例。"""

from html.parser import HTMLParser
from pathlib import Path


class _ExampleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.examples: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        if tag == "button" and "example" in classes:
            self.examples.append({
                "route": values.get("data-route") or "",
                "prompt": values.get("data-prompt") or "",
            })


def test_welcome_examples_cover_all_routes_with_real_prompts():
    html = (Path(__file__).parents[1] / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    parser = _ExampleParser()
    parser.feed(html)

    assert {item["route"] for item in parser.examples} == {
        "consult", "readonly_diagnosis", "knowledge", "mutation",
    }
    assert all(item["prompt"] for item in parser.examples)
    assert "input.value = btn.dataset.prompt" in html
