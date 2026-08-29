"""A small TwiML builder.

Hand-rolled rather than pulled from the Twilio SDK, which would add `requests`,
`PyJWT` and friends for what is a few hundred bytes of XML. The one thing that
absolutely must be right is **escaping**: `<Say>` carries model output and
pilgrim speech, and an unescaped `&` or `<` produces XML Twilio rejects — the
caller then hears silence, on a line they may have dialled in an emergency.
Every value goes through `xml.sax.saxutils`, and there are tests for it.

Voices are Indian-accented: Polly.Aditi is Amazon's bilingual Hindi/English
voice, and Marathi has no Polly voice at all, so it uses Google's mr-IN.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

# language → (Twilio voice, spoken language tag)
VOICES: dict[str, tuple[str, str]] = {
    "mr": ("Google.mr-IN-Standard-A", "mr-IN"),
    "hi": ("Polly.Aditi", "hi-IN"),
    "en": ("Polly.Aditi", "en-IN"),
}
DEFAULT_LANGUAGE = "en"

# What Twilio's speech recogniser should expect back.
SPEECH_LANGUAGES: dict[str, str] = {"mr": "mr-IN", "hi": "hi-IN", "en": "en-IN"}


def voice_for(language: str) -> tuple[str, str]:
    return VOICES.get(language, VOICES[DEFAULT_LANGUAGE])


def _attrs(pairs: dict[str, object]) -> str:
    """Render attributes, skipping None. Values are quoted and escaped."""
    rendered = "".join(
        f" {name}={quoteattr(str(value))}"
        for name, value in pairs.items()
        if value is not None
    )
    return rendered


class Element:
    def __init__(self, tag: str, text: str | None = None, **attrs: object) -> None:
        self.tag = tag
        self.text = text
        self.attrs = attrs
        self.children: list[Element] = []

    def add(self, child: Element) -> Element:
        self.children.append(child)
        return child

    def render(self) -> str:
        opening = f"<{self.tag}{_attrs(self.attrs)}"
        if not self.children and self.text is None:
            return opening + "/>"

        body = escape(self.text) if self.text else ""
        body += "".join(child.render() for child in self.children)
        return f"{opening}>{body}</{self.tag}>"


class VoiceResponse:
    """Builds a `<Response>` document."""

    def __init__(self) -> None:
        self.children: list[Element] = []

    # --- verbs -------------------------------------------------------------

    def say(self, text: str, language: str = DEFAULT_LANGUAGE) -> VoiceResponse:
        voice, spoken = voice_for(language)
        self.children.append(Element("Say", text, voice=voice, language=spoken))
        return self

    def play(self, url: str, loop: int | None = None) -> VoiceResponse:
        self.children.append(Element("Play", url, loop=loop))
        return self

    def pause(self, seconds: int = 1) -> VoiceResponse:
        self.children.append(Element("Pause", length=seconds))
        return self

    def redirect(self, url: str) -> VoiceResponse:
        self.children.append(Element("Redirect", url, method="POST"))
        return self

    def hangup(self) -> VoiceResponse:
        self.children.append(Element("Hangup"))
        return self

    def gather(
        self,
        action: str,
        *,
        input_types: str = "speech dtmf",
        num_digits: int | None = None,
        speech_timeout: int | str | None = None,
        timeout: int | None = None,
        language: str = DEFAULT_LANGUAGE,
        hints: str | None = None,
    ) -> Gather:
        element = Element(
            "Gather",
            action=action,
            method="POST",
            input=input_types,
            numDigits=num_digits,
            speechTimeout=speech_timeout,
            timeout=timeout,
            language=SPEECH_LANGUAGES.get(language, "en-IN"),
            hints=hints,
            actionOnEmptyResult="true",
        )
        self.children.append(element)
        return Gather(element)

    # --- output ------------------------------------------------------------

    def to_xml(self) -> str:
        body = "".join(child.render() for child in self.children)
        return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


class Gather:
    """Prompts nested inside a `<Gather>` — spoken while input is awaited."""

    def __init__(self, element: Element) -> None:
        self._element = element

    def say(self, text: str, language: str = DEFAULT_LANGUAGE) -> Gather:
        voice, spoken = voice_for(language)
        self._element.add(Element("Say", text, voice=voice, language=spoken))
        return self

    def play(self, url: str) -> Gather:
        self._element.add(Element("Play", url))
        return self

    def pause(self, seconds: int = 1) -> Gather:
        self._element.add(Element("Pause", length=seconds))
        return self
