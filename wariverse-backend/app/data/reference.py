"""Reference data for zones, facilities and the palkhi route.

IMPORTANT: every coordinate below is an approximate placeholder derived from
public maps, accurate to roughly a few hundred metres. Before a live Wari,
replace this file with the surveyed dataset published by the Solapur district
administration / Mandir Samiti — pilgrims will navigate on these numbers.

The same records are used by `scripts/seed.py` to populate Postgres and as the
in-process fallback when the database is empty or unreachable.
"""

from __future__ import annotations

from typing import Any

# --- monitored zones --------------------------------------------------------

ZONES: list[dict[str, Any]] = [
    {
        "zone_id": "gate-1",
        "name_en": "Gate 1",
        "name_mr": "दरवाजा १",
        "name_hi": "द्वार 1",
        "description_en": "East entrance",
        "zone_type": "queue",
        "lat": 17.6790,
        "lon": 75.3308,
        "radius_m": 120,
        "capacity": 8000,
        "alternate_zone_ids": ["gate-2", "gate-3"],
    },
    {
        "zone_id": "gate-2",
        "name_en": "Gate 2",
        "name_mr": "दरवाजा २",
        "name_hi": "द्वार 2",
        "description_en": "North entrance",
        "zone_type": "queue",
        "lat": 17.6795,
        "lon": 75.3295,
        "radius_m": 120,
        "capacity": 8000,
        "alternate_zone_ids": ["gate-1", "gate-3"],
    },
    {
        "zone_id": "gate-3",
        "name_en": "Gate 3",
        "name_mr": "दरवाजा ३",
        "name_hi": "द्वार 3",
        "description_en": "South entrance",
        "zone_type": "queue",
        "lat": 17.6779,
        "lon": 75.3301,
        "radius_m": 120,
        "capacity": 8000,
        "alternate_zone_ids": ["gate-1", "gate-2"],
    },
    {
        "zone_id": "temple-main",
        "name_en": "Main Temple",
        "name_mr": "मुख्य मंदिर",
        "name_hi": "मुख्य मंदिर",
        "description_en": "Shri Vitthal Rukmini temple complex",
        "zone_type": "temple",
        "lat": 17.6775,
        "lon": 75.3283,
        "radius_m": 150,
        "capacity": 12000,
        "alternate_zone_ids": ["gate-2", "gate-3"],
    },
    {
        "zone_id": "bhima-ghat",
        "name_en": "Bhima Ghat",
        "name_mr": "भीमा घाट",
        "name_hi": "भीमा घाट",
        "description_en": "Chandrabhaga river bathing ghat",
        "zone_type": "ghat",
        "lat": 17.6812,
        "lon": 75.3262,
        "radius_m": 400,
        "capacity": 40000,
        "alternate_zone_ids": ["main-road"],
    },
    {
        "zone_id": "main-road",
        "name_en": "Main Road",
        "name_mr": "मुख्य रस्ता",
        "name_hi": "मुख्य सड़क",
        "description_en": "Pradakshina approach corridor",
        "zone_type": "corridor",
        "lat": 17.6771,
        "lon": 75.3325,
        "radius_m": 600,
        "capacity": 30000,
        "alternate_zone_ids": ["bhima-ghat"],
    },
]

ZONES_BY_ID: dict[str, dict[str, Any]] = {z["zone_id"]: z for z in ZONES}


# --- facilities -------------------------------------------------------------

FACILITIES: list[dict[str, Any]] = [
    # --- medical: 3 posts covering the two busiest gates and the ghat -------
    {
        "external_id": "fac-001",
        "name_en": "Wari Medical Center",
        "name_mr": "वारी वैद्यकीय केंद्र",
        "name_hi": "वारी चिकित्सा केंद्र",
        "facility_type": "medical",
        "lat": 17.6788, "lon": 75.3305,
        "address": "Gate 1 approach, temple precinct",
        "contact_phone": "+912166222333",
        "is_24x7": True,
        "wheelchair_accessible": True,
        "capacity": 40,
        "details": {"staffing": "Volunteer staffed", "ambulance": True, "oxygen": True},
    },
    {
        "external_id": "fac-002",
        "name_en": "Gate 3 First Aid Post",
        "name_mr": "दरवाजा ३ प्रथमोपचार केंद्र",
        "name_hi": "द्वार 3 प्राथमिक चिकित्सा केंद्र",
        "facility_type": "medical",
        "lat": 17.6777, "lon": 75.3298,
        "address": "Gate 3, south entrance",
        "contact_phone": "+912166222334",
        "is_24x7": True,
        "wheelchair_accessible": True,
        "capacity": 20,
        "details": {"staffing": "Volunteer staffed", "ors": True},
    },
    {
        "external_id": "fac-003",
        "name_en": "Bhima Ghat Medical Camp",
        "name_mr": "भीमा घाट वैद्यकीय शिबिर",
        "name_hi": "भीमा घाट चिकित्सा शिविर",
        "facility_type": "medical",
        "lat": 17.6808, "lon": 75.3265,
        "address": "Riverside, Bhima Ghat",
        "contact_phone": "+912166222335",
        "opens_at": "04:00", "closes_at": "23:00",
        "capacity": 30,
        "details": {"staffing": "Doctor on duty", "ambulance": True},
    },
    # --- water: 5 points along the main pilgrim path ------------------------
    {
        "external_id": "fac-004",
        "name_en": "Main Road Water Point",
        "name_mr": "मुख्य रस्ता पाणी केंद्र",
        "name_hi": "मुख्य सड़क जल केंद्र",
        "facility_type": "water",
        "lat": 17.6772, "lon": 75.3320,
        "is_24x7": True, "capacity": 30,
        "details": {"staffing": "Volunteer staffed", "taps": 30, "cold_water": True},
    },
    {
        "external_id": "fac-005",
        "name_en": "Temple Approach Water Point",
        "name_mr": "मंदिर मार्ग पाणी केंद्र",
        "name_hi": "मंदिर मार्ग जल केंद्र",
        "facility_type": "water",
        "lat": 17.6776, "lon": 75.3305,
        "is_24x7": True, "capacity": 24,
        "details": {"staffing": "Volunteer staffed", "taps": 24},
    },
    {
        "external_id": "fac-006",
        "name_en": "Gate 2 Water Point",
        "name_mr": "दरवाजा २ पाणी केंद्र",
        "name_hi": "द्वार 2 जल केंद्र",
        "facility_type": "water",
        "lat": 17.6780, "lon": 75.3292,
        "is_24x7": True, "capacity": 20,
        "details": {"staffing": "Volunteer staffed", "taps": 20},
    },
    {
        "external_id": "fac-007",
        "name_en": "Ghat Road Water Point",
        "name_mr": "घाट रस्ता पाणी केंद्र",
        "name_hi": "घाट सड़क जल केंद्र",
        "facility_type": "water",
        "lat": 17.6795, "lon": 75.3280,
        "is_24x7": True, "capacity": 24,
        "details": {"staffing": "Volunteer staffed", "taps": 24},
    },
    {
        "external_id": "fac-008",
        "name_en": "Bhima Ghat Water Tanker",
        "name_mr": "भीमा घाट पाण्याचा टँकर",
        "name_hi": "भीमा घाट जल टैंकर",
        "facility_type": "water",
        "lat": 17.6805, "lon": 75.3268,
        "opens_at": "04:00", "closes_at": "23:00", "capacity": 12,
        "details": {"staffing": "Volunteer staffed", "tanker": True},
    },
    # --- toilets: 4 blocks --------------------------------------------------
    {
        "external_id": "fac-009",
        "name_en": "Main Road Toilet Block",
        "name_mr": "मुख्य रस्ता स्वच्छतागृह",
        "name_hi": "मुख्य सड़क शौचालय",
        "facility_type": "toilet",
        "lat": 17.6768, "lon": 75.3315,
        "is_24x7": True, "capacity": 60, "wheelchair_accessible": True,
        "details": {"staffing": "Cleaning staff on site", "units": 60},
    },
    {
        "external_id": "fac-010",
        "name_en": "Temple East Toilet Block",
        "name_mr": "मंदिर पूर्व स्वच्छतागृह",
        "name_hi": "मंदिर पूर्व शौचालय",
        "facility_type": "toilet",
        "lat": 17.6784, "lon": 75.3299,
        "is_24x7": True, "capacity": 40,
        "details": {"staffing": "Cleaning staff on site", "units": 40},
    },
    {
        "external_id": "fac-011",
        "name_en": "Gate 2 Toilet Block",
        "name_mr": "दरवाजा २ स्वच्छतागृह",
        "name_hi": "द्वार 2 शौचालय",
        "facility_type": "toilet",
        "lat": 17.6790, "lon": 75.3288,
        "is_24x7": True, "capacity": 45, "wheelchair_accessible": True,
        "details": {"staffing": "Cleaning staff on site", "units": 45},
    },
    {
        "external_id": "fac-012",
        "name_en": "Ghat Toilet Block",
        "name_mr": "घाट स्वच्छतागृह",
        "name_hi": "घाट शौचालय",
        "facility_type": "toilet",
        "lat": 17.6800, "lon": 75.3272,
        "is_24x7": True, "capacity": 50,
        "details": {"staffing": "Cleaning staff on site", "units": 50},
    },
    # --- rest shelters: 2 ---------------------------------------------------
    {
        "external_id": "fac-013",
        "name_en": "Main Road Rest Shelter",
        "name_mr": "मुख्य रस्ता विश्रांती निवारा",
        "name_hi": "मुख्य सड़क विश्राम आश्रय",
        "facility_type": "rest",
        "lat": 17.6765, "lon": 75.3325,
        "is_24x7": True, "capacity": 400, "wheelchair_accessible": True,
        "details": {"staffing": "Volunteer staffed", "shade": True, "mats": True},
    },
    {
        "external_id": "fac-014",
        "name_en": "Ghat Rest Shelter",
        "name_mr": "घाट विश्रांती निवारा",
        "name_hi": "घाट विश्राम आश्रय",
        "facility_type": "rest",
        "lat": 17.6798, "lon": 75.3260,
        "is_24x7": True, "capacity": 300,
        "details": {"staffing": "Volunteer staffed", "shade": True},
    },
    # --- food: 3 volunteer-run langars --------------------------------------
    {
        "external_id": "fac-015",
        "name_en": "Sant Namdev Langar",
        "name_mr": "संत नामदेव लंगर",
        "name_hi": "संत नामदेव लंगर",
        "facility_type": "food",
        "lat": 17.6770, "lon": 75.3310,
        "opens_at": "06:00", "closes_at": "22:00", "capacity": 3000,
        "details": {"staffing": "Volunteer run", "free": True,
                    "meals": ["breakfast", "lunch", "dinner"]},
    },
    {
        "external_id": "fac-016",
        "name_en": "Temple Road Annachhatra",
        "name_mr": "मंदिर रस्ता अन्नछत्र",
        "name_hi": "मंदिर मार्ग अन्नक्षेत्र",
        "facility_type": "food",
        "lat": 17.6786, "lon": 75.3282,
        "opens_at": "10:00", "closes_at": "21:00", "capacity": 2000,
        "details": {"staffing": "Volunteer run", "free": True,
                    "meals": ["lunch", "dinner"]},
    },
    {
        "external_id": "fac-017",
        "name_en": "Ghat Community Kitchen",
        "name_mr": "घाट सामुदायिक भोजनालय",
        "name_hi": "घाट सामुदायिक रसोई",
        "facility_type": "food",
        "lat": 17.6802, "lon": 75.3258,
        "opens_at": "05:00", "closes_at": "22:00", "capacity": 2500,
        "details": {"staffing": "Volunteer run", "free": True,
                    "meals": ["breakfast", "lunch", "dinner"]},
    },
    # --- not in the seed spec, but SOS dispatch routes responders to these ---
    # Dropping them would leave `trigger_sos` with nowhere to send help.
    {
        "external_id": "fac-018",
        "name_en": "Temple Police Chowky",
        "name_mr": "मंदिर पोलीस चौकी",
        "name_hi": "मंदिर पुलिस चौकी",
        "facility_type": "police",
        "lat": 17.6781, "lon": 75.3290,
        "contact_phone": "112", "is_24x7": True,
        "details": {"staffing": "Police on duty"},
    },
    {
        "external_id": "fac-019",
        "name_en": "Ghat Police Outpost",
        "name_mr": "घाट पोलीस चौकी",
        "name_hi": "घाट पुलिस चौकी",
        "facility_type": "police",
        "lat": 17.6810, "lon": 75.3270,
        "contact_phone": "112", "is_24x7": True,
        "details": {"staffing": "Police on duty"},
    },
    {
        "external_id": "fac-020",
        "name_en": "Temple Lost & Found Desk",
        "name_mr": "मंदिर हरवले-सापडले कक्ष",
        "name_hi": "मंदिर खोया-पाया केंद्र",
        "facility_type": "lost_found_desk",
        "lat": 17.6779, "lon": 75.3286,
        "contact_phone": "1800-233-1000", "is_24x7": True,
        "details": {"staffing": "Volunteer staffed", "announcements": True},
    },
]

# Categories the /api/facilities/nearby endpoint exposes. `accommodation` is a
# valid query with no seeded rows yet — the seed spec listed rest shelters but
# no overnight accommodation, so it returns an empty list rather than inventing
# places for pilgrims to sleep.
FACILITY_CATEGORIES: tuple[str, ...] = (
    "medical", "water", "toilet", "rest", "food", "accommodation",
)


# --- palkhi route -----------------------------------------------------------

DEFAULT_ROUTE_ID = "alandi_pandharpur"

ROUTE_WAYPOINTS: list[dict[str, Any]] = [
    {"sequence": 1, "name_en": "Alandi", "name_mr": "आळंदी", "name_hi": "आलंदी",
     "lat": 18.6773, "lon": 73.8987, "is_halt": True,
     "landmark": "Sant Dnyaneshwar Maharaj Samadhi Mandir"},
    {"sequence": 2, "name_en": "Pune", "name_mr": "पुणे", "name_hi": "पुणे",
     "lat": 18.5089, "lon": 73.8646, "is_halt": True, "landmark": "Bhavani Peth"},
    {"sequence": 3, "name_en": "Saswad", "name_mr": "सासवड", "name_hi": "सासवड",
     "lat": 18.3450, "lon": 74.0330, "is_halt": True, "landmark": "Sopandev Samadhi"},
    {"sequence": 4, "name_en": "Jejuri", "name_mr": "जेजुरी", "name_hi": "जेजुरी",
     "lat": 18.2770, "lon": 74.1600, "is_halt": True, "landmark": "Khandoba Temple"},
    {"sequence": 5, "name_en": "Walhe", "name_mr": "वाल्हे", "name_hi": "वाल्हे",
     "lat": 18.1670, "lon": 74.2500, "is_halt": True, "landmark": "Walhe village ground"},
    {"sequence": 6, "name_en": "Lonand", "name_mr": "लोणंद", "name_hi": "लोणंद",
     "lat": 18.0330, "lon": 74.2000, "is_halt": True, "landmark": "Lonand halt ground"},
    {"sequence": 7, "name_en": "Taradgaon", "name_mr": "तरडगाव", "name_hi": "तरडगांव",
     "lat": 17.9800, "lon": 74.3300, "is_halt": True, "landmark": "Taradgaon ringan ground"},
    {"sequence": 8, "name_en": "Phaltan", "name_mr": "फलटण", "name_hi": "फलटण",
     "lat": 17.9900, "lon": 74.4300, "is_halt": True, "landmark": "Phaltan town"},
    {"sequence": 9, "name_en": "Barad", "name_mr": "बरड", "name_hi": "बरड",
     "lat": 17.8500, "lon": 74.5500, "is_halt": True, "landmark": "Barad ringan ground"},
    {"sequence": 10, "name_en": "Natepute", "name_mr": "नातेपुते", "name_hi": "नातेपुते",
     "lat": 17.9000, "lon": 74.7300, "is_halt": True, "landmark": "Natepute halt"},
    {"sequence": 11, "name_en": "Malshiras", "name_mr": "माळशिरस", "name_hi": "माळशिरस",
     "lat": 17.8500, "lon": 74.9200, "is_halt": True, "landmark": "Malshiras town"},
    {"sequence": 12, "name_en": "Velapur", "name_mr": "वेळापूर", "name_hi": "वेलापुर",
     "lat": 17.7800, "lon": 75.0300, "is_halt": True, "landmark": "Velapur halt"},
    {"sequence": 13, "name_en": "Bhandishegaon", "name_mr": "भंडीशेगाव", "name_hi": "भंडीशेगांव",
     "lat": 17.7400, "lon": 75.1600, "is_halt": True, "landmark": "Bhandishegaon ground"},
    {"sequence": 14, "name_en": "Wakhari", "name_mr": "वाखरी", "name_hi": "वाखरी",
     "lat": 17.6903, "lon": 75.2787, "is_halt": True, "zone_ref": None,
     "landmark": "Wakhari halt ground — final ringan"},
    {"sequence": 15, "name_en": "Isbavi", "name_mr": "इसबावी", "name_hi": "इसबावी",
     "lat": 17.6702, "lon": 75.3178, "is_halt": False, "zone_ref": None,
     "landmark": "Isbavi camping ground"},
    {"sequence": 16, "name_en": "Chandrabhaga Ghat", "name_mr": "चंद्रभागा घाट",
     "name_hi": "चंद्रभागा घाट", "lat": 17.6812, "lon": 75.3262, "is_halt": False,
     "zone_ref": "bhima-ghat", "landmark": "River bathing ghat"},
    {"sequence": 17, "name_en": "Shri Vitthal Rukmini Temple", "name_mr": "श्री विठ्ठल रुक्मिणी मंदिर",
     "name_hi": "श्री विट्ठल रुक्मिणी मंदिर", "lat": 17.6775, "lon": 75.3283, "is_halt": False,
     "zone_ref": "temple-main", "landmark": "Journey ends at Vitthal's feet"},
]


def localized_name(record: dict[str, Any], language: str) -> str:
    """Pick the localized name, falling back to English."""
    return record.get(f"name_{language}") or record["name_en"]
