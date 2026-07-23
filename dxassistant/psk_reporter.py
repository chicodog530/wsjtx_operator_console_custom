from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode

import httpx


class PskReporterClient:
    ENDPOINT = "https://retrieve.pskreporter.info/query"

    async def who_heard_me(self, callsign: str, minutes: int = 60) -> list[dict[str, Any]]:
        params = {
            "senderCallsign": callsign.upper(),
            "flowStartSeconds": -max(60, minutes * 60),
            "rronly": 1,
            "noactive": 1,
        }
        async with httpx.AsyncClient(timeout=25, headers={"User-Agent":"-Radio-Command-Center/3.1"}) as client:
            response = await client.get(self.ENDPOINT, params=params)
            response.raise_for_status()
        root = ET.fromstring(response.text)
        reports=[]
        for node in root.iter():
            if not node.tag.lower().endswith("receptionreport"):
                continue
            a=node.attrib
            receiver=a.get("receiverCallsign","")
            if not receiver: continue
            ts=int(a.get("flowStartSeconds","0") or 0)
            reports.append({
                "receiver_call":receiver,
                "receiver_grid":a.get("receiverLocator","") or a.get("receiverLocatorExtended",""),
                "sender_grid":a.get("senderLocator",""),
                "frequency":int(a.get("frequency","0") or 0),
                "mode":a.get("modeInformation","") or a.get("mode", ""),
                "snr":float(a.get("sNR","0") or 0),
                "time":ts,
                "age_seconds":max(0,int(time.time())-ts) if ts else None,
            })
        reports.sort(key=lambda r:r.get("time",0), reverse=True)
        # de-duplicate receiver/frequency/mode, keep newest
        seen=set(); unique=[]
        for r in reports:
            key=(r["receiver_call"],r["frequency"],r["mode"])
            if key in seen: continue
            seen.add(key); unique.append(r)
        return unique[:250]
