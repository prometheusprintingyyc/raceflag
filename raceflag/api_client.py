from __future__ import annotations
from datetime import date, datetime

import httpx

from raceflag.state import (
    DriverStanding, ConstructorStanding, NextRace, TEAM_COLORS, COUNTRY_FLAGS,
)

BASE_URL = "https://api.jolpi.ca/ergast"


def date_today() -> str:
    return date.today().isoformat()


class JolpicaClient:
    async def _get(self, url: str, **kwargs) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, **kwargs)
            resp.raise_for_status()
            return resp.json()

    async def fetch_driver_standings(self) -> list[DriverStanding]:
        try:
            data = await self._get(f"{BASE_URL}/f1/current/driverStandings.json")
        except Exception:
            return []
        try:
            rows = data["MRData"]["StandingsTable"]["StandingsLists"][0]["DriverStandings"]
        except (KeyError, IndexError):
            return []
        result = []
        for row in rows:
            team = row["Constructors"][0]["name"] if row.get("Constructors") else ""
            result.append(DriverStanding(
                position=int(row["position"]),
                full_name=f"{row['Driver']['givenName']} {row['Driver']['familyName']}",
                team=team,
                team_color=TEAM_COLORS.get(team, "#888888"),
                points=int(float(row["points"])),
            ))
        return result

    async def fetch_constructor_standings(self) -> list[ConstructorStanding]:
        try:
            data = await self._get(f"{BASE_URL}/f1/current/constructorStandings.json")
        except Exception:
            return []
        try:
            rows = data["MRData"]["StandingsTable"]["StandingsLists"][0]["ConstructorStandings"]
        except (KeyError, IndexError):
            return []
        result = []
        for row in rows:
            name = row["Constructor"]["name"]
            result.append(ConstructorStanding(
                position=int(row["position"]),
                name=name,
                team_color=TEAM_COLORS.get(name, "#888888"),
                points=int(float(row["points"])),
            ))
        return result

    async def fetch_next_race(self) -> NextRace:
        try:
            data = await self._get(f"{BASE_URL}/f1/current.json")
        except Exception:
            return NextRace()
        try:
            races = data["MRData"]["RaceTable"]["Races"]
        except (KeyError, IndexError):
            return NextRace()
        today = date_today()
        for race in races:
            if race["date"] >= today:
                country = race["Circuit"]["Location"]["country"]
                dt = datetime.strptime(race["date"], "%Y-%m-%d")
                return NextRace(
                    name=race["raceName"],
                    circuit=race["Circuit"]["circuitName"],
                    venue=race["Circuit"]["Location"]["locality"],
                    country_flag=COUNTRY_FLAGS.get(country, ""),
                    round_number=int(race["round"]),
                    race_date=dt.strftime("%A %d %B %Y"),
                )
        return NextRace()
