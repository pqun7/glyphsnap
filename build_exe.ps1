$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " Arabic Newspaper OCR - Nuitka Build" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# Locate Python
$Python = (Get-Command python -ErrorAction Stop).Source

Write-Host "Python:" -ForegroundColor Yellow
& $Python --version
if ($LASTEXITCODE -ne 0) {
    throw "Python could not be started."
}

# Nuitka cache
$env:NUITKA_CACHE_DIR = Join-Path $PSScriptRoot '.nuitka-cache'
New-Item -ItemType Directory -Force -Path $env:NUITKA_CACHE_DIR | Out-Null

Write-Host ""
Write-Host "Nuitka cache:" -ForegroundColor Yellow
Write-Host $env:NUITKA_CACHE_DIR -ForegroundColor DarkGray

# Optional local Qt dependencies
$LocalQt = Join-Path $PSScriptRoot '.qtdeps'

if (Test-Path (Join-Path $LocalQt 'PySide6')) {
    Write-Host ""
    Write-Host "Using local Qt dependencies:" -ForegroundColor Yellow
    Write-Host $LocalQt -ForegroundColor DarkGray

    if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
        $env:PYTHONPATH = $LocalQt
    }
    else {
        $env:PYTHONPATH = "$LocalQt$([IO.Path]::PathSeparator)$env:PYTHONPATH"
    }
}

# Check required packages
Write-Host ""
Write-Host "Checking required packages..." -ForegroundColor Yellow
Write-Host ""

& $Python -c "import PySide6; print('PySide6 OK:', getattr(PySide6, '__version__', 'unknown'))"
if ($LASTEXITCODE -ne 0) {
    throw "PySide6 is not available."
}

& $Python -c "import pymupdf; print('PyMuPDF OK:', getattr(pymupdf, '__version__', 'unknown'))"
if ($LASTEXITCODE -ne 0) {
    throw "PyMuPDF is not available."
}

& $Python -c "import PIL; print('Pillow OK:', getattr(PIL, '__version__', 'unknown'))"
if ($LASTEXITCODE -ne 0) {
    throw "Pillow is not available."
}

& $Python -c "import pytesseract; print('pytesseract OK:', getattr(pytesseract, '__version__', 'unknown'))"
if ($LASTEXITCODE -ne 0) {
    throw "pytesseract is not available."
}

# Locate PyMuPDF
Write-Host ""
Write-Host "Locating PyMuPDF..." -ForegroundColor Yellow

$PyMuPDFPath = & $Python -c "import pymupdf, pathlib; print(pathlib.Path(pymupdf.__file__).resolve().parent)"
if ($LASTEXITCODE -ne 0) {
    throw "Could not locate PyMuPDF."
}

$PyMuPDFPath = ($PyMuPDFPath | Select-Object -Last 1).ToString().Trim()
if ([string]::IsNullOrWhiteSpace($PyMuPDFPath)) {
    throw "PyMuPDF path is empty."
}

if (-not (Test-Path $PyMuPDFPath -PathType Container)) {
    throw "PyMuPDF directory does not exist: $PyMuPDFPath"
}

Write-Host "PyMuPDF:" -ForegroundColor DarkGray
Write-Host $PyMuPDFPath -ForegroundColor DarkGray

# Get PyMuPDF version
$PyMuPDFVersion = & $Python -c "import pymupdf; print(getattr(pymupdf, '__version__', 'unknown'))"
if ($LASTEXITCODE -ne 0) {
    throw "Could not determine PyMuPDF version."
}

$PyMuPDFVersion = ($PyMuPDFVersion | Select-Object -Last 1).ToString().Trim()
Write-Host "PyMuPDF version:" -ForegroundColor DarkGray
Write-Host $PyMuPDFVersion -ForegroundColor DarkGray

# Output directories
$OutputDir = Join-Path $PSScriptRoot 'dist-nuitka'
$DistDir = Join-Path $OutputDir 'arabic_newspaper_ocr_native.dist'
$ExePath = Join-Path $DistDir 'ArabicNewspaperOCR.exe'

# Clean previous build
if (Test-Path $OutputDir) {
    Write-Host ""
    Write-Host "Removing previous build..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $OutputDir
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

# Build with Nuitka
Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host " Starting Nuitka compilation" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""

$NuitkaArgs = @(
    '-m', 'nuitka',
    '--standalone',
    '--windows-console-mode=disable',
    '--enable-plugin=pyside6',
    '--assume-yes-for-downloads',

    # Keep PyMuPDF out of Nuitka's generated C code.
    '--nofollow-import-to=fitz',
    '--nofollow-import-to=pymupdf',

    # Unused heavy dependencies.
    '--nofollow-import-to=numpy',
    '--nofollow-import-to=pandas',
    '--nofollow-import-to=cv2',
    '--nofollow-import-to=streamlit',
    '--nofollow-import-to=streamlit_cropper',
    '--nofollow-import-to=paddle',
    '--nofollow-import-to=paddleocr',
    '--nofollow-import-to=scipy',

    '--output-dir=dist-nuitka',
    '--output-filename=ArabicNewspaperOCR.exe',
    'arabic_newspaper_ocr_native.py'
)

& $Python @NuitkaArgs
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka compilation failed."
}

# Verify Nuitka output
Write-Host ""
Write-Host "Checking Nuitka output..." -ForegroundColor Yellow

if (-not (Test-Path $DistDir -PathType Container)) {
    throw "Nuitka distribution directory was not created: $DistDir"
}

if (-not (Test-Path $ExePath -PathType Leaf)) {
    throw "Nuitka EXE was not created: $ExePath"
}

$ExeInfo = Get-Item $ExePath
Write-Host "Nuitka EXE created successfully." -ForegroundColor Green
Write-Host "EXE size: $("{0:N2}" -f ($ExeInfo.Length / 1MB)) MB" -ForegroundColor DarkGray

# Copy PyMuPDF package
Write-Host ""
Write-Host "Adding PyMuPDF runtime files..." -ForegroundColor Yellow

$PyMuPDFDestination = Join-Path $DistDir 'pymupdf'
if (Test-Path $PyMuPDFDestination) {
    Remove-Item -Recurse -Force $PyMuPDFDestination
}

New-Item -ItemType Directory -Force -Path $PyMuPDFDestination | Out-Null
Copy-Item -Path (Join-Path $PyMuPDFPath '*') -Destination $PyMuPDFDestination -Recurse -Force

# Copy the legacy fitz compatibility package when present.
$SitePackages = & $Python -c "import site; print(site.getsitepackages()[0])"
if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($SitePackages)) {
    $SitePackages = ($SitePackages | Select-Object -Last 1).ToString().Trim()
    $FitzPackage = Join-Path $SitePackages 'fitz'

    if (Test-Path $FitzPackage -PathType Container) {
        Write-Host "Copying fitz compatibility package..." -ForegroundColor DarkGray
        Copy-Item -Path $FitzPackage -Destination $DistDir -Recurse -Force
    }
}

# Inspect and copy native PyMuPDF files.
Write-Host ""
Write-Host "Checking PyMuPDF native files..." -ForegroundColor Yellow

$NativeFiles = @(
    Get-ChildItem -Path $PyMuPDFDestination -Recurse -File -Include *.pyd, *.dll -ErrorAction SilentlyContinue
)

if ($NativeFiles.Count -eq 0) {
    throw "No PyMuPDF .pyd or .dll files were found."
}

foreach ($File in $NativeFiles) {
    Write-Host ("  " + $File.FullName) -ForegroundColor DarkGray
    Copy-Item -Path $File.FullName -Destination (Join-Path $DistDir $File.Name) -Force
}

# Runtime verification using development Python.
Write-Host ""
Write-Host "Verifying PyMuPDF runtime..." -ForegroundColor Yellow

$VerifyScript = @(
    'import sys'
    "sys.path.insert(0, r'$DistDir')"
    ''
    'import pymupdf'
    ''
    'print("PyMuPDF runtime import: OK")'
    'print("PyMuPDF version:", getattr(pymupdf, "__version__", "unknown"))'
    ''
    'doc = pymupdf.open()'
    'page = doc.new_page()'
    'page.insert_text((72, 72), "Runtime test")'
    'text = page.get_text()'
    'doc.close()'
    ''
    'if "Runtime test" not in text:'
    '    raise RuntimeError("PyMuPDF functional test failed.")'
    ''
    'print("PyMuPDF functional test: OK")'
) -join [Environment]::NewLine

$TempVerify = Join-Path $OutputDir '_verify_pymupdf.py'
Set-Content -Path $TempVerify -Value $VerifyScript -Encoding UTF8

& $Python $TempVerify
$VerifyExitCode = $LASTEXITCODE
Remove-Item -Force $TempVerify -ErrorAction SilentlyContinue

if ($VerifyExitCode -ne 0) {
    throw "PyMuPDF runtime verification failed."
}

# Check important distribution items.
Write-Host ""
Write-Host "Checking distribution contents..." -ForegroundColor Yellow

foreach ($Required in @('ArabicNewspaperOCR.exe', 'pymupdf')) {
    $RequiredPath = Join-Path $DistDir $Required
    if (-not (Test-Path $RequiredPath)) {
        throw "Required distribution item is missing: $Required"
    }
}

# Final information.
$ExeInfo = Get-Item $ExePath
$DistributionSize = (
    Get-ChildItem -Path $DistDir -Recurse -File -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum
).Sum

Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host " BUILD COMPLETED SUCCESSFULLY" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""
Write-Host "EXE:" -ForegroundColor Yellow
Write-Host $ExePath
Write-Host ""
Write-Host "EXE size:" -ForegroundColor Yellow
Write-Host ("{0:N2} MB" -f ($ExeInfo.Length / 1MB))
Write-Host ""
Write-Host "Distribution size:" -ForegroundColor Yellow
Write-Host ("{0:N2} MB" -f ($DistributionSize / 1MB))
Write-Host ""
Write-Host "PyMuPDF:" -ForegroundColor Yellow
Write-Host $PyMuPDFVersion
Write-Host ""
Write-Host "Distribution:" -ForegroundColor Yellow
Write-Host $DistDir
Write-Host ""
Write-Host "NOTE: Tesseract and Arabic language data (ara.traineddata) are required on the target PC." -ForegroundColor DarkGray
Write-Host ""
