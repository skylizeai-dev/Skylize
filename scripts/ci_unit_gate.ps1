<#
.SYNOPSIS
    Runs the gate commands of CI's `unit` job (.github/workflows/ci.yml) locally,
    in CI's order, in one invocation.

.DESCRIPTION
    A terminal must pass this before claiming the tree is green. It exists because
    a prior session's local gate set omitted `ruff check src tests`, so CI's unit
    job was red from commit 11b595e6 onward with no terminal noticing.

    TWO DELIBERATE DIFFERENCES FROM CI, both of which report MORE than CI does
    and so can never show green where CI would be red:

      1. No early stop. CI's steps are fail-fast; this runs every gate and prints
         a summary, so one run lists every problem instead of the first.
      2. `pytest` here inherits whatever is in your environment. With the
         SKYLIZE_TEST_* service variables set, the Postgres/Redis-backed tests RUN
         (a strict superset of CI's unit job, where those variables are unset and
         those tests skip; CI covers them in its separate `integration` job).

    ONE FORCED DEVIATION: the test step runs `python -m pytest -q`, not ci.yml's
    literal `pytest -q`. The `pytest.exe` console shim is blocked by this machine's
    Application Control policy; `python -m pytest` is the same pytest with the same
    arguments. Nothing is skipped or relaxed by the change.

    CI's `pip install -e ".[dev]"` step is provisioning for a bare runner, not a
    gate, so it is off by default. Pass -Install to reproduce it.

    NOT COVERED: CI's `website` job (npm ci / npm run typecheck / npm test, run in
    website/) and its `integration` job (alembic upgrade head; pytest -q -m
    integration -rA). Run those separately when you touch those areas.

.PARAMETER Install
    Also run CI's `pip install -e ".[dev]"` provisioning step first.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/ci_unit_gate.ps1

.NOTES
    This list is a hand-kept mirror of ci.yml. Nothing enforces that they agree:
    a step added to ci.yml's unit job will NOT appear here until someone adds it.
#>
[CmdletBinding()]
param(
    [switch]$Install
)

# A command that cannot even be launched (blocked shim, missing tool) must be
# reported as a FAILING gate, never silently skipped. 'Stop' makes such a launch
# failure a catchable terminating error instead of a console message the script
# would sail past.
$ErrorActionPreference = 'Stop'

# The gate commands of ci.yml's `unit` job, in the order that file lists them.
# `python -m pytest` stands in for ci.yml's `pytest` (see .DESCRIPTION).
$steps = @(
    @{ Name = 'Ruff';                   Exe = 'ruff';         Args = @('check', 'src', 'tests') }
    @{ Name = 'Import boundaries';      Exe = 'lint-imports'; Args = @() }
    @{ Name = 'Forbidden imports';      Exe = 'python';       Args = @('scripts/check_forbidden_imports.py') }
    @{ Name = 'All modules importable'; Exe = 'python';       Args = @('scripts/check_all_modules_importable.py') }
    @{ Name = 'Orphan-module contract'; Exe = 'python';       Args = @('scripts/find_orphan_modules.py') }
    @{ Name = 'Types (mypy strict)';    Exe = 'mypy';         Args = @('src') }
    @{ Name = 'Tests (no exclusions)';  Exe = 'python';       Args = @('-m', 'pytest', '-q') }
)

function Invoke-Gate {
    <#
      Runs one gate and returns its exit code. $LASTEXITCODE is pre-set to a
      sentinel so a command that never runs cannot leave the previous step's 0
      behind and read as a pass.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @()
    )
    $global:LASTEXITCODE = -1
    try {
        # Out-Host, not bare invocation: a function's pipeline output IS its return
        # value, so letting the tool's stdout fall through would make this function
        # return [stdout lines + code] instead of the code.
        if ($Arguments.Count -eq 0) {
            & $Exe | Out-Host
        } else {
            & $Exe @Arguments | Out-Host
        }
    } catch {
        Write-Host "  could not run '$Exe': $($_.Exception.Message)" -ForegroundColor Red
        return 127
    }
    return $LASTEXITCODE
}

# Run from the repo root so the relative scripts/ paths resolve wherever this is
# invoked from.
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $results = @()
    $failed = 0

    if ($Install) {
        Write-Host ''
        Write-Host '=== [install] pip install -e ".[dev]" ===' -ForegroundColor Cyan
        $code = Invoke-Gate -Exe 'pip' -Arguments @('install', '-e', '.[dev]')
        if ($code -ne 0) { $failed++ }
        $results += [pscustomobject]@{
            Status = $(if ($code -eq 0) { 'PASS' } else { 'FAIL' })
            Step   = 'Install (pip install -e ".[dev]")'
            Exit   = $code
        }
    }

    $total = $steps.Count
    $i = 0
    foreach ($step in $steps) {
        $i++
        $shown = (@($step.Exe) + $step.Args) -join ' '
        Write-Host ''
        Write-Host "=== [$i/$total] $($step.Name): $shown ===" -ForegroundColor Cyan
        $code = Invoke-Gate -Exe $step.Exe -Arguments $step.Args
        if ($code -ne 0) { $failed++ }
        $results += [pscustomobject]@{
            Status = $(if ($code -eq 0) { 'PASS' } else { 'FAIL' })
            Step   = $step.Name
            Exit   = $code
        }
    }

    Write-Host ''
    Write-Host '=============== CI unit-job parity summary ==============='
    foreach ($r in $results) {
        $colour = $(if ($r.Status -eq 'PASS') { 'Green' } else { 'Red' })
        Write-Host ("  {0,-4} {1} (exit {2})" -f $r.Status, $r.Step, $r.Exit) -ForegroundColor $colour
    }
    Write-Host ''

    if ($failed -gt 0) {
        Write-Host "$failed of $($results.Count) gate(s) FAILED - CI's unit job would be RED on this tree." -ForegroundColor Red
        exit 1
    }
    Write-Host "All $($results.Count) gates passed - CI's unit job would be GREEN on this tree." -ForegroundColor Green
    exit 0
}
finally {
    Pop-Location
}
