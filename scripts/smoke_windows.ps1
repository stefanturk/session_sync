# Check a built Windows SessionSync end to end:
#   1. the installer installs for this user without asking for admin,
#   2. sessionsync: links are registered to the installed program,
#   3. the program starts (no window needed),
#   4. clicking a link while it runs hands the link to that copy (a second
#      copy starts, passes it on and quits), exactly as a double-click on
#      "▶ Open in Live (Windows)" in a shared song folder would.
#
#   powershell -ExecutionPolicy Bypass -File scripts\smoke_windows.ps1 [installer.exe]
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$installer = if ($args.Count -gt 0) { $args[0] } else {
    Get-ChildItem (Join-Path $root "dist") -Filter "SessionSync-Setup-*.exe" | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $installer) { throw "No installer in dist\ - run scripts\build_windows.ps1 first" }

function Fail($why) { Write-Host "FAIL: $why"; exit 1 }

Write-Host "Installing $installer"
$p = Start-Process $installer -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER" -Wait -PassThru
if ($p.ExitCode -ne 0) { Fail "installer exit code $($p.ExitCode)" }

$exe = Join-Path $env:LOCALAPPDATA "Programs\SessionSync\SessionSync.exe"
if (-not (Test-Path $exe)) { Fail "no program at $exe" }
Write-Host "OK installed: $exe"

$command = (Get-ItemProperty "HKCU:\Software\Classes\sessionsync\shell\open\command")."(default)"
if ($command -ne "`"$exe`" `"%1`"") { Fail "link command is '$command'" }
Write-Host "OK links registered: $command"

$log = Join-Path $env:LOCALAPPDATA "SessionSync\SessionSync.log"
Remove-Item $log -ErrorAction SilentlyContinue
$config = Join-Path $env:TEMP ("sessionsync-smoke-" + [guid]::NewGuid())
$env:SESSIONSYNC_HOME = $config
$env:SESSIONSYNC_NAME = "Smoke Test"
$env:SESSIONSYNC_EMAIL = "smoke@example.com"
$env:SESSIONSYNC_SMOKE = "25"
if (-not $env:QT_QPA_PLATFORM) { $env:QT_QPA_PLATFORM = "offscreen" }

Write-Host "Starting SessionSync"
$first = Start-Process $exe -PassThru
$deadline = (Get-Date).AddSeconds(60)
while (-not ((Test-Path $log) -and (Select-String -Path $log -Pattern "smoke: window up" -Quiet))) {
    if ($first.HasExited) { Get-Content $log -ErrorAction SilentlyContinue; Fail "it quit at start (exit $($first.ExitCode))" }
    if ((Get-Date) -gt $deadline) { Get-Content $log -ErrorAction SilentlyContinue; Fail "no window after 60 s" }
    Start-Sleep -Milliseconds 500
}
Write-Host "OK it started"

$link = "sessionsync://open?repo=smoke-test&song=Smoke%20Song"
Write-Host "Clicking $link"
Start-Process $link
$deadline = (Get-Date).AddSeconds(30)
# Windows hands the link over as sessionsync://open/?… (with a slash added).
while (-not (Select-String -Path $log -Pattern "from another copy: sessionsync://open/?\?repo=smoke-test&song=Smoke%20Song" -Quiet)) {
    if ((Get-Date) -gt $deadline) { Get-Content $log; Fail "the running copy never got the link" }
    Start-Sleep -Milliseconds 500
}
Write-Host "OK the running copy got the link"

# The launcher file itself, as Session Sync writes it into a shared song folder.
$song = Join-Path $config "Smoke Song"
New-Item -ItemType Directory -Path $song -Force | Out-Null
$door = Join-Path $song "$([char]0x25B6) Open in Live (Windows).url"
[IO.File]::WriteAllText($door, "[InternetShortcut]`r`nURL=sessionsync://open?repo=smoke-door&song=Smoke%20Song`r`n")
Write-Host "Double-clicking $door"
Start-Process $door
$deadline = (Get-Date).AddSeconds(30)
# The new copy hands the link over and quits; the running copy logs it. Both
# write the log at that moment, so accept either of the running copy's lines.
while (-not (Select-String -Path $log -Pattern "(from another copy|link): sessionsync://open/?\?repo=smoke-door" -Quiet)) {
    if ((Get-Date) -gt $deadline) { Get-Content $log; Fail "double-clicking the launcher did nothing" }
    Start-Sleep -Milliseconds 500
}
Write-Host "OK the launcher file opens Session Sync"

$first.WaitForExit(60000) | Out-Null
$copies = @(Get-Process SessionSync -ErrorAction SilentlyContinue)
if ($copies.Count -gt 0) { Fail "$($copies.Count) copies still running" }
Write-Host "--- log ---"
Get-Content $log
Remove-Item -Recurse -Force $config -ErrorAction SilentlyContinue
Write-Host "PASS"
