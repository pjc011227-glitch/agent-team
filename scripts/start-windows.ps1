$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
python -m pip install -e .
python -m agent_team.server
