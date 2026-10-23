# Updates a RaceFlag unit over SSH, for units whose own update button can't work
# (SD card protection on). Double-click "Update RaceFlag.cmd" to run it.
# Needs Windows PowerShell 5.1 or newer and Windows' OpenSSH client. Keep this file ASCII.
param(
    [string]$Address = 'raceflag.local',
    [int]$Port = 8080,
    [string]$Tag = '',
    [string]$Repo = 'prometheusprintingyyc/raceflag',
    [string]$User = '',
    [switch]$Yes,
    [switch]$DryRun,
    [int]$WaitMinutes = 15
)

function Get-RemoteCommand {
    param([string]$Tag, [string]$Repo)
    return "curl -fsSL --connect-timeout 20 --max-time 120 https://raw.githubusercontent.com/$Repo/$Tag/tools/raceflag-remote-update.sh | sudo bash -s -- $Tag $Repo"
}

function Get-SshTools {
    # Windows' own OpenSSH first (Sysnative for a 32-bit host on 64-bit Windows), else ssh.exe
    # from PATH. ssh-keygen comes from the same folder. Ssh is $null if there is no client.
    $root = $env:SystemRoot
    if ($root) {
        foreach ($sub in @('Sysnative', 'System32')) {
            $exe = Join-Path (Join-Path (Join-Path $root $sub) 'OpenSSH') 'ssh.exe'
            if (Test-Path -LiteralPath $exe) {
                return @{ Ssh = $exe; Keygen = (Join-Path (Split-Path -Parent $exe) 'ssh-keygen.exe') }
            }
        }
    }
    if (Get-Command ssh.exe -ErrorAction SilentlyContinue) {
        return @{ Ssh = 'ssh.exe'; Keygen = 'ssh-keygen.exe' }
    }
    return @{ Ssh = $null; Keygen = $null }
}

function Get-KnownHostsPath {
    # The known_hosts file to give ssh, or $null if no safe spelling of the path exists.
    # ssh splits UserKnownHostsFile at whitespace and rejects a bare apostrophe ("invalid quotes").
    # An apostrophe is escaped as backslash-apostrophe, except right after a backslash (that
    # would read as an escaped backslash): such a path is replaced by its 8.3 short path.
    param([string]$KnownHosts)
    if (-not $KnownHosts) { return $null }
    if (-not $KnownHosts.Contains("\'")) { return $KnownHosts }
    try {
        $folder = Split-Path -Parent $KnownHosts
        if (-not (Test-Path $folder)) { [void](New-Item -ItemType Directory -Path $folder -Force -ErrorAction Stop) }
        $short = (New-Object -ComObject Scripting.FileSystemObject).GetFolder($folder).ShortPath
        $candidate = Join-Path $short 'known_hosts'
        if ($candidate -notmatch "[\s']") { return $candidate }
    } catch {
    }
    return $null
}

function Get-KnownHostsOption {
    param([string]$KnownHosts)
    $path = Get-KnownHostsPath -KnownHosts $KnownHosts
    if ($null -eq $path) { return $null }
    $escaped = $path.Replace("'", "\'")
    if ($path -match '\s') { return "UserKnownHostsFile='$escaped'" }
    return "UserKnownHostsFile=$escaped"
}

function Get-SshArguments {
    param([string]$User, [string]$Address, [string]$KnownHosts, [string]$Command)
    $result = @('-o', 'StrictHostKeyChecking=accept-new')
    $option = Get-KnownHostsOption -KnownHosts $KnownHosts
    if ($null -ne $option) { $result += @('-o', $option) }
    $result += @('-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4', "$User@$Address", $Command)
    return $result
}

function Read-RemoteResult {
    param([string[]]$Lines)
    $all = @($Lines)
    if ($all -contains 'DONE') {
        return @{ Ok = $true; Error = '' }
    }
    foreach ($line in $all) {
        if ($line -like 'ERROR *') {
            return @{ Ok = $false; Error = $line.Substring(6).Trim() }
        }
    }
    $sawStep = $false
    foreach ($line in $all) {
        if ($line -like 'STEP *') { $sawStep = $true }
    }
    if ($sawStep) {
        return @{ Ok = $false; Error = 'The connection to RaceFlag was lost during the update. Wait two minutes, then run this again.' }
    }
    foreach ($line in $all) {
        if ($line -match 'Connection refused|Connection timed out|timed out|Could not resolve') {
            return @{ Ok = $false; Error = "Couldn't connect to RaceFlag over SSH. Check it's on and on this network. Nothing was changed." }
        }
    }
    return @{ Ok = $false; Error = "Could not download the update script. Check the unit's internet connection and the release number." }
}

function Test-HostKeyChanged {
    param([string[]]$Lines)
    foreach ($line in @($Lines)) {
        if ($line -like '*REMOTE HOST IDENTIFICATION HAS CHANGED*') { return $true }
    }
    return $false
}

function Get-UnitVersion {
    # Returns @{ Version; Reason }: Version is $null if the unit didn't answer; Reason is
    # 'timeout' (it was slow) or 'unreachable' (refused, unresolvable, odd answer) then.
    param([string]$Address, [int]$Port, [int]$TimeoutSec = 25)
    try {
        $r = Invoke-RestMethod -Uri "http://${Address}:${Port}/api/update/check" -TimeoutSec $TimeoutSec -ErrorAction Stop
        if ($null -eq $r -or -not $r.current) { return @{ Version = $null; Reason = 'unreachable' } }
        return @{ Version = [string]$r.current; Reason = '' }
    } catch {
        $timedOut = $false
        if ($_.Exception.Status -eq [System.Net.WebExceptionStatus]::Timeout) { $timedOut = $true }
        if ($_.Exception.Message -match 'timed out') { $timedOut = $true }
        if ($timedOut) { return @{ Version = $null; Reason = 'timeout' } }
        return @{ Version = $null; Reason = 'unreachable' }
    }
}

function Wait-ForRestart {
    param([string]$Address, [int]$Port, [string]$Version, [int]$WaitSeconds, [int]$PollSeconds = 5)
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    $seenGone = $false
    while ($true) {
        $v = (Get-UnitVersion -Address $Address -Port $Port -TimeoutSec 15).Version
        if ($null -eq $v) {
            $seenGone = $true
        } elseif ($seenGone -and $v -eq $Version) {
            return $true
        }
        if ((Get-Date) -ge $deadline) { return $false }
        Start-Sleep -Seconds $PollSeconds
    }
}

if ($env:RACEFLAG_UPDATER_NO_MAIN -eq '1') { return }

function Format-SshCommandLine {
    param([string[]]$SshArgs)
    $parts = foreach ($a in $SshArgs) {
        if ($a -match '[\s|]') { '"' + $a + '"' } else { $a }
    }
    return 'ssh.exe ' + ($parts -join ' ')
}

function Get-StepText {
    param([string]$Step)
    switch ($Step) {
        'check'    { return 'Checking the unit...' }
        'download' { return 'Downloading the update...' }
        'verify'   { return 'Checking the download...' }
        'packages' { return 'Getting the packages it needs...' }
        'install'  { return 'Installing...' }
        'version'  { return 'Saving the version number...' }
        'restart'  { return 'Restarting RaceFlag...' }
        default    { return $null }
    }
}

function Invoke-RemoteUpdate {
    param([string[]]$SshArgs)
    $collected = New-Object System.Collections.ArrayList
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $script:SshExe @SshArgs 2>&1 | ForEach-Object {
            $line = ([string]$_).TrimEnd()
            [void]$collected.Add($line)
            if ($line -like 'STEP *') {
                $text = Get-StepText -Step $line.Substring(5).Trim()
                if ($text) { Write-Host $text }
            }
        }
    } finally {
        $ErrorActionPreference = $old
    }
    return ,@($collected.ToArray())
}

function Invoke-Main {
    $tools = Get-SshTools
    $script:SshExe = $tools.Ssh
    $script:KeygenExe = $tools.Keygen
    if (-not $script:SshExe) {
        Write-Host "Windows' SSH client is missing: Settings > Apps > Optional features > OpenSSH Client."
        return 1
    }

    if (-not $script:Tag) {
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            $latest = Invoke-RestMethod -Uri "https://api.github.com/repos/$($script:Repo)/releases/latest" -Headers @{ 'User-Agent' = 'raceflag-updater' } -TimeoutSec 15 -ErrorAction Stop
            $script:Tag = [string]$latest.tag_name
        } catch {
            Write-Host "Couldn't find the latest RaceFlag release. Check this computer's internet connection, or give the release with -Tag vX.Y.Z."
            return 1
        }
    }
    if ($script:Tag -cnotmatch '^v\d+\.\d+\.\d+$') {
        Write-Host "That isn't a RaceFlag release number (expected vX.Y.Z)."
        return 1
    }

    $check = Get-UnitVersion -Address $script:Address -Port $script:Port
    $current = $check.Version
    if ($null -eq $current) {
        if ($check.Reason -eq 'timeout') {
            Write-Host 'RaceFlag answered too slowly - it may not reach the internet. Try again in a minute.'
            return 1
        }
        Write-Host "Can't reach RaceFlag at $($script:Address). Check it's on and on this network, or run again with its IP address."
        return 1
    }

    if (-not $script:User) {
        $script:User = (Read-Host 'Username (from Prometheus Printing)').Trim()
        if (-not $script:User) {
            Write-Host 'No username given. Nothing was changed.'
            return 1
        }
    }

    if ($current -eq $script:Tag.Substring(1)) {
        Write-Host "RaceFlag is already on $($script:Tag); this will install it again."
    }
    $question = "Install $($script:Tag) on RaceFlag v$current at $($script:Address)? Its settings are kept. (Y/N)"
    if ($script:Yes) {
        Write-Host "$question Y"
    } else {
        $answer = Read-Host $question
        if ($answer -ne 'Y' -and $answer -ne 'y') {
            Write-Host 'Nothing was changed.'
            return 1
        }
    }

    $dataDir = Join-Path $env:LOCALAPPDATA 'RaceFlag'
    $knownHosts = Get-KnownHostsPath -KnownHosts (Join-Path $dataDir 'known_hosts')
    $remote = Get-RemoteCommand -Tag $script:Tag -Repo $script:Repo
    $sshArgs = Get-SshArguments -User $script:User -Address $script:Address -KnownHosts $knownHosts -Command $remote

    if ($script:DryRun) {
        Write-Host (Format-SshCommandLine -SshArgs $sshArgs)
        Write-Host 'Dry run: nothing was changed.'
        return 0
    }

    if (-not (Test-Path $dataDir)) { [void](New-Item -ItemType Directory -Path $dataDir -Force) }

    Write-Host 'Connecting. When asked, type the password from Prometheus Printing (it does not show as you type).'
    $lines = Invoke-RemoteUpdate -SshArgs $sshArgs

    if (Test-HostKeyChanged -Lines $lines) {
        Write-Host ''
        Write-Host 'This RaceFlag has a different security key than last time. That is normal if it was re-flashed or replaced.'
        $reset = Read-Host 'Forget the old key so you can connect? (Y/N)'
        if ($reset -ne 'Y' -and $reset -ne 'y') {
            Write-Host 'Nothing was changed.'
            return 1
        }
        if ($null -ne $knownHosts) {
            & $script:KeygenExe -R $script:Address -f $knownHosts | Out-Null
        } else {
            & $script:KeygenExe -R $script:Address | Out-Null
        }
        $again = Read-Host 'Run the update again now? (Y/N)'
        if ($again -ne 'Y' -and $again -ne 'y') {
            Write-Host 'Nothing was changed.'
            return 1
        }
        $lines = Invoke-RemoteUpdate -SshArgs $sshArgs
    }

    $result = Read-RemoteResult -Lines $lines
    if (-not $result.Ok) {
        foreach ($line in $lines) {
            if ($line -like 'LOG *') { Write-Host ('  ' + $line.Substring(4)) }
        }
        $joined = $lines -join "`n"
        if ($result.Error -like 'Could not download the update script*' -and $joined -match 'Permission denied') {
            Write-Host 'The username or password was not accepted. Nothing was changed.'
        } else {
            Write-Host $result.Error
        }
        return 1
    }

    Write-Host "Installed. RaceFlag is restarting - this can take a few minutes. Don't unplug it."
    $ok = Wait-ForRestart -Address $script:Address -Port $script:Port -Version $script:Tag.Substring(1) -WaitSeconds ($WaitMinutes * 60)
    if ($ok) {
        Write-Host "RaceFlag updated to $($script:Tag). You can close this window."
    } else {
        Write-Host "RaceFlag hasn't come back yet. Wait a minute, then open its page."
    }
    return 0
}

exit (Invoke-Main)
