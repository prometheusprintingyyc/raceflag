import pytest
import httpx
from pytest_mock import MockerFixture
from raceflag.api_client import JolpicaClient
from raceflag.state import AppState, COUNTRY_FLAGS


DRIVER_STANDINGS_RESPONSE = {
    "MRData": {
        "StandingsTable": {
            "StandingsLists": [{
                "DriverStandings": [
                    {
                        "position": "1",
                        "points": "195",
                        "Driver": {"givenName": "Max", "familyName": "Verstappen"},
                        "Constructors": [{"name": "Red Bull Racing"}],
                    },
                    {
                        "position": "2",
                        "points": "171",
                        "Driver": {"givenName": "Lando", "familyName": "Norris"},
                        "Constructors": [{"name": "McLaren"}],
                    },
                ]
            }]
        }
    }
}

CONSTRUCTOR_STANDINGS_RESPONSE = {
    "MRData": {
        "StandingsTable": {
            "StandingsLists": [{
                "ConstructorStandings": [
                    {
                        "position": "1",
                        "points": "303",
                        "Constructor": {"name": "McLaren"},
                    },
                ]
            }]
        }
    }
}

RACE_SCHEDULE_RESPONSE = {
    "MRData": {
        "RaceTable": {
            "Races": [
                {
                    "round": "8",
                    "raceName": "Monaco Grand Prix",
                    "Circuit": {
                        "circuitName": "Circuit de Monaco",
                        "Location": {"locality": "Monte Carlo", "country": "Monaco"},
                    },
                    "date": "2025-05-25",
                },
                {
                    "round": "9",
                    "raceName": "Canadian Grand Prix",
                    "Circuit": {
                        "circuitName": "Circuit Gilles Villeneuve",
                        "Location": {"locality": "Montreal", "country": "Canada"},
                    },
                    "date": "2025-06-15",
                },
            ]
        }
    }
}


@pytest.fixture
def mock_client(mocker: MockerFixture):
    client = JolpicaClient()

    async def fake_get(url, **kwargs):
        if "driverStandings" in url:
            return DRIVER_STANDINGS_RESPONSE
        if "constructorStandings" in url:
            return CONSTRUCTOR_STANDINGS_RESPONSE
        if "current.json" in url:
            return RACE_SCHEDULE_RESPONSE
        return {}

    mocker.patch.object(client, "_get", side_effect=fake_get)
    return client


@pytest.mark.asyncio
async def test_fetch_driver_standings_returns_list(mock_client):
    standings = await mock_client.fetch_driver_standings()
    assert len(standings) == 2
    assert standings[0].full_name == "Max Verstappen"
    assert standings[0].position == 1
    assert standings[0].points == 195
    assert standings[1].full_name == "Lando Norris"


@pytest.mark.asyncio
async def test_fetch_constructor_standings_returns_list(mock_client):
    standings = await mock_client.fetch_constructor_standings()
    assert len(standings) == 1
    assert standings[0].name == "McLaren"
    assert standings[0].points == 303


@pytest.mark.asyncio
async def test_fetch_next_race_returns_future_race(mock_client, mocker):
    mocker.patch("raceflag.api_client.date_today", return_value="2025-06-01")
    race = await mock_client.fetch_next_race()
    assert race.name == "Canadian Grand Prix"
    assert race.round_number == 9
    assert race.race_date == "Sunday 15 June 2025"
    assert race.country_flag == COUNTRY_FLAGS.get("Canada", "")


@pytest.mark.asyncio
async def test_fetch_driver_standings_returns_empty_on_network_error(mocker: MockerFixture):
    client = JolpicaClient()
    mocker.patch.object(client, "_get", side_effect=Exception("network error"))
    result = await client.fetch_driver_standings()
    assert result == []


@pytest.mark.asyncio
async def test_fetch_next_race_returns_empty_on_network_error(mocker: MockerFixture):
    client = JolpicaClient()
    mocker.patch.object(client, "_get", side_effect=Exception("network error"))
    result = await client.fetch_next_race()
    assert result.name == ""
