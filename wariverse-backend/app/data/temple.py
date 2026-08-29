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
            "12:00 AM · Temple Midnight Opening (Ekadashi)",
            "3:00 AM – 4:00 AM · Special Abhishek",
            "4:00 AM – 5:30 AM · Kakad Aarti",
            "5:30 AM · Main Darshan Begins",
            "6:30 AM · Morning aarti",
            "10:45 AM – 11:00 AM · Mahanaivedya Offering",
            "7:00 PM · Evening aarti (Dhup aarti)",
            "10:30 PM · Shej aarti",
        ],
        "events": [
            "Mukhdarshan (Distant view): 15–20 mins fast queue (Recommended)",
            "Padsparsha Darshan (Touch feet): 2–3 hrs normal, 24–36 hrs peak Ekadashi",
            "Token Slot Booking: Shri Sant Dnyaneshwar Darshan Mandap (North Side)",
            "Paid Passes: Special Darshan (₹100–300), VIP Darshan (₹500–1000)",
            "Ashadhi Ekadashi — July 2026",
            "Kartiki Ekadashi — November 2026",
        ],
        "description": (
            "Shri Vitthal Rukmini Temple, Pandharpur. Please follow volunteer guidance. "
            "Fast Mukhdarshan is available at 15–20 min wait. "
            "Token holders must report to Sant Dnyaneshwar Darshan Mandap North Rear Entry. "
            "No photography inside sanctum."
        ),
    },
    "mr": {
        "title": "विठ्ठल मंदिर — पंढरपूर",
        "timings": "सामान्य दिवस: सकाळी ४:०० – रात्री ११:४५ | आषाढी एकादशी: मध्यरात्री १२:०० – ११:३०",
        "rituals": [
            "मध्यरात्री १२:०० · मंदिर प्रवेश (एकादशी)",
            "पहाटे ३:०० – ४:०० · विशेष अभिषेक",
            "पहाटे ४:०० – ५:३० · काकड आरती",
            "पहाटे ५:३० · मुख्य दर्शन सुरू",
            "सकाळी ६:३० · सकाळ आरती",
            "सकाळी १०:४५ – ११:०० · महानैवेद्य",
            "सायंकाळी ७:०० · धूप आरती",
            "रात्री १०:३० · शेजारती",
        ],
        "events": [
            "मुखदर्शन (पाहणी दर्शन): १५–२० मिनिटे (जलद पर्याय)",
            "पदस्पर्श दर्शन (चरणस्पर्श): २–३ तास (एकादशीस २४–३६ तास)",
            "पास/टोकन केंद्र: श्री संत ज्ञानेश्वर दर्शन मंडप (उत्तर प्रवेश द्वार)",
            "विशेष दर्शन पास: ₹१००–३००, व्हीआयपी: ₹५००–१०००",
            "आषाढी एकादशी यात्रा २०२६",
            "कार्तिकी एकादशी २०२६",
        ],
        "description": (
            "श्री विठ्ठल रुक्मिणी मंदिर. कमी वेळेत दर्शनासाठी मुखदर्शन रांगेचा वापर करा (१५–२० मिनिटे). "
            "ऑनलाइन पासधारकांनी संत ज्ञानेश्वर दर्शन मंडपात वेळेत उपस्थित राहावे."
        ),
    },
    "hi": {
        "title": "विट्ठल मंदिर — पंढरपुर",
        "timings": "सामान्य दिन: सुबह 4:00 – रात 11:45 | आषाढ़ी एकादशी: मध्यरात्रि 12:00 – 11:30",
        "rituals": [
            "मध्यरात्रि 12:00 · मंदिर द्वार खुलना (एकादशी)",
            "सुबह 3:00 – 4:00 · विशेष अभिषेक",
            "सुबह 4:00 – 5:30 · काकड़ आरती",
            "सुबह 5:30 · मुख्य दर्शन प्रारंभ",
            "सुबह 6:30 · प्रातः आरती",
            "सुबह 10:45 – 11:00 · महानैवेद्य",
            "शाम 7:00 · धूप आरती",
            "रात 10:30 · शेज आरती",
        ],
        "events": [
            "मुखदर्शन (दृष्टि दर्शन): 15–20 मिनट (त्वरित विकल्प)",
            "पदस्पर्श दर्शन (चरण स्पर्श): 2–3 घंटे (एकादशी पर 24–36 घंटे)",
            "टोकन प्रवेश केंद्र: श्री संत ज्ञानेश्वर दर्शन मंडप (उत्तर द्वार)",
            "विशेष दर्शन पास: ₹100–300, वीआईपी पास: ₹500–1000",
            "आषाढ़ी एकादशी यात्रा 2026",
            "कार्तिकी एकादशी 2026",
        ],
        "description": (
            "श्री विट्ठल रुक्मिणी मंदिर। कम समय में दर्शन के लिए मुखदर्शन पंक्ति का उपयोग करें (15–20 मिनट)। "
            "टोकन धारक संत ज्ञानेश्वर दर्शन मंडप उत्तर प्रवेश पर रिपोर्ट करें।"
        ),
    },
}


def temple_defaults(language: str) -> dict[str, Any]:
    """Bundled content for a language, falling back to English."""
    return DEFAULT_TEMPLE_INFO.get(language, DEFAULT_TEMPLE_INFO["en"])


# Backwards-compatible alias used by the orchestrator's temple tool.
def temple_content(language: str) -> dict[str, Any]:
    return temple_defaults(language)
