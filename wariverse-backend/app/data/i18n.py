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
    # `distance` arrives already formatted with its unit ("0.8 km", "45 m"),
    # so these must NOT append one — that produced "(0 m m away)".
    "facilities_found": {
        "mr": "तुमच्या जवळ {count} सुविधा सापडल्या. सर्वात जवळची: {nearest} ({distance} अंतरावर).",
        "hi": "आपके पास {count} सुविधाएँ मिलीं। सबसे नज़दीकी: {nearest} ({distance} दूर)।",
        "en": "Found {count} facilities near you. Nearest: {nearest} ({distance} away).",
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
    "sos_activated": {
        "mr": "मदत मागवली आहे. शांत राहा. स्वयंसेवकाला कळवले जात आहे.",
        "hi": "मदद मांगी गई है। शांत रहें। स्वयंसेवक को सूचित किया जा रहा है।",
        "en": "Help has been requested. Stay calm. A volunteer is being notified.",
    },
    "control_room_connected": {
        "mr": "जोडलेले", "hi": "जुड़ा हुआ", "en": "Connected",
    },
    "control_room_standing_by": {
        "mr": "तयार", "hi": "तैयार", "en": "Standing by",
    },
    "control_room_unreachable": {
        "mr": "संपर्क होत नाही", "hi": "संपर्क नहीं", "en": "Unreachable",
    },
    "control_room_cancelled": {
        "mr": "रद्द", "hi": "रद्द", "en": "Cancelled",
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
    # Keyed by `crowd_` + the lowercased density status from the DB.
    "crowd_very_high": {
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
    "route_walk_minutes": {
        "mr": "{minutes} मिनिटे चालत", "hi": "{minutes} मिनट पैदल",
        "en": "{minutes} min walk",
    },
    "route_current_location": {
        "mr": "सध्याचे ठिकाण", "hi": "वर्तमान स्थान", "en": "Current location",
    },
    "route_selected_destination": {
        "mr": "निवडलेले ठिकाण", "hi": "चयनित स्थान", "en": "Selected destination",
    },
    "congestion_high": {
        "mr": "जास्त गर्दी", "hi": "अधिक भीड़", "en": "high congestion",
    },
    "congestion_very_high": {
        "mr": "अतिशय दाट गर्दी", "hi": "अत्यधिक भीड़", "en": "very high congestion",
    },
    # --- facilities --------------------------------------------------------
    "facility_seva_open": {
        "mr": "मोफत सेवा · {provider}",
        "hi": "नि:शुल्क सेवा · {provider}",
        "en": "Free seva · {provider}",
    },
    "facility_open": {"mr": "सुरू", "hi": "खुला", "en": "Open"},
    "facility_closed": {"mr": "बंद", "hi": "बंद", "en": "Closed"},
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
    "lost_found_status_open": {
        "mr": "शोध सुरू", "hi": "खोज जारी", "en": "Searching",
    },
    "lost_found_status_in_progress": {
        "mr": "स्वयंसेवक शोधत आहेत", "hi": "स्वयंसेवक खोज रहे हैं",
        "en": "Volunteers searching",
    },
    "lost_found_status_matched": {
        "mr": "संभाव्य माहिती मिळाली", "hi": "संभावित सुराग मिला",
        "en": "Possible match found",
    },
    "lost_found_status_resolved": {
        "mr": "पुन्हा भेट झाली", "hi": "मिलन हो गया", "en": "Reunited",
    },
    "lost_found_status_closed": {"mr": "बंद", "hi": "बंद", "en": "Closed"},
    "lost_found_next_action": {
        "mr": "शेवटच्या ठिकाणाजवळ थांबा आणि फोन सुरू ठेवा.",
        "hi": "अंतिम स्थान के पास रुकें और फोन चालू रखें।",
        "en": "Stay near the last known location and keep your phone reachable.",
    },
    "lost_found_filed": {
        "mr": "तुमची तक्रार स्वयंसेवक पथकाला पाठवली आहे.",
        "hi": "आपकी शिकायत स्वयंसेवक टीम को भेज दी गई है।",
        "en": "Your report has been shared with volunteer team.",
    },
    "lost_found_created": {
        "mr": "तक्रार नोंदवली. संदर्भ क्रमांक {ref_id}. हा क्रमांक जपून ठेवा आणि "
              "{helpline} वर संपर्क साधा. जवळच्या हरवले-सापडले कक्षात नोंद पोहोचली आहे.",
        "hi": "शिकायत दर्ज हो गई। संदर्भ संख्या {ref_id}। यह नंबर संभालकर रखें और "
              "{helpline} पर संपर्क करें। नज़दीकी खोया-पाया केंद्र को सूचना भेज दी गई है।",
        "en": "Report registered. Reference number {ref_id}. Keep this number safe and "
              "call {helpline}. The nearest lost & found desk has been notified.",
    },
    "lost_found_offline": {
        "mr": "सध्या नोंद करता येत नाही. कृपया लगेच {helpline} वर कॉल करा किंवा "
              "जवळच्या हरवले-सापडले कक्षात जा.",
        "hi": "अभी दर्ज नहीं हो पा रहा। कृपया तुरंत {helpline} पर कॉल करें या "
              "नज़दीकी खोया-पाया केंद्र जाएँ।",
        "en": "I can't file the report right now. Please call {helpline} immediately "
              "or go to the nearest lost & found desk.",
    },
    # --- forecast ----------------------------------------------------------
    "forecast_recommendation": {
        "mr": "{zone} येथे सुमारे {time} वाजता सर्वात कमी गर्दी असण्याची शक्यता आहे.",
        "hi": "{zone} पर लगभग {time} बजे सबसे कम भीड़ रहने की संभावना है।",
        "en": "{zone} is likely to be quietest around {time}.",
    },
    "forecast_before": {
        "mr": "{zone} ला जाण्यासाठी {time} पूर्वीची वेळ सर्वोत्तम आहे.",
        "hi": "{zone} जाने के लिए {time} से पहले का समय सबसे अच्छा है।",
        "en": "Before {time} is the best time to visit {zone}.",
    },
    "forecast_after": {
        "mr": "सध्या गर्दी आहे; {zone} ला जाण्यासाठी {time} नंतरची वेळ सर्वोत्तम आहे.",
        "hi": "अभी भीड़ है; {zone} जाने के लिए {time} के बाद का समय सबसे अच्छा है।",
        "en": "It's busy now — after {time} is the best time to visit {zone}.",
    },
    "forecast_all_clear": {
        "mr": "{zone} येथे पुढील काही तास गर्दी कमी राहण्याची शक्यता आहे.",
        "hi": "{zone} पर अगले कुछ घंटे भीड़ कम रहने की संभावना है।",
        "en": "{zone} should stay comfortable for the next several hours.",
    },
    "forecast_quietest": {
        "mr": "{zone} येथे {time} च्या सुमारास तुलनेने कमी गर्दी असेल.",
        "hi": "{zone} पर {time} के आसपास अपेक्षाकृत कम भीड़ रहेगी।",
        "en": "{zone} is relatively quietest around {time}.",
    },
    "forecast_updated": {
        "mr": "अद्ययावत {age}", "hi": "अपडेट {age}", "en": "Updated {age}",
    },
    # --- escalation --------------------------------------------------------
    "escalation_waiting": {
        "mr": "मी स्वयंसेवकाला कळवले आहे. ते लवकरच संपर्क करतील. तातडीचे असल्यास "
              "{helpline} वर कॉल करा.",
        "hi": "मैंने स्वयंसेवक को सूचित कर दिया है। वे जल्द संपर्क करेंगे। ज़रूरी हो तो "
              "{helpline} पर कॉल करें।",
        "en": "I've flagged this for a volunteer — they'll reach out shortly. If it's "
              "urgent, call {helpline}.",
    },
    "escalation_offline": {
        "mr": "सध्या स्वयंसेवक कक्ष बंद आहे (पहाटे ५ ते रात्री ११ सुरू). तातडीचे असल्यास "
              "{helpline} वर कॉल करा.",
        "hi": "अभी स्वयंसेवक कक्ष बंद है (सुबह 5 से रात 11 तक खुला)। ज़रूरी हो तो "
              "{helpline} पर कॉल करें।",
        "en": "The volunteer desk is closed right now (open 5am-11pm). If it's urgent, "
              "call {helpline}.",
    },
    # --- IVR (spoken: keep sentences short and free of digits/markup) -------
    "ivr_language_prompt": {
        "mr": "वारीव्हर्समध्ये आपले स्वागत आहे. मराठीसाठी एक दाबा.",
        "hi": "वारीवर्स में आपका स्वागत है। हिंदी के लिए दो दबाएं।",
        "en": "Welcome to WariVerse. For English, press three.",
    },
    "ivr_ask_question": {
        "mr": "आपला प्रश्न सांगा. आपत्कालीन मदतीसाठी नऊ दाबा.",
        "hi": "अपना सवाल बोलिए। आपात मदद के लिए नौ दबाएं।",
        "en": "Please say your question. For emergency help, press nine.",
    },
    "ivr_anything_else": {
        "mr": "आणखी काही विचारायचे आहे का?",
        "hi": "और कुछ पूछना है?",
        "en": "Is there anything else?",
    },
    "ivr_no_input": {
        "mr": "मला काही ऐकू आले नाही. कृपया पुन्हा सांगा.",
        "hi": "मुझे कुछ सुनाई नहीं दिया। कृपया दोबारा बोलिए।",
        "en": "I did not hear anything. Please say that again.",
    },
    "ivr_goodbye": {
        "mr": "धन्यवाद. राम कृष्ण हरी.",
        "hi": "धन्यवाद। राम कृष्ण हरी।",
        "en": "Thank you. Ram Krishna Hari.",
    },
    "ivr_error": {
        "mr": "क्षमा करा, तांत्रिक अडचण आली आहे. आपत्कालीन मदतीसाठी एक एक दोन वर कॉल करा.",
        "hi": "क्षमा करें, तकनीकी दिक्कत है। आपात मदद के लिए एक एक दो पर कॉल करें।",
        "en": "Sorry, there is a technical problem. For emergency help call one one two.",
    },
    "ivr_where_are_you": {
        "mr": "आपण कोणत्या दरवाजाजवळ किंवा घाटाजवळ आहात ते सांगा.",
        "hi": "आप किस द्वार या घाट के पास हैं, बताइए।",
        "en": "Please say which gate or ghat you are near.",
    },
    "ivr_escalation_hold": {
        "mr": "कृपया थांबा. मी स्वयंसेवकाला जोडत आहे.",
        "hi": "कृपया रुकिए। मैं स्वयंसेवक से जोड़ रहा हूँ।",
        "en": "Please hold. I am connecting you to a volunteer.",
    },
    "ivr_sos_no_location": {
        "mr": "मदत मागवली आहे. आपले ठिकाण माहीत नाही, त्यामुळे नियंत्रण कक्ष याच "
              "क्रमांकावर परत कॉल करेल. फोन सुरू ठेवा.",
        "hi": "मदद मांगी गई है। आपका स्थान ज्ञात नहीं है, इसलिए नियंत्रण कक्ष इसी "
              "नंबर पर वापस कॉल करेगा। फोन चालू रखें।",
        "en": "Help has been requested. Your location is not known, so the control "
              "room will call you back on this number. Please keep your phone on.",
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
