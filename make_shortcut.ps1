# Creates an "ado:3" shortcut on your desktop (and, with -Startup, one that starts her when you log in).
# Usage, inside the project folder:
#   .\make_shortcut.ps1
#   .\make_shortcut.ps1 -Startup
param([switch]$Startup)

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $dir ".venv\Scripts\pythonw.exe"
$main = Join-Path $dir "main.py"

if (-not (Test-Path $pythonw)) {
    Write-Host "Can't find $pythonw" -ForegroundColor Red
    Write-Host "Set up the virtual environment first (README, install step 3)." -ForegroundColor Red
    exit 1
}

$shell = New-Object -ComObject WScript.Shell

function New-AdoShortcut([string]$folder) {
    $link = $shell.CreateShortcut((Join-Path $folder "ado3.lnk"))
    $link.TargetPath = $pythonw
    $link.Arguments = '"' + $main + '"'
    $link.WorkingDirectory = $dir
    $link.Description = "ado:3 desktop pet"
    $icon = Join-Path $dir "ado.ico"
    if (Test-Path $icon) { $link.IconLocation = $icon }
    $link.Save()
    Write-Host "Created $($link.FullName)" -ForegroundColor Green
}

New-AdoShortcut ([Environment]::GetFolderPath("Desktop"))
if ($Startup) {
    New-AdoShortcut ([Environment]::GetFolderPath("Startup"))
    Write-Host "She will now start when you log in. Delete the shortcut in shell:startup to undo." -ForegroundColor Green
}
