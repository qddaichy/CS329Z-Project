$ErrorActionPreference = 'Stop'
if ($env:CONDA_DEFAULT_ENV -ne 'cs329z_project') {
    throw 'Activate the cs329z_project Conda environment before running setup.'
}
$pythonPrefix = & python -c 'import sys; print(sys.prefix)'
if ($LASTEXITCODE -ne 0 -or $pythonPrefix -ne $env:CONDA_PREFIX) {
    throw 'The selected Python does not belong to the active Conda environment.'
}

$requirements = Join-Path $PSScriptRoot 'requirements.txt'
& python -m pip install --no-user -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

Write-Host 'Setup complete. From the CS329Z_project directory, run:'
Write-Host 'python -m project.run_react --help'
