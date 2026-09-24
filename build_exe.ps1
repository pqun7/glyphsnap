$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# Keep one canonical Nuitka pipeline. The previous implementation duplicated
# the compiler flags and accidentally excluded PyMuPDF at runtime.
& (Join-Path $PSScriptRoot 'build_nuitka.ps1')
if ($LASTEXITCODE -ne 0) {
    throw 'GlyphSnap Nuitka build failed.'
}
