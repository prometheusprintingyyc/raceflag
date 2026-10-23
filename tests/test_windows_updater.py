"""Tests for tools/update-raceflag.ps1 (Windows PowerShell 5.1), run through powershell.exe."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "update-raceflag.ps1"
CMD = ROOT / "tools" / "Update RaceFlag.cmd"

pytestmark = pytest.mark.skipif(not shutil.which("powershell"), reason="needs Windows PowerShell")

HOST_KEY_WARNING = """@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
IT IS POSSIBLE THAT SOMEONE IS DOING SOMETHING NASTY!
Host key verification failed."""


def ps(command, extra_env=None, timeout=60):
    env = dict(os.environ, RACEFLAG_UPDATER_NO_MAIN="1")
    env.update(extra_env or {})
    full = f". '{SCRIPT}'; {command}"
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", full],
        capture_output=True, text=True, env=env, timeout=timeout,
    )


def ps_json(command, **kw):
    r = ps(f"({command}) | ConvertTo-Json -Compress", **kw)
    assert r.returncode == 0, r.stderr + r.stdout
    return json.loads(r.stdout)


def lines_arg(lines):
    return "@(" + ",".join("'" + l.replace("'", "''") + "'" for l in lines) + ")"


class _Handler(BaseHTTPRequestHandler):
    version_reported = "0.2.34"
    delay = 0

    def do_GET(self):
        if self.path == "/api/update/check":
            if self.delay:
                time.sleep(self.delay)
            body = json.dumps({"current": self.version_reported, "latest": "v0.2.35",
                               "update_available": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


def start_server(version="0.2.34", port=0, delay=0):
    handler = type("H", (_Handler,), {"version_reported": version, "delay": delay})
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def free_port():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    srv.server_close()
    return port


def test_script_parses():
    data = SCRIPT.read_bytes()
    data.decode("ascii")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('{SCRIPT}',[ref]$t,[ref]$e);"
         "if($e.Count -gt 0){$e|ForEach-Object{$_.Message};exit 1}"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_remote_command():
    got = ps_json("Get-RemoteCommand -Tag v9.9.9 -Repo a/b")
    assert got == ("curl -fsSL --connect-timeout 20 --max-time 120 "
                   "https://raw.githubusercontent.com/a/b/v9.9.9/tools/"
                   "raceflag-remote-update.sh | sudo bash -s -- v9.9.9 a/b")


def test_ssh_arguments_use_raceflag_known_hosts():
    args = ps_json("Get-SshArguments -User bob -Address raceflag.local "
                   "-KnownHosts 'C:/x y/known_hosts' -Command 'echo hi'")
    if isinstance(args, str):
        args = [args]
    assert "StrictHostKeyChecking=accept-new" in args
    assert "UserKnownHostsFile='C:/x y/known_hosts'" in args
    assert "bob@raceflag.local" in args
    assert "ServerAliveInterval=15" in args and "ServerAliveCountMax=4" in args
    assert args[-1] == "echo hi"


def test_done_marker_counts_as_success_despite_ssh_exit_code():
    lines = ["STEP check", "STEP download", "STEP verify", "STEP packages", "STEP install",
             "STEP version", "STEP restart", "DONE", "Connection to x closed by remote host."]
    got = ps_json(f"Read-RemoteResult -Lines {lines_arg(lines)}")
    assert got["Ok"] is True


def test_error_line_is_reported():
    lines = ["STEP check", "STEP download", "ERROR The download is not a RaceFlag release."]
    got = ps_json(f"Read-RemoteResult -Lines {lines_arg(lines)}")
    assert got["Ok"] is False
    assert got["Error"] == "The download is not a RaceFlag release."


def test_no_done_and_no_error_is_a_failure():
    got = ps_json(f"Read-RemoteResult -Lines {lines_arg(['curl: (22) The requested URL returned error: 404'])}")
    assert got["Ok"] is False
    assert got["Error"] == ("Could not download the update script. Check the unit's "
                            "internet connection and the release number.")


def test_lost_connection_after_steps_is_explained():
    lines = ['STEP check', 'STEP download', 'client_loop: send disconnect: Broken pipe']
    got = ps_json(f"Read-RemoteResult -Lines {lines_arg(lines)}")
    assert got["Ok"] is False
    assert got["Error"] == ("The connection to RaceFlag was lost during the update. "
                            "Wait two minutes, then run this again.")


@pytest.mark.parametrize("text", [
    "ssh: connect to host raceflag.local port 22: Connection refused",
    "ssh: connect to host 10.0.0.9 port 22: Connection timed out",
    "ssh: connect to host 10.0.0.9 port 22: Operation timed out",
    "ssh: Could not resolve hostname raceflag.local: No such host is known."])
def test_ssh_connection_failure_is_explained(text):
    got = ps_json(f"Read-RemoteResult -Lines {lines_arg([text])}")
    assert got["Ok"] is False
    assert got["Error"] == ("Couldn't connect to RaceFlag over SSH. Check it's on and on this "
                            "network. Nothing was changed.")


def test_ssh_output_with_a_changed_host_key_is_explained():
    assert ps_json(f"Test-HostKeyChanged -Lines {lines_arg(HOST_KEY_WARNING.splitlines())}") is True
    assert ps_json(f"Test-HostKeyChanged -Lines {lines_arg(['STEP check', 'DONE'])}") is False


def test_unit_version_and_unreachable():
    srv = start_server("0.2.34")
    try:
        port = srv.server_address[1]
        assert ps_json(f"Get-UnitVersion -Address 127.0.0.1 -Port {port}") == {
            "Version": "0.2.34", "Reason": ""}
    finally:
        srv.shutdown()
        srv.server_close()
    got = ps_json(f"Get-UnitVersion -Address 127.0.0.1 -Port {free_port()}")
    assert got == {"Version": None, "Reason": "unreachable"}


def test_slow_unit_is_still_read_with_the_default_timeout():
    srv = start_server("0.2.34", delay=6)
    try:
        port = srv.server_address[1]
        got = ps_json(f"Get-UnitVersion -Address 127.0.0.1 -Port {port}", timeout=90)
        assert got == {"Version": "0.2.34", "Reason": ""}
    finally:
        srv.shutdown()
        srv.server_close()


def test_unit_that_never_answers_is_a_timeout():
    srv = start_server("0.2.34", delay=8)
    try:
        port = srv.server_address[1]
        got = ps_json(f"Get-UnitVersion -Address 127.0.0.1 -Port {port} -TimeoutSec 2", timeout=90)
        assert got == {"Version": None, "Reason": "timeout"}
    finally:
        srv.shutdown()
        srv.server_close()


def run_script(tmp_path, *args):
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path / "local"))
    env.pop("RACEFLAG_UPDATER_NO_MAIN", None)
    (tmp_path / "local").mkdir(exist_ok=True)
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
        capture_output=True, text=True, env=env, timeout=120)


def test_slow_unit_on_first_check_is_explained(tmp_path):
    # The default first-check timeout is 25 s, so the fake answers after 30 s.
    srv = start_server("0.2.34", delay=30)
    try:
        port = srv.server_address[1]
        r = run_script(tmp_path, "-Address", "127.0.0.1", "-Port", str(port), "-Tag", "v9.9.9",
                       "-User", "u", "-Yes", "-DryRun")
    finally:
        srv.shutdown()
        srv.server_close()
    assert ("RaceFlag answered too slowly - it may not reach the internet. "
            "Try again in a minute.") in r.stdout
    assert "Can't reach" not in r.stdout


def test_capital_v_tag_is_refused(tmp_path):
    r = run_script(tmp_path, "-Address", "127.0.0.1", "-Port", "1", "-Tag", "V9.9.9",
                   "-User", "u", "-Yes", "-DryRun")
    assert "That isn't a RaceFlag release number (expected vX.Y.Z)." in r.stdout


def test_ssh_tools_prefer_windows_openssh():
    exe = Path(os.environ.get("SystemRoot", "C:" + chr(92) + "Windows")) / "System32" / "OpenSSH" / "ssh.exe"
    if not exe.exists():
        pytest.skip("needs Windows' OpenSSH client")
    got = ps_json("Get-SshTools")
    assert Path(got["Ssh"]).name == "ssh.exe"
    assert "openssh" in got["Ssh"].lower()
    assert Path(got["Keygen"]).name == "ssh-keygen.exe"
    assert Path(got["Keygen"]).parent == Path(got["Ssh"]).parent


def test_wait_for_restart_needs_the_unit_to_go_away():
    srv = start_server("0.2.35")
    try:
        port = srv.server_address[1]
        got = ps_json(f"Wait-ForRestart -Address 127.0.0.1 -Port {port} -Version 0.2.35 "
                      "-WaitSeconds 4 -PollSeconds 1")
        assert got is False
    finally:
        srv.shutdown()
        srv.server_close()

    port = free_port()
    holder = {}

    def later():
        time.sleep(2.5)
        holder["srv"] = start_server("0.2.35", port)

    t = threading.Thread(target=later)
    t.start()
    try:
        got = ps_json(f"Wait-ForRestart -Address 127.0.0.1 -Port {port} -Version 0.2.35 "
                      "-WaitSeconds 12 -PollSeconds 1", timeout=90)
        assert got is True
    finally:
        t.join()
        if "srv" in holder:
            holder["srv"].shutdown()
            holder["srv"].server_close()


@pytest.mark.skipif(not shutil.which("ssh"), reason="needs ssh.exe")
def test_dry_run_changes_nothing(tmp_path):
    srv = start_server("9.9.9")
    try:
        port = srv.server_address[1]
        env = dict(os.environ, LOCALAPPDATA=str(tmp_path))
        env.pop("RACEFLAG_UPDATER_NO_MAIN", None)
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
             "-Address", "127.0.0.1", "-Port", str(port), "-Tag", "v9.9.9", "-User", "u",
             "-Yes", "-DryRun"],
            capture_output=True, text=True, env=env, timeout=60)
    finally:
        srv.shutdown()
        srv.server_close()
    out = r.stdout
    assert r.returncode == 0, out + r.stderr
    assert "Install v9.9.9 on RaceFlag v9.9.9 at 127.0.0.1? Its settings are kept. (Y/N)" in out
    assert "already" in out
    assert "u@127.0.0.1" in out
    assert "raceflag-remote-update.sh" in out
    assert "Dry run: nothing was changed." in out
    assert not (tmp_path / "RaceFlag").exists()


def test_cmd_wrapper():
    text = CMD.read_text()
    assert "-ExecutionPolicy Bypass" in text
    assert "%~dp0update-raceflag.ps1" in text
    assert "pause" in text


@pytest.mark.skipif(not shutil.which("ssh.exe"), reason="needs ssh.exe")
def test_known_hosts_option_with_spaces_is_one_file(tmp_path):
    kh = tmp_path / "A  B" / "known_hosts"
    args = ps_json(f"Get-SshArguments -User u -Address x -KnownHosts '{kh}' -Command c")
    option = [a for a in args if a.startswith("UserKnownHostsFile=")][0]
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"ssh.exe -G -o \"{option}\" x"],
                       capture_output=True, text=True, timeout=30)
    entries = [l for l in r.stdout.splitlines() if l.lower().startswith("userknownhostsfile ")]
    assert len(entries) == 1, r.stdout + r.stderr
    value = entries[0].split(" ", 1)[1].strip()
    assert value.replace("\\", "/").rstrip("'\"").lstrip("'\"") == str(kh).replace("\\", "/")


BS = chr(92)


def _q(s):
    return "'" + str(s).replace("'", "''") + "'"


def test_known_hosts_option_plain_space_apostrophe_cases():
    assert ps_json("Get-KnownHostsOption -KnownHosts 'C:/Users/jo/known_hosts'") ==         "UserKnownHostsFile=C:/Users/jo/known_hosts"
    assert ps_json("Get-KnownHostsOption -KnownHosts 'C:/Users/J S/known_hosts'") ==         "UserKnownHostsFile='C:/Users/J S/known_hosts'"
    # ssh rejects a bare apostrophe ("invalid quotes"); it must be backslash-escaped.
    assert ps_json("Get-KnownHostsOption -KnownHosts 'C:/Users/O''Brien/known_hosts'") ==         "UserKnownHostsFile=C:/Users/O" + BS + "'Brien/known_hosts"
    assert ps_json("Get-KnownHostsOption -KnownHosts 'C:/Users/O''Brien S/known_hosts'") ==         "UserKnownHostsFile='C:/Users/O" + BS + "'Brien S/known_hosts'"


def test_known_hosts_option_omitted_when_it_cannot_be_spelled_safely():
    # An apostrophe right after a backslash cannot be escaped; no 8.3 short path exists here.
    path = "Z:" + BS + "no such drive" + BS + "'Brien" + BS + "known_hosts"
    r = ps(f"$o = Get-KnownHostsOption -KnownHosts {_q(path)}; if ($null -eq $o) {{ 'NULL' }} else {{ $o }}")
    assert r.stdout.strip() == "NULL", r.stdout + r.stderr
    args = ps_json(f"Get-SshArguments -User u -Address x -KnownHosts {_q(path)} -Command c")
    assert not [a for a in args if str(a).startswith("UserKnownHostsFile")]
    assert "StrictHostKeyChecking=accept-new" in args
    assert args[-1] == "c"


@pytest.mark.skipif(not shutil.which("ssh.exe"), reason="needs ssh.exe")
@pytest.mark.parametrize("folder", ["O'Brien", "O'Brien Smith"])
def test_known_hosts_option_with_apostrophe_is_one_file(tmp_path, folder):
    kh = tmp_path / folder / "known_hosts"
    option = ps_json(f"Get-KnownHostsOption -KnownHosts {_q(kh)}")
    # Windows' own OpenSSH client (the one customers have); Git's bundled ssh parses differently.
    exe = os.path.join(os.environ.get("SystemRoot", "C:" + BS + "Windows"), "System32", "OpenSSH", "ssh.exe")
    if not os.path.exists(exe):
        pytest.skip("needs Windows' OpenSSH client")
    r = subprocess.run([exe, "-G", "-o", option, "x"], capture_output=True, text=True, timeout=30)
    entries = [l for l in r.stdout.splitlines() if l.lower().startswith("userknownhostsfile ")]
    assert len(entries) == 1, r.stdout + r.stderr
    value = entries[0].split(" ", 1)[1].strip()
    assert value.replace(BS, "/") == str(kh).replace(BS, "/")


def _run_cmd(folder, *args):
    return subprocess.run(["cmd", "/c", str(folder / "Update RaceFlag.cmd"), *args],
                          input="\r\n", capture_output=True, text=True, timeout=120,
                          env=dict(os.environ, LOCALAPPDATA=str(folder)))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch file")
def test_cmd_without_the_script_asks_to_extract(tmp_path):
    folder = tmp_path / "Update Files"
    folder.mkdir()
    shutil.copy(CMD, folder / "Update RaceFlag.cmd")
    r = _run_cmd(folder)
    assert "Extract all the files first, then double-click Update RaceFlag.cmd again." in r.stdout
    assert r.returncode == 1


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("ssh"), reason="Windows batch file")
def test_cmd_with_both_files_in_a_folder_with_a_space_runs_the_script(tmp_path):
    folder = tmp_path / "Update Files"
    folder.mkdir()
    shutil.copy(CMD, folder / "Update RaceFlag.cmd")
    shutil.copy(SCRIPT, folder / "update-raceflag.ps1")
    srv = start_server("9.9.9")
    try:
        r = _run_cmd(folder, "-DryRun", "-Address", "127.0.0.1", "-Port",
                     str(srv.server_address[1]), "-Tag", "v9.9.9", "-User", "u", "-Yes")
    finally:
        srv.shutdown()
        srv.server_close()
    assert "Dry run: nothing was changed." in r.stdout, r.stdout + r.stderr
