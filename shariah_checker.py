"""shariah_checker.py — Determines shariah compliance status.

Policy (strict):
  * COMPLIANT only if symbol is in the verified KMI-30 constituent list
    (with source + verification date) OR has a documented alternative
    verification entry in config.OTHER_COMPLIANT.
  * Everything else -> "Needs manual verification" and EXCLUDED from the
    top-10 ranking. We never assume compliance.
  * Warns when the verification snapshot is stale (KMI-30 is recomposed
    semi-annually; financial ratios change every quarter).
"""

import logging
from datetime import datetime

import config

log = logging.getLogger("shariah")

# KMI screening criteria (for the README / manual checks; the engine relies
# on the official index screening rather than recomputing ratios itself):
SCREENING_CRITERIA = [
    "Core business must be halal",
    "Interest-bearing debt / total assets < 37%",
    "Illiquid assets / total assets > 25%",
    "Net liquid assets per share < market price",
    "Non-compliant investments / total assets < 33%",
    "Non-compliant income / total revenue < 5%",
]


def _staleness_warning():
    try:
        age = (datetime.now()
               - datetime.fromisoformat(config.KMI30_VERIFICATION_DATE)).days
    except Exception:
        return "Verification date unparsable — re-verify the KMI-30 list."
    if age > config.SHARIAH_STALE_DAYS:
        return (f"Shariah verification snapshot is {age} days old. KMI-30 is "
                "recomposed semi-annually — re-verify before relying on it.")
    return None


def check(symbol):
    """Return dict: status, eligible_for_ranking, source, notes[]"""
    notes = []
    stale = _staleness_warning()
    if stale:
        notes.append(stale)

    if symbol in config.LOWER_PRICE_VERIFIED:
        return {'symbol': symbol, 'status': 'Compliant (KMI All Share)',
                'eligible_for_ranking': True,
                'source': f'{config.LOWER_PRICE_SOURCE}, checked {config.LOWER_PRICE_VERIFIED_ON}',
                'notes': notes}

    if symbol in config.KMI30_VERIFIED:
        return {
            "symbol": symbol, "status": "Compliant (KMI-30 constituent)",
            "eligible_for_ranking": True,
            "source": f"{config.KMI30_SOURCE}, effective "
                      f"{config.KMI30_VERIFICATION_DATE}",
            "notes": notes,
        }

    if symbol in getattr(config, "KMIALLSHR_VERIFIED", set()):
        try:
            age = (datetime.now() - datetime.fromisoformat(
                config.KMIALLSHR_VERIFICATION_DATE)).days
            if age > config.SHARIAH_STALE_DAYS:
                notes.append(
                    f"KMI All-Share verification snapshot is {age} days old — "
                    "re-verify at the next semi-annual recomposition.")
        except Exception:
            pass
        return {
            "symbol": symbol,
            "status": "Compliant (KMI All-Share constituent)",
            "eligible_for_ranking": True,
            "source": f"{config.KMIALLSHR_SOURCE}, effective "
                      f"{config.KMIALLSHR_VERIFICATION_DATE}",
            "notes": notes,
        }

    if symbol in config.OTHER_COMPLIANT:
        entry = config.OTHER_COMPLIANT[symbol]
        notes.append(entry["verify_note"])
        return {
            "symbol": symbol,
            "status": "Compliant (non-KMI-30 route — verify quarterly)",
            "eligible_for_ranking": True,
            "source": entry["reason"],
            "notes": notes,
        }

    notes.append("Not found in any verified compliance source. Excluded "
                 "from top-10 ranking until manually verified against the "
                 "latest KMI All Share / SECP shariah list.")
    return {"symbol": symbol, "status": "Needs manual verification",
            "eligible_for_ranking": False, "source": "none", "notes": notes}


def check_all(symbols):
    return {s: check(s) for s in symbols}
