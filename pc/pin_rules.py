"""Regole del PIN amministratore (in un modulo a parte per poterle provare con i test automatici)."""
from typing import Optional

DEFAULT_PIN = "1234"
MIN_PIN_LENGTH = 6
MAX_PIN_LENGTH = 12


def is_default_pin(pin) -> bool:
    """PIN predefinito (o assente): con questo PIN l'accesso da internet è bloccato."""
    return str(pin if pin is not None else "").strip() in ("", DEFAULT_PIN)


def validate_new_pin(pin: str) -> Optional[str]:
    """Messaggio d'errore se il nuovo PIN non è accettabile, altrimenti None.
    Solo cifre: i campi PIN dell'app Android accettano soltanto numeri."""
    p = (pin or "").strip()
    if not p.isdigit():
        return "Il PIN deve contenere solo numeri."
    if len(p) < MIN_PIN_LENGTH:
        return f"Il PIN deve avere almeno {MIN_PIN_LENGTH} cifre."
    if len(p) > MAX_PIN_LENGTH:
        return f"Il PIN può avere al massimo {MAX_PIN_LENGTH} cifre."
    if len(set(p)) == 1 or p in "01234567890123456789" or p in "98765432109876543210":
        return "Il PIN è troppo facile (cifre tutte uguali o in sequenza): scegline un altro."
    return None
