"""Loaders turning municipal open-data dumps into a common Record stream plus
official same-issue link sets.

Every loader returns (records, links) where
    records : list[Record] sorted by arrival time
    links   : dict child_rid -> parent_rid   (official workflow linkage)

No field listed in schema.POST_HOC_FIELDS is exposed on Record.
"""
from __future__ import annotations

import csv
import os
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from .schema import Record

DATA = Path(os.environ.get("PSID_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))


def _dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()
                                           and len(value) >= 12):
        ms = float(value)
        return datetime.fromtimestamp(ms / 1000.0)
    v = str(value).strip()
    for fmt, cut in (("%Y-%m-%d %H:%M:%S.%f", 26), ("%Y-%m-%d %H:%M:%S", 19),
                     ("%Y-%m-%dT%H:%M:%S.%f", 26), ("%Y-%m-%dT%H:%M:%S", 19),
                     ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(v[:cut], fmt)
        except ValueError:
            continue
    return None


def load_san_diego(year: int = 2024, path: Path | None = None,
                   include_post_hoc: bool = False
                   ) -> Tuple[List[Record], Dict[str, str]]:
    path = path or DATA / "SanDiegoGetItDone" / f"closed{year}" / \
        f"get_it_done_requests_closed_{year}_datasd.csv"
    recs: List[Record] = []
    links: Dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            rid = (row.get("service_request_id") or "").strip()
            when = _dt(row.get("date_requested", ""))
            if not rid or when is None:
                continue
            parent = (row.get("service_request_parent_id") or "").strip()
            if parent:
                links[rid] = parent
            lat = row.get("lat", "")
            lng = row.get("lng", "")
            extra = {}
            if include_post_hoc:
                extra = {"date_closed": row.get("date_closed", ""),
                         "status": row.get("status", ""),
                         "case_age_days": row.get("case_age_days", ""),
                         "floc": row.get("floc", ""),
                         "iamfloc": row.get("iamfloc", "")}
            recs.append(Record(
                rid=rid, city="san_diego", when=when,
                service=(row.get("service_name") or "").strip(),
                service_detail=(row.get("service_name_detail") or "").strip(),
                address_raw=(row.get("street_address") or "").strip(),
                zipcode=(row.get("zipcode") or "").strip(),
                description=(row.get("public_description") or "").strip(),
                lat=float(lat) if lat else None,
                lng=float(lng) if lng else None,
                extra=extra,
            ))
    recs.sort(key=lambda r: (r.when, r.rid))
    return recs, links


def load_chicago(path: Path | None = None
                 ) -> Tuple[List[Record], Dict[str, str]]:
    path = path or DATA / "Chicago311" / "2026Q1" / "chicago311_2026q1_six_fields.csv"
    recs: List[Record] = []
    links: Dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            rid = (row.get("sr_number") or "").strip()
            when = _dt(row.get("created_date", ""))
            if not rid or when is None:
                continue
            parent = (row.get("parent_sr_number") or "").strip()
            if parent:
                links[rid] = parent
            addr = (row.get("street_address") or "").strip()
            recs.append(Record(
                rid=rid, city="chicago", when=when,
                service=(row.get("sr_type") or "").strip(),
                service_detail="",
                address_raw=addr,
                zipcode="",
                description="",
            ))
    recs.sort(key=lambda r: (r.when, r.rid))
    return recs, links


def load_louisville(month: str = "June2024", path: Path | None = None
                    ) -> Tuple[List[Record], Dict[str, str]]:
    """ArcGIS REST page dumps. Louisville carries no official parent link, so the
    link dict is empty; human cluster gold is loaded separately."""
    base = path or DATA / "Louisville311" / month
    recs: List[Record] = []
    for page in sorted(base.glob("page_*.json")):
        blob = json.loads(page.read_text(encoding="utf-8"))
        for feat in blob.get("features", []):
            a = feat.get("attributes", {})
            rid = str(a.get("service_request_id") or a.get("ObjectId") or "").strip()
            when = _dt(str(a.get("requested_datetime") or ""))
            if not rid or when is None:
                continue
            lat, lng = a.get("latitude"), a.get("longitude")
            recs.append(Record(
                rid=rid, city="louisville", when=when,
                service=str(a.get("service_name") or ""),
                service_detail="",
                address_raw=str(a.get("address") or ""),
                zipcode="",
                description=str(a.get("description") or ""),
                lat=float(lat) if lat not in (None, "") else None,
                lng=float(lng) if lng not in (None, "") else None,
            ))
    recs.sort(key=lambda r: (r.when, r.rid))
    return recs, {}


def louisville_bill_fields(month: str = "June2024") -> List[str]:
    base = DATA / "Louisville311" / month
    page = sorted(base.glob("page_*.json"))[0]
    blob = json.loads(page.read_text(encoding="utf-8"))
    return [f.get("name", "") for f in blob.get("fields", [])]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print("louisville fields:", louisville_bill_fields())
    for name, fn in (("san_diego", load_san_diego), ("chicago", load_chicago),
                     ("louisville", load_louisville)):
        recs, links = fn()
        print(f"{name:12s} records={len(recs):7,} links={len(links):7,} "
              f"span={recs[0].when:%Y-%m-%d}..{recs[-1].when:%Y-%m-%d}")
