"""Presentation regressions: stable form/media bindings and candidate separation."""

from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape


class PageElements(HTMLParser):
    """Collect controls and media ancestry without adding an HTML dependency."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.controls: dict[str, dict[str, str | None]] = {}
        self.links: list[str | None] = []
        self.media_in_details: list[str] = []
        self.details_depth = 0
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "details":
            self.details_depth += 1
        if tag in {"input", "select", "textarea", "button"}:
            key = values.get("name") or values.get("id")
            if key:
                self.controls[key] = values
        if tag == "a":
            self.links.append(values.get("href"))
        if tag in {"video", "audio"} and self.details_depth:
            self.media_in_details.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "details":
            self.details_depth -= 1


def render(name: str, **context: object) -> str:
    """Render actual templates without a running application or provider calls."""
    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    env = Environment(loader=FileSystemLoader(templates), autoescape=select_autoescape())
    request = SimpleNamespace(url=SimpleNamespace(path="/interviews"))
    return env.get_template(name).render(request=request, **context)


def test_builder_keeps_all_existing_submission_fields_and_advanced_editor() -> None:
    page = PageElements(render("interviews.html"))
    fields = {"title", "description", "skills", "experience", "duration_minutes", "language", "topics", "criteria", "resume",
        "phone_enabled", "phone_fps", "phone_confidence", "phone_sustain_secs", "phone_cooldown_secs", "fps", "confidence",
        "absent_secs", "multiple_secs", "displacement_secs", "displacement_ratio", "cooldown_secs"}
    assert fields <= page.controls.keys()
    assert len(page.ids) == len(set(page.ids))
    assert {"plan-editor", "review-plan", "activate-plan", "copy-plan", "save-review"} <= page.controls.keys()
    assert "checked" not in page.controls["phone_enabled"]
    assert {"step-role", "step-questions", "step-share"} <= page.controls.keys()
    assert all(page.controls[key]["type"] == "button" for key in ("step-role", "step-questions", "step-share"))


def test_candidate_retains_unique_media_controls_unchecked_consent_and_private_boundary() -> None:
    html = render("candidate.html", candidate_mode=True, session_id="test-session")
    page = PageElements(html)
    assert len(page.ids) == len(set(page.ids))
    assert {"camera-preview", "bot-audio", "transcript", "status", "current-question", "monitoring-status"} <= set(page.ids)
    assert {"start", "mute", "end", "abandon", "check-mic", "check-speaker", "preview-camera", "ready-interview"} <= page.controls.keys()
    assert "checked" not in page.controls["camera-consent"]
    assert "disabled" in page.controls["start"]
    assert page.media_in_details == []
    assert not {"/interviews", "/config", "/calls"} & set(page.links)
    assert "plan-editor" not in page.ids
    assert "Camera frames are processed locally and are not recorded or uploaded." in html
    assert "Voice processing uses external providers." in html
