<#
.SYNOPSIS
    Puts a "TraderBot Plan Tester" shortcut on the Desktop and in the Start menu.

.DESCRIPTION
    The shortcut opens the practice-only desktop app (run_desktop_app.pyw) with
    Python's windowless launcher, so no black console window appears, and it
    does not depend on Windows knowing what to do with .pyw files.

    Run it again any time - after moving this folder, or after installing a
    different version of Python - to refresh the shortcuts. It only ever
    creates or removes these shortcut files; it changes nothing else.

.PARAMETER Remove
    Delete the shortcuts instead of creating them.

.PARAMETER Into
    Put the shortcut in these folders instead of the Desktop and Start menu
    (used by the tests).
#>
[CmdletBinding()]
param(
    [switch]$Remove,
    [string[]]$Into
)

$ErrorActionPreference = 'Stop'

$ShortcutName = 'TraderBot Plan Tester'
$Repo = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $Repo 'run_desktop_app.pyw'
$Icon = Join-Path $Repo 'desktop_app\assets\app.ico'

if (-not $Into) {
    $Into = @(
        [Environment]::GetFolderPath('Desktop'),
        [Environment]::GetFolderPath('Programs')
    )
}

if ($Remove) {
    foreach ($folder in $Into) {
        $path = Join-Path $folder "$ShortcutName.lnk"
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path
            Write-Output "Removed $path"
        }
    }
    return
}

if (-not (Test-Path -LiteralPath $Launcher)) {
    throw "Cannot find $Launcher - run this from inside the TraderBOT folder."
}

# The Microsoft Store puts a do-nothing stand-in for pythonw.exe in WindowsApps;
# skip it so the shortcut points at a real Python.
$python = Get-Command pythonw -All -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -notmatch '\\Microsoft\\WindowsApps\\' } |
    Select-Object -First 1
if (-not $python) {
    throw 'Python does not seem to be installed (no pythonw.exe found). Install it from python.org, then run this again.'
}

$shell = New-Object -ComObject WScript.Shell
foreach ($folder in $Into) {
    if (-not (Test-Path -LiteralPath $folder)) {
        New-Item -ItemType Directory -Path $folder | Out-Null
    }
    $path = Join-Path $folder "$ShortcutName.lnk"
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $python.Source
    $link.Arguments = '"' + $Launcher + '"'
    $link.WorkingDirectory = $Repo
    $link.Description = 'Try crypto buying plans against real past prices. Practice only - never real money.'
    $link.WindowStyle = 1
    if (Test-Path -LiteralPath $Icon) {
        $link.IconLocation = "$Icon,0"
    }
    $link.Save()
    Write-Output "Created $path"
}
