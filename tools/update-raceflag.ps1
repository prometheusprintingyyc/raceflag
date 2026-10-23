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
    return "curl -fsSL https://raw.githubusercontent.com/$Repo/$Tag/tools/raceflag-remote-update.sh | sudo bash -s -- $Tag $Repo"
}

function Get-SshArguments {
    param([string]$User, [string]$Address, [string]$KnownHosts, [string]$Command)
    return @(
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', "UserKnownHostsFile='$KnownHosts'",
        '-o', 'ConnectTimeout=15',
        "$User@$Address",
        $Command
    )
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
    param([string]$Address, [int]$Port)
    try {
        $r = Invoke-RestMethod -Uri "http://${Address}:${Port}/api/update/check" -TimeoutSec 4 -ErrorAction Stop
        if ($null -eq $r -or -not $r.current) { return $null }
        return [string]$r.current
    } catch {
        return $null
    }
}

function Wait-ForRestart {
    param([string]$Address, [int]$Port, [string]$Version, [int]$WaitSeconds, [int]$PollSeconds = 5)
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    $seenGone = $false
    while ($true) {
        $v = Get-UnitVersion -Address $Address -Port $Port
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
        & ssh.exe @SshArgs 2>&1 | ForEach-Object {
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
    if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
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
    if ($script:Tag -notmatch '^v\d+\.\d+\.\d+$') {
        Write-Host "That isn't a RaceFlag release number (expected vX.Y.Z)."
        return 1
    }

    $current = Get-UnitVersion -Address $script:Address -Port $script:Port
    if ($null -eq $current) {
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
    $knownHosts = Join-Path $dataDir 'known_hosts'
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
        & ssh-keygen.exe -R $script:Address -f $knownHosts | Out-Null
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
