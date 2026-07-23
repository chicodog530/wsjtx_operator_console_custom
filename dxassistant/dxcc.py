from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Entity:
    id: int = 0
    prefix: str = ""
    name: str = "Unknown"
    continent: str = ""
    cq_zone: int = 0
    itu_zone: int = 0
    flag: str = ""
    known: bool = False


class DxccDatabase:
    def __init__(self, path: Path) -> None:
        records = json.loads(path.read_text(encoding="utf-8"))
        self.records = sorted(records, key=lambda item: len(item["prefix"]), reverse=True)
        self.by_name = {row["name"]: row for row in records}

    @staticmethod
    def normalize_call(call: str) -> str:
        call = (call or "").strip().upper()
        if "/" not in call:
            return call
        left, right = call.split("/", 1)
        if right in {"P", "M", "MM", "QRP", "R", "A"}:
            return left
        if len(left) <= 4 and len(right) > len(left):
            return right
        return left

    @staticmethod
    def _entity(row: dict, prefix: str | None = None) -> Entity:
        return Entity(
            id=row["id"],
            prefix=prefix or row["prefix"],
            name=row["name"],
            continent=row["continent"],
            cq_zone=row["cq_zone"],
            itu_zone=row["itu_zone"],
            flag=row["flag"],
            known=True,
        )

    def _block_lookup(self, call: str) -> Entity | None:
        one = call[:1]
        two = call[:2]

        # United States: K, N, W and AA-AL.
        if one in {"K", "N", "W"} or (
            len(two) == 2 and two[0] == "A" and "A" <= two[1] <= "L"
        ):
            return self._entity(self.by_name["United States"], two)

        # Canada: CF-CK, CY-CZ, VA-VG, VO, VX-VY.
        if two in {
            "CF","CG","CH","CI","CJ","CK","CY","CZ",
            "VA","VB","VC","VD","VE","VF","VG","VO","VX","VY",
        }:
            return self._entity(self.by_name["Canada"], two)

        # Australia: AX, VH-VN, VZ.
        if two in {"AX", "VZ"} or (
            len(two) == 2 and two[0] == "V" and "H" <= two[1] <= "N"
        ):
            return self._entity(self.by_name["Australia"], two)

        # England: M and common 2E/2M intermediate-license calls.
        if one == "M" or call.startswith(("2E", "2M")):
            return self._entity(self.by_name["England"], two)

        # Germany: DA-DR.
        if len(two) == 2 and two[0] == "D" and "A" <= two[1] <= "R":
            return self._entity(self.by_name["Germany"], two)

        # Mexico: XA-XI.
        if len(two) == 2 and two[0] == "X" and "A" <= two[1] <= "I":
            return self._entity(self.by_name["Mexico"], two)

        if call.startswith("XV"):
            return self._entity(self.by_name["Vietnam"], "XV")

        return None

    def lookup(self, call: str) -> Entity:
        normalized = self.normalize_call(call)

        # Preserve specific entries such as KH6 Hawaii and KL Alaska first.
        for row in self.records:
            if normalized.startswith(row["prefix"]):
                return self._entity(row)

        block = self._block_lookup(normalized)
        return block if block else Entity()
