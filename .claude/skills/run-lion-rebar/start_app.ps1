# Starts LION REBAR DESKTOP from the release folder, waits for the REAL app
# window, saves a screenshot and prints the PIDs. Close it with -ClosePid.
#
# ASCII only on purpose: Windows PowerShell 5.1 reads a .ps1 without BOM as
# ANSI, and non-ASCII text has already broken a window check once.
#
# Why the child is searched by name: .venv\Scripts\python.exe is a launcher
# with TWO children, conhost.exe and python3.12.exe. The window belongs to
# python3.12.exe; checking the first child reports "no window" forever.

param(
    [int]$TimeoutSeconds = 90,
    [string]$Screenshot = "",
    [int]$ClosePid = 0
)

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Drawing
if (-not ('LionWin' -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public class LionWin {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
}
"@
}

function Save-WindowPng([IntPtr]$Handle, [string]$Path) {
    $rect = New-Object LionWin+RECT
    [void][LionWin]::GetWindowRect($Handle, [ref]$rect)
    $width = $rect.Right - $rect.Left
    $height = $rect.Bottom - $rect.Top
    $bitmap = New-Object System.Drawing.Bitmap $width, $height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $hdc = $graphics.GetHdc()
    # 2 = PW_RENDERFULLCONTENT: captures the window even behind other windows.
    [void][LionWin]::PrintWindow($Handle, $hdc, 2)
    $graphics.ReleaseHdc($hdc)
    $graphics.Dispose()
    $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bitmap.Dispose()
}

if ($ClosePid -ne 0) {
    $proc = Get-Process -Id $ClosePid -ErrorAction SilentlyContinue
    if (-not $proc) { "PID $ClosePid is not running"; exit 0 }
    if ($proc.ProcessName -notlike 'python*') {
        throw "PID $ClosePid is $($proc.ProcessName), not the app - refusing to close it"
    }
    [void]$proc.CloseMainWindow()
    if (-not $proc.WaitForExit(15000)) {
        # A dialog can hold the close (export or batch analysis still running).
        # Look at a screenshot instead of killing blindly.
        "PID $ClosePid still running after 15 s - a dialog is probably open; not forcing"
        exit 2
    }
    "CLOSED PID $ClosePid"
    exit 0
}

if (-not $env:LION_REBAR_DATA_ROOT) {
    # Without it the app writes into the owner's session and may switch the
    # owner's active engine.
    throw "LION_REBAR_DATA_ROOT is not set - refusing to start with the owner's data folder"
}

$release = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$python = Join-Path $release '.venv\Scripts\python.exe'
$dataRoot = $env:LION_REBAR_DATA_ROOT -replace '^~', $env:USERPROFILE

$started = Get-Date
$wrapper = Start-Process -FilePath $python -ArgumentList 'run_dev.py' -WorkingDirectory $release -PassThru
$app = $null
$deadline = $started.AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    $child = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($wrapper.Id)" |
        Where-Object { $_.Name -like 'python*' } | Select-Object -First 1
    if ($child) {
        $candidate = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.MainWindowHandle -ne 0) { $app = $candidate; break }
    }
    if ($wrapper.HasExited) { break }
    Start-Sleep -Milliseconds 500
}

$seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
"WRAPPER_PID $($wrapper.Id)"
"DATA_ROOT $dataRoot"
if (-not $app) {
    "NO_WINDOW after $seconds s (wrapper exited: $($wrapper.HasExited))"
    $log = Join-Path $dataRoot 'logs\application.log'
    if (Test-Path $log) { "--- log tail ---"; Get-Content $log -Tail 20 -Encoding UTF8 }
    exit 1
}

"APP_PID $($app.Id)"
"WINDOW_AFTER_SECONDS $seconds"
"TITLE $($app.MainWindowTitle)"
if (-not $Screenshot) { $Screenshot = Join-Path $env:TEMP "lion_rebar_window_$($app.Id).png" }
# Give Qt a moment to paint the first frame before capturing it.
Start-Sleep -Seconds 2
Save-WindowPng $app.MainWindowHandle $Screenshot
"SCREENSHOT $Screenshot"
