"""Translated strings for deterministic (non-LLM) responses.

The assistant must stay useful when the LLM is unavailable or rate-limited, and
safety-critical wording — SOS confirmations, crowd warnings — should never be
paraphrased by a model. Those strings live here.

`t(key, language, **kwargs)` falls back to English for any language without a
translation, so adding a language is additive.
"""

from __future__ import annotations

from typing import Final

FALLBACK_LANGUAGE: Final = "en"

PHRASES: Final[dict[str, dict[str, str]]] = {
    # --- conversation ------------------------------------------------------
    "greeting": {
        "mr": "राम कृष्ण हरी! मी वारीव्हर्स सहाय्यक. दर्शन, गर्दी, पाणी-स्वच्छतागृह, "
              "मार्ग किंवा आपत्कालीन मदतीबद्दल विचारा.",
        "hi": "राम कृष्ण हरी! मैं वारीवर्स सहायक हूँ। दर्शन, भीड़, पानी-शौचालय, रास्ता "
              "या आपातकालीन मदद के बारे में पूछें।",
        "en": "Ram Krishna Hari! I'm the WariVerse assistant. Ask me about darshan, "
              "crowds, water and toilets, routes, or emergency help.",
    },
    "fallback": {
        "mr": "मला नीट समजले नाही. तुम्ही दर्शनाची वेळ, गर्दी, जवळच्या सुविधा, मार्ग "
              "किंवा हरवलेल्या व्यक्तीबद्दल विचारू शकता.",
        "hi": "मैं ठीक से समझ नहीं पाया। आप दर्शन का समय, भीड़, नज़दीकी सुविधाएँ, रास्ता "
              "या खोए व्यक्ति के बारे में पूछ सकते हैं।",
        "en": "I didn't quite catch that. You can ask about darshan timings, crowd "
              "levels, nearby facilities, routes, or a missing person.",
    },
    "need_location": {
        "mr": "तुमचे ठिकाण कळल्यास मी जवळच्या सुविधा दाखवू शकतो. कृपया अ‍ॅपमध्ये लोकेशन सुरू करा.",
        "hi": "आपका स्थान मिलने पर मैं नज़दीकी सुविधाएँ दिखा सकता हूँ। कृपया ऐप में लोकेशन चालू करें।",
        "en": "Share your location and I'll show the nearest facilities. Please turn on "
              "location in the app.",
    },
    "facilities_found": {
        "mr": "तुमच्या जवळ {count} सुविधा सापडल्या. सर्वात जवळची: {nearest} ({distance} मी).",
        "hi": "आपके पास {count} सुविधाएँ मिलीं। सबसे नज़दीकी: {nearest} ({distance} मी)।",
        "en": "Found {count} facilities near you. Nearest: {nearest} ({distance} m away).",
    },
    "facilities_none": {
        "mr": "या परिसरात नोंदवलेली सुविधा सापडली नाही. जवळच्या माहिती केंद्रात विचारा.",
        "hi": "इस क्षेत्र में कोई दर्ज सुविधा नहीं मिली। नज़दीकी सूचना केंद्र पर पूछें।",
        "en": "No registered facility found in this area. Please ask at the nearest "
              "information centre.",
    },
    "lost_found_prompt": {
        "mr": "कोणी हरवले असल्यास लगेच नोंद करा. नाव, वय, कपड्यांचे वर्णन आणि शेवटचे ठिकाण "
              "सांगा — मी तक्रार क्रमांक तयार करतो.",
        "hi": "कोई खो गया है तो तुरंत दर्ज करें। नाम, उम्र, कपड़ों का विवरण और आखिरी स्थान "
              "बताएँ — मैं शिकायत नंबर बना दूँगा।",
        "en": "If someone is missing, report it right away. Tell me their name, age, "
              "what they were wearing and where you last saw them — I'll create a "
              "reference number.",
    },
    # --- SOS ---------------------------------------------------------------
    "sos_confirm_prompt": {
        "mr": "तुम्हाला आपत्कालीन मदत हवी आहे का? 'होय' म्हणा — मी तुमचे ठिकाण जवळच्या "
              "मदत केंद्राला पाठवतो. तात्काळ धोका असल्यास {helpline} वर कॉल करा.",
        "hi": "क्या आपको आपातकालीन मदद चाहिए? 'हाँ' कहें — मैं आपका स्थान नज़दीकी मदद "
              "केंद्र को भेज दूँगा। तत्काल खतरा हो तो {helpline} पर कॉल करें।",
        "en": "Do you need emergency help? Say 'yes' and I'll send your location to the "
              "nearest help post. If you are in immediate danger, call {helpline} now.",
    },
    "sos_dispatched": {
        "mr": "मदत पाठवली आहे. {desk} ला तुमचे ठिकाण कळवले असून अंदाजे {eta} मिनिटांत "
              "पोहोचतील. एका जागी थांबा आणि {helpline} वर कॉल करू शकता.",
        "hi": "मदद भेज दी गई है। {desk} को आपका स्थान भेजा गया है, लगभग {eta} मिनट में "
              "पहुँचेंगे। एक जगह रुकें और {helpline} पर कॉल कर सकते हैं।",
        "en": "Help is on the way. Your location has been sent to {desk}; expected "
              "arrival in about {eta} minutes. Stay where you are — you can also call "
              "{helpline}.",
    },
    "sos_cancelled": {
        "mr": "ठीक आहे, आपत्कालीन विनंती रद्द केली. गरज पडल्यास पुन्हा सांगा.",
        "hi": "ठीक है, आपातकालीन अनुरोध रद्द कर दिया गया। ज़रूरत हो तो फिर बताएँ।",
        "en": "Okay, the emergency request has been cancelled. Tell me again if you need help.",
    },
    "sos_no_location": {
        "mr": "मदत पाठवण्यासाठी तुमचे ठिकाण आवश्यक आहे. कृपया लोकेशन सुरू करा किंवा "
              "थेट {helpline} वर कॉल करा.",
        "hi": "मदद भेजने के लिए आपका स्थान चाहिए। कृपया लोकेशन चालू करें या सीधे "
              "{helpline} पर कॉल करें।",
        "en": "I need your location to send help. Please enable location, or call "
              "{helpline} directly.",
    },
    # --- crowd -------------------------------------------------------------
    "crowd_low": {
        "mr": "{zone} येथे सध्या गर्दी कमी आहे. दर्शनासाठी ही चांगली वेळ आहे.",
        "hi": "{zone} पर अभी भीड़ कम है। दर्शन के लिए यह अच्छा समय है।",
        "en": "{zone} is not crowded right now — a good time to go.",
    },
    "crowd_moderate": {
        "mr": "{zone} येथे मध्यम गर्दी आहे. अंदाजे प्रतीक्षा {wait} मिनिटे. सोबतच्या "
              "लोकांचा हात धरून राहा.",
        "hi": "{zone} पर मध्यम भीड़ है। अनुमानित प्रतीक्षा {wait} मिनट। साथ वालों का हाथ पकड़े रहें।",
        "en": "{zone} has moderate crowding. Expected wait about {wait} minutes. Keep "
              "your group together.",
    },
    "crowd_high": {
        "mr": "{zone} येथे मोठी गर्दी आहे. अंदाजे प्रतीक्षा {wait} मिनिटे. शक्य असल्यास "
              "थोड्या वेळाने या किंवा पर्यायी ठिकाण निवडा.",
        "hi": "{zone} पर भारी भीड़ है। अनुमानित प्रतीक्षा {wait} मिनट। हो सके तो कुछ देर बाद "
              "आएँ या वैकल्पिक स्थान चुनें।",
        "en": "{zone} is heavily crowded. Expected wait about {wait} minutes. Consider "
              "coming later or using an alternative.",
    },
    "crowd_critical": {
        "mr": "धोका: {zone} येथे अतिशय दाट गर्दी आहे. कृपया या भागात जाऊ नका, गर्दीच्या "
              "दिशेने ढकलले जाऊ नका आणि स्वयंसेवकांच्या सूचना पाळा.",
        "hi": "चेतावनी: {zone} पर अत्यधिक भीड़ है। कृपया इस क्षेत्र में न जाएँ, भीड़ के दबाव "
              "में न फँसें और स्वयंसेवकों के निर्देश मानें।",
        "en": "Warning: {zone} is dangerously crowded. Please avoid this area, do not "
              "push into the flow, and follow the volunteers' instructions.",
    },
    "crowd_alternates": {
        "mr": "पर्याय: {zones}.",
        "hi": "विकल्प: {zones}।",
        "en": "Alternatives: {zones}.",
    },
    # --- routes ------------------------------------------------------------
    "route_summary": {
        "mr": "{destination} पर्यंत सुमारे {distance} किमी — चालत अंदाजे {eta} मिनिटे.",
        "hi": "{destination} तक लगभग {distance} किमी — पैदल करीब {eta} मिनट।",
        "en": "About {distance} km to {destination} — roughly {eta} minutes on foot.",
    },
    "route_step": {
        "mr": "{name} कडे {direction} दिशेने {distance} मी चाला.",
        "hi": "{name} की ओर {direction} दिशा में {distance} मी चलें।",
        "en": "Walk {distance} m {direction} towards {name}.",
    },
    "route_arrive": {
        "mr": "{name} येथे पोहोचलात. हीच तुमची शेवटची खूण.",
        "hi": "{name} पहुँच गए। यही आपका अंतिम पड़ाव है।",
        "en": "You arrive at {name} — this is your destination.",
    },
    "route_congested": {
        "mr": "{zone} मार्गावर गर्दी आहे; पुढे हळू चालावे लागेल.",
        "hi": "{zone} मार्ग पर भीड़ है; आगे धीरे चलना पड़ेगा.",
        "en": "{zone} on this route is congested; expect slow movement ahead.",
    },
    # --- temple ------------------------------------------------------------
    "temple_summary": {
        "mr": "{name} — मुख दर्शन रांगेत अंदाजे {wait} मिनिटे प्रतीक्षा आहे.",
        "hi": "{name} — मुख दर्शन कतार में लगभग {wait} मिनट की प्रतीक्षा है।",
        "en": "{name} — the mukh darshan queue is running about {wait} minutes.",
    },
    # --- lost & found ------------------------------------------------------
    "lost_found_created": {
        "mr": "तक्रार नोंदवली. संदर्भ क्रमांक {ref_id}. हा क्रमांक जपून ठेवा आणि "
              "{helpline} वर संपर्क साधा. जवळच्या हरवले-सापडले कक्षात नोंद पोहोचली आहे.",
        "hi": "शिकायत दर्ज हो गई। संदर्भ संख्या {ref_id}। यह नंबर संभालकर रखें और "
              "{helpline} पर संपर्क करें। नज़दीकी खोया-पाया केंद्र को सूचना भेज दी गई है।",
        "en": "Report registered. Reference number {ref_id}. Keep this number safe and "
              "call {helpline}. The nearest lost & found desk has been notified.",
    },
    # --- auth --------------------------------------------------------------
    "otp_sent": {
        "mr": "{phone} वर सत्यापन क्रमांक पाठवला आहे.",
        "hi": "{phone} पर सत्यापन कोड भेजा गया है।",
        "en": "A verification code has been sent to {phone}.",
    },
}


def t(key: str, language: str, **kwargs: object) -> str:
    """Translate `key` into `language`, formatting any placeholders."""
    variants = PHRASES.get(key)
    if not variants:
        return key
    template = variants.get(language) or variants[FALLBACK_LANGUAGE]
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        # A missing placeholder must never break a safety message.
        return template
