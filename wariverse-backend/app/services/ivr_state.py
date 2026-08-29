"""The in-app IVR menu tree, as a pure state machine.

No I/O and no service calls live here — `next_state()` maps (state, key) to the
next state plus an *action* the router performs. That keeps the whole menu
navigable in tests without a database, a model, or an audio provider, which is
the part most likely to regress when menus are reshuffled.

Prompts are written to be **spoken**: short sentences, no lists, no markup,
digits written as words where a text-to-speech voice would otherwise read them
oddly. This is the same constraint the telephone IVR has, for the same reason —
someone is listening to it while walking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

State = Literal["language", "menu", "sos_confirm", "speech", "ended"]

# The opening menu maps a digit to a language.
LANGUAGE_KEYS: dict[str, str] = {"1": "mr", "2": "hi", "3": "en"}

# Actions the router carries out. The state machine never performs them.
Action = Literal[
    "greet",
    "language_selected",
    "crowd_summary",
    "temple_info",
    "nearby_seva",
    "sos_confirm",
    "sos_dispatch",
    "escalate",
    "enter_speech",
    "replay",
    "invalid",
    "goodbye",
]


@dataclass(frozen=True)
class Option:
    key: str
    label: str


@dataclass
class Transition:
    state: State
    action: Action
    options: list[Option] = field(default_factory=list)
    # Static prompt text. Actions that fetch live data leave this empty and the
    # router fills it in.
    text: str = ""
    ends_session: bool = False


# --- copy -------------------------------------------------------------------

_LANGUAGE_MENU = {
    "mr": "वारीव्हर्समध्ये आपले स्वागत आहे. मराठीसाठी एक दाबा.",
    "hi": "वारीवर्स में आपका स्वागत है। हिंदी के लिए दो दबाएं।",
    "en": "Welcome to WariVerse. For English, press three.",
}

_MENU = {
    "mr": (
        "गर्दीच्या माहितीसाठी एक दाबा. दर्शन आणि टोकनसाठी दोन दाबा. "
        "जवळच्या सुविधा आणि मोफत सेवेसाठी तीन दाबा. "
        "आपत्कालीन मदतीसाठी चार दाबा. बोलून विचारण्यासाठी नऊ दाबा."
    ),
    "hi": (
        "भीड़ की जानकारी के लिए एक दबाएं। दर्शन और टोकन के लिए दो दबाएं। "
        "नज़दीकी सुविधाओं और नि:शुल्क सेवा के लिए तीन दबाएं। "
        "आपात मदद के लिए चार दबाएं। बोलकर पूछने के लिए नौ दबाएं।"
    ),
    "en": (
        "For crowd and queue status, press one. For darshan and token "
        "information, press two. For nearby facilities and free seva, press "
        "three. For emergency help, press four. To speak your question, press nine."
    ),
}

_MENU_OPTIONS = {
    "mr": [
        ("1", "गर्दी आणि रांग"),
        ("2", "दर्शन आणि टोकन"),
        ("3", "जवळच्या सुविधा आणि सेवा"),
        ("4", "आपत्कालीन मदत"),
        ("9", "बोलून विचारा"),
        ("0", "मेनू पुन्हा ऐका"),
    ],
    "hi": [
        ("1", "भीड़ और कतार"),
        ("2", "दर्शन और टोकन"),
        ("3", "नज़दीकी सुविधाएँ और सेवा"),
        ("4", "आपात मदद"),
        ("9", "बोलकर पूछें"),
        ("0", "मेन्यू दोबारा सुनें"),
    ],
    "en": [
        ("1", "Crowd and queue status"),
        ("2", "Darshan and token pass"),
        ("3", "Nearby facilities and seva"),
        ("4", "Emergency help"),
        ("9", "Speak your question"),
        ("0", "Replay this menu"),
    ],
}

_INVALID = {
    "mr": "तो पर्याय उपलब्ध नाही. मेनू पुन्हा ऐकण्यासाठी शून्य दाबा.",
    "hi": "यह विकल्प उपलब्ध नहीं है। मेन्यू दोबारा सुनने के लिए शून्य दबाएं।",
    "en": "That option is not available. Press zero to replay the menu.",
}

_SOS_CONFIRM = {
    "mr": "आपत्कालीन मदत पाठवायची आहे का? होय असल्यास एक दाबा. रद्द करण्यासाठी शून्य दाबा.",
    "hi": "क्या आपात मदद भेजनी है? हाँ के लिए एक दबाएं। रद्द करने के लिए शून्य दबाएं।",
    "en": "Do you want emergency help sent? Press one to confirm. Press zero to cancel.",
}

_SOS_CANCELLED = {
    "mr": "ठीक आहे, आपत्कालीन विनंती रद्द केली.",
    "hi": "ठीक है, आपातकालीन अनुरोध रद्द कर दिया गया।",
    "en": "Okay, the emergency request has been cancelled.",
}

_SPEECH = {
    "mr": "आपला प्रश्न बोला. मेनूवर परत जाण्यासाठी शून्य दाबा.",
    "hi": "अपना सवाल बोलिए। मेन्यू पर लौटने के लिए शून्य दबाएं।",
    "en": "Please say your question. Press zero to return to the menu.",
}

_GOODBYE = {
    "mr": "धन्यवाद. राम कृष्ण हरी.",
    "hi": "धन्यवाद। राम कृष्ण हरी।",
    "en": "Thank you. Ram Krishna Hari.",
}


def _copy(table: dict[str, str], language: str) -> str:
    return table.get(language, table["en"])


def _options(language: str) -> list[Option]:
    rows = _MENU_OPTIONS.get(language, _MENU_OPTIONS["en"])
    return [Option(key=key, label=label) for key, label in rows]


def language_prompt() -> str:
    """The opening greeting, spoken in all three languages in turn."""
    return " ".join(_LANGUAGE_MENU[code] for code in ("mr", "hi", "en"))


def language_options() -> list[Option]:
    return [
        Option(key="1", label="मराठी / Marathi"),
        Option(key="2", label="हिंदी / Hindi"),
        Option(key="3", label="English"),
    ]


def menu_prompt(language: str) -> str:
    return _copy(_MENU, language)


def start() -> Transition:
    return Transition(
        state="language",
        action="greet",
        options=language_options(),
        text=language_prompt(),
    )


def main_menu(language: str, action: Action = "replay") -> Transition:
    return Transition(
        state="menu",
        action=action,
        options=_options(language),
        text=_copy(_MENU, language),
    )


def next_state(state: State, key: str, language: str) -> Transition:
    """Where a keypress takes the call.

    Unknown keys never advance the state — they re-offer the same options with
    a "press zero to replay" nudge, so a mis-tap cannot strand someone.
    """
    key = (key or "").strip()

    if state == "language":
        if key in LANGUAGE_KEYS:
            chosen = LANGUAGE_KEYS[key]
            return Transition(
                state="menu",
                action="language_selected",
                options=_options(chosen),
                text=_copy(_MENU, chosen),
            )
        return Transition(
            state="language",
            action="invalid",
            options=language_options(),
            text=language_prompt(),
        )

    if state == "menu":
        if key == "1":
            return Transition("menu", "crowd_summary", _options(language))
        if key == "2":
            return Transition("menu", "temple_info", _options(language))
        if key == "3":
            return Transition("menu", "nearby_seva", _options(language))
        if key == "4":
            # Never dispatch straight off a menu key: a mis-tap would send
            # responders to someone who is fine, and away from someone who is not.
            return Transition(
                state="sos_confirm",
                action="sos_confirm",
                options=[
                    Option("1", _confirm_label(language)),
                    Option("0", _cancel_label(language)),
                ],
                text=_copy(_SOS_CONFIRM, language),
            )
        if key == "9":
            return Transition(
                state="speech",
                action="enter_speech",
                options=[Option("0", _back_label(language))],
                text=_copy(_SPEECH, language),
            )
        if key == "0":
            return main_menu(language, action="replay")
        if key == "#":
            return Transition(
                state="ended",
                action="goodbye",
                text=_copy(_GOODBYE, language),
                ends_session=True,
            )
        return _invalid(language)

    if state == "sos_confirm":
        if key == "1":
            return Transition("menu", "sos_dispatch", _options(language))
        if key in ("0", "#"):
            return Transition(
                state="menu",
                action="replay",
                options=_options(language),
                text=f"{_copy(_SOS_CANCELLED, language)} {_copy(_MENU, language)}",
            )
        # Anything else during an emergency confirmation re-asks rather than
        # guessing either way.
        return Transition(
            state="sos_confirm",
            action="invalid",
            options=[
                Option("1", _confirm_label(language)),
                Option("0", _cancel_label(language)),
            ],
            text=f"{_copy(_INVALID, language)} {_copy(_SOS_CONFIRM, language)}",
        )

    if state == "speech":
        if key in ("0", "#"):
            return main_menu(language, action="replay")
        return Transition(
            state="speech",
            action="invalid",
            options=[Option("0", _back_label(language))],
            text=_copy(_SPEECH, language),
        )

    # Ended: any key restarts rather than dead-ending.
    return start()


def _invalid(language: str) -> Transition:
    return Transition(
        state="menu",
        action="invalid",
        options=_options(language),
        text=f"{_copy(_INVALID, language)} {_copy(_MENU, language)}",
    )


def _confirm_label(language: str) -> str:
    return {"mr": "होय, मदत पाठवा", "hi": "हाँ, मदद भेजें"}.get(language, "Yes, send help")


def _cancel_label(language: str) -> str:
    return {"mr": "रद्द करा", "hi": "रद्द करें"}.get(language, "Cancel")


def _back_label(language: str) -> str:
    return {"mr": "मेनूवर परत", "hi": "मेन्यू पर वापस"}.get(language, "Back to menu")


def goodbye(language: str) -> str:
    return _copy(_GOODBYE, language)
