"""Possible duplicate invitations, flagged for human review.

Nothing here is applied to the counts. The dashboard reports the verified
242 attending; these are warnings only, for someone to confirm or dismiss.

``people_at_risk`` is how many people the headline would drop by *if* the pair
turns out to be one person invited twice.
"""

HIGH = "HIGH"
MEDIUM = "MEDIUM"
CHECK = "CHECK"

DUPLICATES = [
    {
        "confidence": HIGH,
        "bucket": "Attending",
        "record_a": "Krish - 19165256440 - Attending, 2 people",
        "record_b": "Krish - 19195256440 - Attending, 2 people",
        "matches": "Identical name; the phone numbers differ only by a transposed digit (916 vs 919).",
        "effect": "Attending headcount overstated by 2.",
        "people_at_risk": 2,
    },
    {
        "confidence": HIGH,
        "bucket": "Attending",
        "record_a": "Bagya Mob - 19193686860 (sms) - Attending, 3 people",
        "record_b": "Bagya - skiphopbear@gmail.com (email) - Attending, 3 people",
        "matches": "Same first name, same party size of 3, same RSVP - one person answering on two channels.",
        "effect": "Attending headcount overstated by 3.",
        "people_at_risk": 3,
    },
    {
        "confidence": MEDIUM,
        "bucket": "Pending",
        "record_a": "Geeta Vemuri - 17278081003 - Sent, 5 invited",
        "record_b": "Geeta Vemuri - 17277271002 - Attending, 5 people",
        "matches": "Identical name; near-identical phone number.",
        "effect": "Pending overstated by 5 - she has already replied on the other record.",
        "people_at_risk": 5,
    },
    {
        "confidence": MEDIUM,
        "bucket": "Pending",
        "record_a": "Girish Jandhyala - 13365436288 - Sent, 4 invited",
        "record_b": "Girish Jandhyala - 13365542522 - Attending, 2 people",
        "matches": "Identical name, two phone numbers.",
        "effect": "Pending overstated by 4 - he has already replied on the other record.",
        "people_at_risk": 4,
    },
    {
        "confidence": MEDIUM,
        "bucket": "Pending",
        "record_a": "Lalitha Mob - 19197574873 - Page viewed, 5 invited",
        "record_b": "Lalitha - lalithapasupuleti@gmail.com - Attending, 3 people",
        "matches": "Same first name, sms record vs email record.",
        "effect": "Pending overstated by 5.",
        "people_at_risk": 5,
    },
    {
        "confidence": MEDIUM,
        "bucket": "Pending",
        "record_a": "Caldwell Velnambi - 16142265605 - Sent, 5 invited",
        "record_b": "Caldwell - 12145297764 - Attending, 2 people",
        "matches": "Same name, two phone numbers.",
        "effect": "Pending overstated by 5.",
        "people_at_risk": 5,
    },
    {
        "confidence": MEDIUM,
        "bucket": "Pending",
        "record_a": "Manosh - 19195937875 - Sent, 2 invited",
        "record_b": "MANOSH MAJUMDAR - mithuster@gmail.com - Attending, 3 people",
        "matches": "Same first name, sms record vs email record.",
        "effect": "Pending overstated by 2.",
        "people_at_risk": 2,
    },
    {
        "confidence": MEDIUM,
        "bucket": "Pending",
        "record_a": "Bobbie new - 13366092657 - Sent, 2 invited",
        "record_b": "Bobbie K - bobbie160482@gmail.com - Attending, 1 person",
        "matches": "Same first name, sms record vs email record.",
        "effect": "Pending overstated by 2.",
        "people_at_risk": 2,
    },
    {
        "confidence": MEDIUM,
        "bucket": "Pending",
        "record_a": "Girish Jammu - 19198646522 - Sent, 1 invited",
        "record_b": "Girish Jammu - 16178502323 - Page viewed, 2 invited",
        "matches": "Identical name, two phone numbers - both still unanswered.",
        "effect": "Pending overstated by 1.",
        "people_at_risk": 1,
    },
    {
        "confidence": CHECK,
        "bucket": "Attending",
        "record_a": "Anandhi Cary - 19193894912 - Attending, 5 people",
        "record_b": "Rochana Jayakumar - 19199309432 - Attending, 3 people",
        "matches": ("Anandhi's message lists her party as \"Jayakumar, Anandhi, Praveen, "
                    "Prasanth, Rochana + Baby\" - Rochana also has her own row."),
        "effect": "Attending may be overstated if Rochana is counted in both parties.",
        "people_at_risk": 0,
    },
    {
        "confidence": CHECK,
        "bucket": "Attending",
        "record_a": "Pavan Vemuri - 17276561611 - Attending, 1 person",
        "record_b": "Geeta Vemuri - 17277271002 - Attending, 5 people",
        "matches": "Pavan's own message says \"My sister may have included me with their group\".",
        "effect": "Attending may be overstated by 1 if he sits inside Geeta's party of 5.",
        "people_at_risk": 0,
    },
]

DATA_QUALITY_NOTES = [
    ("Ravichandran AT",
     'The phone number was exported as "9.19943E+11" - the source file truncated it to '
     "scientific notation and the real digits are gone. It is preserved exactly as found."),
    ("Three rows have no name",
     "Three attending records carry a phone number but no Full Name. They are kept and counted "
     "(2 + 4 + 2 = 8 people); the party shows as \"(name not recorded)\"."),
]


def review_totals():
    """How the headline would move if the flagged pairs were confirmed."""
    high = sum(d["people_at_risk"] for d in DUPLICATES if d["confidence"] == HIGH)
    medium = sum(d["people_at_risk"] for d in DUPLICATES if d["confidence"] == MEDIUM)
    return {
        "high_confidence_people": high,
        "medium_confidence_people": medium,
        "flagged_pairs": len(DUPLICATES),
    }
