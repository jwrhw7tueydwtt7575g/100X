"""Default Shri Vitthal Rukmini Mandir information.

This is the seed content for the `temple_info` table and the fallback when that
table is empty. Once seeded, an operator edits the row through
`PUT /api/admin/temple/info` — editing this file only changes what a fresh
database starts with.

⚠️ Timings and event dates are editorial content that must be checked against
the Mandir Samiti's published notice before each Wari. The Ekadashi dates below
in particular are placeholders.
"""

from __future__ import annotations

from typing import Any

TEMPLE_ID = "vitthal_rukmini_pandharpur"
TEMPLE_LAT = 17.6775
TEMPLE_LON = 75.3283
TEMPLE_CONTACT = "1800-233-1000"

DEFAULT_TEMPLE_INFO: dict[str, dict[str, Any]] = {
    "en": {
        "title": "Vitthal Temple — Pandharpur",
        "timings": "6:00 AM – 11:00 PM",
        "rituals": [
            "Morning aarti · 6:30 AM",
            "Kakad aarti · 5:00 AM (special days)",
            "Evening aarti · 7:00 PM",
            "Shej aarti · 10:30 PM",
        ],
        "events": [
            "Ashadhi Ekadashi — July 17, 2026",
            "Kartiki Ekadashi — November 5, 2026",
        ],
        "description": (
            "Please follow volunteer guidance. Keep walkways clear. "
            "No photography inside sanctum."
        ),
    },
    "mr": {
        "title": "विठ्ठल मंदिर — पंढरपूर",
        "timings": "सकाळी ६:०० – रात्री ११:००",
        "rituals": [
            "काकड आरती · पहाटे ५:०० (विशेष दिवशी)",
            "सकाळ आरती · सकाळी ६:३०",
            "धूप आरती · सायंकाळी ७:००",
            "शेजारती · रात्री १०:३०",
        ],
        "events": [
            "आषाढी एकादशी — १७ जुलै २०२६",
            "कार्तिकी एकादशी — ५ नोव्हेंबर २०२६",
        ],
        "description": (
            "कृपया स्वयंसेवकांच्या सूचनांचे पालन करा. रस्ता मोकळा ठेवा. "
            "गर्भगृहात छायाचित्रण करू नका."
        ),
    },
    "hi": {
        "title": "विट्ठल मंदिर — पंढरपुर",
        "timings": "सुबह 6:00 – रात 11:00",
        "rituals": [
            "काकड़ आरती · सुबह 5:00 (विशेष दिनों पर)",
            "प्रातः आरती · सुबह 6:30",
            "संध्या आरती · शाम 7:00",
            "शेज आरती · रात 10:30",
        ],
        "events": [
            "आषाढी एकादशी — 17 जुलाई 2026",
            "कार्तिकी एकादशी — 5 नवंबर 2026",
        ],
        "description": (
            "कृपया स्वयंसेवकों के निर्देशों का पालन करें। रास्ता खुला रखें। "
            "गर्भगृह में फोटोग्राफी वर्जित है।"
        ),
    },
}


def temple_defaults(language: str) -> dict[str, Any]:
    """Bundled content for a language, falling back to English."""
    return DEFAULT_TEMPLE_INFO.get(language, DEFAULT_TEMPLE_INFO["en"])


# Backwards-compatible alias used by the orchestrator's temple tool.
def temple_content(language: str) -> dict[str, Any]:
    return temple_defaults(language)
