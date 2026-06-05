import pytest
from pathlib import Path
from pytest_mock import MockerFixture
from raceflag.ota import OTAUpdater


LATEST_RELEASE = {
    "tag_name": "v0.2.0",
    "assets": [
        {"name": "raceflag-v0.2.0.tar.gz", "browser_download_url": "https://example.com/raceflag.tar.gz"}
    ],
}


@pytest.fixture
def updater(tmp_path):
    version_file = tmp_path / "version.txt"
    version_file.write_text("0.1.0")
    install_dir = tmp_path / "raceflag"
    install_dir.mkdir()
    return OTAUpdater(
        version_file=version_file,
        install_dir=install_dir,
        github_repo="prometheusprintingyyc/raceflag",
    )


@pytest.mark.asyncio
async def test_check_returns_no_update_when_current(updater, mocker: MockerFixture):
    mocker.patch.object(updater, "_fetch_latest_release", return_value={"tag_name": "v0.1.0", "assets": []})
    result = await updater.check()
    assert result["update_available"] is False
    assert result["current"] == "0.1.0"


@pytest.mark.asyncio
async def test_check_returns_update_available_when_newer(updater, mocker: MockerFixture):
    mocker.patch.object(updater, "_fetch_latest_release", return_value=LATEST_RELEASE)
    result = await updater.check()
    assert result["update_available"] is True
    assert result["latest"] == "v0.2.0"


@pytest.mark.asyncio
async def test_check_returns_no_update_on_api_error(updater, mocker: MockerFixture):
    mocker.patch.object(updater, "_fetch_latest_release", side_effect=Exception("network error"))
    result = await updater.check()
    assert result["update_available"] is False


def test_is_newer_version_detects_newer(updater):
    assert updater._is_newer("v0.2.0", "0.1.0") is True
    assert updater._is_newer("v1.0.0", "0.9.9") is True


def test_is_newer_version_same_is_not_newer(updater):
    assert updater._is_newer("v0.1.0", "0.1.0") is False


def test_is_newer_version_older_is_not_newer(updater):
    assert updater._is_newer("v0.0.9", "0.1.0") is False
