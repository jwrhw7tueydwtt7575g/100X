"""Curated Shri Vitthal Rukmini Mandir information, per language.

Timings follow the Mandir Samiti's published daily schedule and change during
Ashadhi Ekadashi. Treat this file as editorial content: it should be reviewed
against the temple's official notice before each Wari, and time-bound changes
should be published as `temple_notices` rows rather than by editing code.
"""

from __future__ import annotations

from typing import Any

TEMPLE_ID = "vitthal_rukmini_pandharpur"
TEMPLE_LAT = 17.6786
TEMPLE_LON = 75.3300
TEMPLE_CONTACT = "1800-233-1000"

TEMPLE_INFO: dict[str, dict[str, Any]] = {
    "mr": {
        "name": "श्री विठ्ठल रुक्मिणी मंदिर, पंढरपूर",
        "deity": "श्री विठ्ठल (पांडुरंग) व श्री रुक्मिणी माता",
        "address": "मंदिर परिसर, पंढरपूर, जि. सोलापूर, महाराष्ट्र ४१३३०४",
        "darshan_types": [
            {"name": "मुख दर्शन (नि:शुल्क रांग)", "starts_at": "04:00", "ends_at": "23:00",
             "note": "एकादशीला रांग २४ तास सुरू असते."},
            {"name": "पद स्पर्श दर्शन", "starts_at": "06:00", "ends_at": "21:00",
             "note": "गर्दीच्या दिवशी बंद ठेवले जाऊ शकते."},
            {"name": "मुखदर्शन (ज्येष्ठ व दिव्यांग)", "starts_at": "06:00", "ends_at": "20:00",
             "note": "स्वतंत्र रांग — ओळखपत्र आवश्यक."},
        ],
        "aarti_schedule": [
            {"name": "काकड आरती", "starts_at": "04:00", "ends_at": "04:45"},
            {"name": "महापूजा", "starts_at": "05:00", "ends_at": "06:00"},
            {"name": "महानैवेद्य", "starts_at": "11:15", "ends_at": "11:45"},
            {"name": "धूप आरती", "starts_at": "18:45", "ends_at": "19:15"},
            {"name": "शेजारती", "starts_at": "23:00", "ends_at": "23:30"},
        ],
        "dress_code": [
            "पारंपरिक व सभ्य पोशाख घाला.",
            "गर्भगृहात प्रवेशापूर्वी पादत्राणे बाहेर ठेवा.",
            "चामड्याच्या वस्तू मंदिरात नेऊ नका.",
        ],
        "rules": [
            "रांगेत मोबाईल कॅमेरा वापरू नका; गर्भगृहात छायाचित्रण निषिद्ध आहे.",
            "मौल्यवान वस्तू सोबत ठेवू नका — जवळच्या ठेव कक्षाचा वापर करा.",
            "लहान मुलांच्या हातावर नाव व संपर्क क्रमांक लिहा.",
            "स्वयंसेवक व पोलिसांच्या सूचनांचे पालन करा.",
        ],
        "facilities_on_site": [
            "पिण्याचे पाणी", "स्वच्छतागृहे", "प्रथमोपचार केंद्र",
            "हरवले-सापडले कक्ष", "पादत्राणे ठेव कक्ष", "व्हीलचेअर सुविधा",
        ],
    },
    "hi": {
        "name": "श्री विट्ठल रुक्मिणी मंदिर, पंढरपुर",
        "deity": "श्री विट्ठल (पांडुरंग) एवं श्री रुक्मिणी माता",
        "address": "मंदिर परिसर, पंढरपुर, जिला सोलापुर, महाराष्ट्र 413304",
        "darshan_types": [
            {"name": "मुख दर्शन (नि:शुल्क कतार)", "starts_at": "04:00", "ends_at": "23:00",
             "note": "एकादशी पर कतार 24 घंटे चलती है।"},
            {"name": "पद स्पर्श दर्शन", "starts_at": "06:00", "ends_at": "21:00",
             "note": "अत्यधिक भीड़ के दिन बंद रह सकता है।"},
            {"name": "मुख दर्शन (वरिष्ठ एवं दिव्यांग)", "starts_at": "06:00", "ends_at": "20:00",
             "note": "अलग कतार — पहचान पत्र आवश्यक।"},
        ],
        "aarti_schedule": [
            {"name": "काकड़ आरती", "starts_at": "04:00", "ends_at": "04:45"},
            {"name": "महापूजा", "starts_at": "05:00", "ends_at": "06:00"},
            {"name": "महानैवेद्य", "starts_at": "11:15", "ends_at": "11:45"},
            {"name": "धूप आरती", "starts_at": "18:45", "ends_at": "19:15"},
            {"name": "शेज आरती", "starts_at": "23:00", "ends_at": "23:30"},
        ],
        "dress_code": [
            "पारंपरिक और शालीन वस्त्र पहनें।",
            "गर्भगृह में प्रवेश से पहले जूते-चप्पल बाहर रखें।",
            "चमड़े की वस्तुएँ मंदिर में न ले जाएँ।",
        ],
        "rules": [
            "कतार में मोबाइल कैमरा न चलाएँ; गर्भगृह में फोटोग्राफी वर्जित है।",
            "कीमती सामान साथ न रखें — निकटतम जमा कक्ष का उपयोग करें।",
            "बच्चों के हाथ पर नाम और संपर्क नंबर लिखें।",
            "स्वयंसेवकों और पुलिस के निर्देशों का पालन करें।",
        ],
        "facilities_on_site": [
            "पेयजल", "शौचालय", "प्राथमिक चिकित्सा केंद्र",
            "खोया-पाया केंद्र", "जूता जमा कक्ष", "व्हीलचेयर सुविधा",
        ],
    },
    "en": {
        "name": "Shri Vitthal Rukmini Temple, Pandharpur",
        "deity": "Shri Vitthal (Panduranga) and Shri Rukmini Mata",
        "address": "Temple precinct, Pandharpur, Solapur district, Maharashtra 413304",
        "darshan_types": [
            {"name": "Mukh Darshan (free queue)", "starts_at": "04:00", "ends_at": "23:00",
             "note": "The queue runs 24 hours on Ekadashi."},
            {"name": "Pad Sparsh Darshan", "starts_at": "06:00", "ends_at": "21:00",
             "note": "May be suspended on peak-crowd days."},
            {"name": "Mukh Darshan (seniors and differently abled)", "starts_at": "06:00",
             "ends_at": "20:00", "note": "Separate queue — carry photo ID."},
        ],
        "aarti_schedule": [
            {"name": "Kakad Aarti", "starts_at": "04:00", "ends_at": "04:45"},
            {"name": "Mahapooja", "starts_at": "05:00", "ends_at": "06:00"},
            {"name": "Mahanaivedya", "starts_at": "11:15", "ends_at": "11:45"},
            {"name": "Dhoop Aarti", "starts_at": "18:45", "ends_at": "19:15"},
            {"name": "Shej Aarti", "starts_at": "23:00", "ends_at": "23:30"},
        ],
        "dress_code": [
            "Wear traditional, modest clothing.",
            "Leave footwear outside before entering the sanctum.",
            "Leather items are not permitted inside the temple.",
        ],
        "rules": [
            "No phone cameras in the queue; photography is prohibited in the sanctum.",
            "Do not carry valuables — use the nearest deposit counter.",
            "Write your name and phone number on children's arms.",
            "Follow instructions from volunteers and police.",
        ],
        "facilities_on_site": [
            "Drinking water", "Toilets", "First-aid post",
            "Lost & found desk", "Footwear deposit", "Wheelchair assistance",
        ],
    },
}

# Languages without curated content fall back to English.
TEMPLE_INFO["kn"] = TEMPLE_INFO["en"]
TEMPLE_INFO["te"] = TEMPLE_INFO["en"]


def temple_content(language: str) -> dict[str, Any]:
    return TEMPLE_INFO.get(language, TEMPLE_INFO["en"])
