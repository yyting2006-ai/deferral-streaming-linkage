"""Record schema and text/address normalisation (arrival-time fields only)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

# Fields that only become observable AFTER the request is adjudicated.
# They must never enter a prefix-safe feature vector.
POST_HOC_FIELDS = ("date_closed", "status", "case_age_days", "floc", "iamfloc",
                   "sap_notification_number", "referred", "case_record_type")

_DIRECTIONAL = {"N": "N", "S": "S", "E": "E", "W": "W", "NE": "NE", "NW": "NW",
                "SE": "SE", "SW": "SW", "NORTH": "N", "SOUTH": "S", "EAST": "E",
                "WEST": "W", "NORTHEAST": "NE", "NORTHWEST": "NW",
                "SOUTHEAST": "SE", "SOUTHWEST": "SW"}

_SUFFIX = {"STREET": "ST", "ST": "ST", "AVENUE": "AVE", "AVE": "AVE", "AV": "AVE",
           "BOULEVARD": "BLVD", "BLVD": "BLVD", "ROAD": "RD", "RD": "RD",
           "DRIVE": "DR", "DR": "DR", "LANE": "LN", "LN": "LN",
           "COURT": "CT", "CT": "CT", "PLACE": "PL", "PL": "PL",
           "WAY": "WAY", "TERRACE": "TER", "TER": "TER", "CIRCLE": "CIR",
           "CIR": "CIR", "HIGHWAY": "HWY", "HWY": "HWY", "PARKWAY": "PKWY",
           "PKWY": "PKWY", "TRAIL": "TRL", "TRL": "TRL", "ALLEY": "ALY"}

_WS_RE = re.compile(r"[^0-9a-z]+")


def _fold(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.lower()


def norm_text(text: str) -> str:
    """Aggressive folding for matching: lowercase, collapse non-alphanumerics."""
    return _WS_RE.sub(" ", _fold(text)).strip()


@dataclass(frozen=True)
class Address:
    """Normalised street address: number, street tokens, unit."""
    raw: str
    number: str
    street: str
    zip5: str

    @property
    def key(self) -> str:
        return f"{self.number}|{self.street}" if self.number else f"|{self.street}"

    @property
    def block(self) -> str:
        """Hundred-block key: 4312 -> 4300. Robust to mis-recorded house numbers."""
        if self.number.isdigit():
            n = int(self.number)
            return f"{n // 100 * 100}|{self.street}"
        return self.key


def parse_address(raw: str, zipcode: str = "") -> Address:
    t = norm_text(raw)
    if not t:
        return Address(raw=raw or "", number="", street="", zip5=(zipcode or "").strip()[:5])
    toks = t.split()
    number = ""
    if toks and toks[0].isdigit():
        number = toks[0]
        toks = toks[1:]
    # drop unit designators and everything after them
    out = []
    for tok in toks:
        if tok in ("apt", "unit", "ste", "suite", "#") or tok.startswith("#"):
            break
        out.append(tok)
    out = [_DIRECTIONAL.get(tok, tok) for tok in out]
    if out:
        out[-1] = _SUFFIX.get(out[-1], out[-1])
    return Address(raw=raw or "", number=number, street=" ".join(out),
                   zip5=(zipcode or "").strip()[:5])


@dataclass
class Record:
    """One complaint request, as observed at arrival time."""
    rid: str
    city: str
    when: datetime
    service: str
    service_detail: str
    address_raw: str
    zipcode: str
    description: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    extra: Dict[str, str] = field(default_factory=dict)

    # cached normalisations
    _addr: Optional[Address] = None
    _text: Optional[str] = None

    @property
    def addr(self) -> Address:
        if self._addr is None:
            self._addr = parse_address(self.address_raw, self.zipcode)
        return self._addr

    @property
    def service_key(self) -> str:
        return norm_text(f"{self.service} {self.service_detail}").strip()

    @property
    def text(self) -> str:
        if self._text is None:
            self._text = " ".join(x for x in (norm_text(self.description),
                                              self.service_key) if x)
        return self._text

    @property
    def day(self) -> int:
        return self.when.toordinal()

    def visible_dict(self) -> Dict[str, str]:
        """Exactly the fields a deployed system may read at arrival."""
        return {
            "rid": self.rid, "city": self.city,
            "when": self.when.isoformat(),
            "service": self.service, "service_detail": self.service_detail,
            "address": self.address_raw, "zipcode": self.zipcode,
            "description": self.description,
        }
