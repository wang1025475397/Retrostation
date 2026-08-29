<#
Thin wrapper -- delegates to deploy.py so Windows users don't have to install
bash.  Keep both wrappers in sync with deploy.py.
#>
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
& python "$here\deploy.py" @args
exit $LASTEXITCODE
