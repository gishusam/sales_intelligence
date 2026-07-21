<#
.SYNOPSIS
Deploy the Sales Intelligence Cloud Run API service only.

.DESCRIPTION
Run this script from the repository root:

    Set-ExecutionPolicy -Scope Process Bypass
    .\deploy-api-only.ps1

The script:
- deploys ONLY the sales-intelligence-api Cloud Run service
- NEVER builds, updates, or executes the sales-scraper Cloud Run Job
- builds from .\backend\Dockerfile using .\backend as the Cloud Build context
- signs in with gcloud when no usable active login exists
- preserves the current service environment, secrets, IAM, service account,
  CPU, memory, port, concurrency, timeout, and other unchanged settings
- backs up the current service configuration
- verifies /health and /api/leads/mine?page&limit
- records an exact rollback command

.PARAMETER Yes
Skip confirmation prompts.

.PARAMETER SkipTests
Do not attempt to run local pytest tests.

.PARAMETER NoBrowser
Use gcloud auth login --no-launch-browser.

.EXAMPLE
.\deploy-api-only.ps1

.EXAMPLE
.\deploy-api-only.ps1 -Yes -SkipTests
#>

[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$SkipTests,
    [switch]$NoBrowser,
    [string]$ProjectId = $(if ($env:PROJECT_ID) { $env:PROJECT_ID } else { "sales-intelligens" }),
    [string]$Region = $(if ($env:REGION) { $env:REGION } else { "europe-west1" }),
    [string]$ApiService = $(if ($env:API_SERVICE) { $env:API_SERVICE } else { "sales-intelligence-api" }),
    [string]$BuildContext = $(if ($env:BUILD_CONTEXT) { $env:BUILD_CONTEXT } else { "backend" }),
    [string]$BackupDir = $(if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { "deployment-backups" }),
    [string]$ExpectedOpenApiPath = "/api/leads/mine"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Dockerfile = Join-Path $BuildContext "Dockerfile"
$PreviousRevision = ""
$NewRevision = ""
$ApiImage = ""
$ApiUrl = ""
$DeploymentAttempted = $false

function Write-Step {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "✓ $Message" -ForegroundColor Green
}

function Write-WarningMessage {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "! $Message" -ForegroundColor Yellow
}

function Stop-Deploy {
    param([Parameter(Mandatory)][string]$Message)
    throw $Message
}

function Confirm-DeployAction {
    param([Parameter(Mandatory)][string]$Message)

    if ($Yes) {
        return $true
    }

    $answer = Read-Host "$Message [y/N]"
    return $answer -match '^(y|yes)$'
}

function Get-GCloudCommand {
    $command = Get-Command "gcloud" -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $commonPaths = @(
        "$env:ProgramFiles\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd",
        "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    )

    foreach ($path in $commonPaths) {
        if (Test-Path $path) {
            return $path
        }
    }

    Stop-Deploy "Google Cloud CLI was not found. Install it, reopen PowerShell, and rerun this script."
}

function Invoke-GCloud {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    & $script:GCloud @Arguments
    $exitCode = $LASTEXITCODE

    if (-not $AllowFailure -and $exitCode -ne 0) {
        Stop-Deploy "gcloud failed with exit code $exitCode`: gcloud $($Arguments -join ' ')"
    }
}

function Get-GCloudText {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $output = & $script:GCloud @Arguments
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        Stop-Deploy "gcloud failed with exit code $exitCode`: gcloud $($Arguments -join ' ')"
    }

    return (($output | Out-String).Trim())
}

function Ensure-GCloudAuthentication {
    $activeAccount = Get-GCloudText @(
        "auth", "list",
        "--filter=status:ACTIVE",
        "--format=value(account)"
    )

    $tokenWorks = $true
    & $script:GCloud auth print-access-token *> $null
    if ($LASTEXITCODE -ne 0) {
        $tokenWorks = $false
    }

    if ([string]::IsNullOrWhiteSpace($activeAccount) -or -not $tokenWorks) {
        Write-Step "No usable active Google Cloud login was found"

        $loginArgs = @("auth", "login")
        if ($NoBrowser) {
            $loginArgs += "--no-launch-browser"
        }

        Invoke-GCloud -Arguments $loginArgs

        $activeAccount = Get-GCloudText @(
            "auth", "list",
            "--filter=status:ACTIVE",
            "--format=value(account)"
        )
    }

    if ([string]::IsNullOrWhiteSpace($activeAccount)) {
        Stop-Deploy "Authentication did not produce an active Google Cloud account."
    }

    Write-Success "Authenticated as $activeAccount"
    return $activeAccount
}

function Remove-ImageTagOrDigest {
    param([Parameter(Mandatory)][string]$Image)

    $value = $Image -replace '@sha256:.*$', ''
    return ($value -replace ':[^/:]+$', '')
}

function Assert-NoSensitiveBuildFiles {
    $allowedNames = @(".env.example", ".env.sample", ".env.template")
    $sensitive = Get-ChildItem -Path $BuildContext -Recurse -File -ErrorAction Stop |
        Where-Object {
            $name = $_.Name
            if ($allowedNames -contains $name) {
                return $false
            }

            return (
                $name -eq ".env" -or
                $name -like ".env.*" -or
                $name -like "*.pem" -or
                $name -like "*.key" -or
                $name -like "*credentials*.json" -or
                $name -like "*service-account*.json"
            )
        }

    if ($sensitive) {
        $sensitive | ForEach-Object { Write-Host $_.FullName -ForegroundColor Red }
        Stop-Deploy "Potential secret files exist inside '$BuildContext', which is the uploaded Cloud Build context. Remove or relocate them before deploying."
    }
}

function Invoke-LocalTestsIfAvailable {
    if ($SkipTests) {
        Write-WarningMessage "Local tests were skipped by request."
        return
    }

    $testTargets = @()
    if (Test-Path (Join-Path $BuildContext "tests")) {
        $testTargets += (Join-Path $BuildContext "tests")
    }
    if (Test-Path ".\tests") {
        $testTargets += ".\tests"
    }

    if ($testTargets.Count -eq 0) {
        Write-WarningMessage "No local tests directory was found; continuing without local pytest."
        return
    }

    $python = $null
    $candidatePaths = @(
        ".\.venv\Scripts\python.exe",
        ".\$BuildContext\.venv\Scripts\python.exe"
    )

    foreach ($candidate in $candidatePaths) {
        if (Test-Path $candidate) {
            $python = $candidate
            break
        }
    }

    if ($null -eq $python) {
        $pythonCommand = Get-Command "python" -ErrorAction SilentlyContinue
        if ($null -ne $pythonCommand) {
            & $pythonCommand.Source -c "import pytest" *> $null
            if ($LASTEXITCODE -eq 0) {
                $python = $pythonCommand.Source
            }
        }
    }

    if ($null -eq $python) {
        Write-WarningMessage "Tests exist, but no Python environment with pytest is available."
        if (-not (Confirm-DeployAction "Continue without running local tests?")) {
            Stop-Deploy "Deployment cancelled."
        }
        return
    }

    Write-Step "Running local API tests"
    & $python -m pytest -q @testTargets
    if ($LASTEXITCODE -ne 0) {
        Stop-Deploy "Local tests failed."
    }

    Write-Success "Local tests passed"
}

function Invoke-HealthCheck {
    param([Parameter(Mandatory)][string]$BaseUrl)

    Write-Step "Checking $BaseUrl/health"
    $attempts = 6

    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        try {
            $health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health" -TimeoutSec 45
            $health | Format-Table | Out-Host

            if ($health.status -eq "ok") {
                Write-Success "Health check passed"
                return
            }

            Write-WarningMessage "Health endpoint responded, but did not report status=ok."
        }
        catch {
            Write-WarningMessage "Health attempt $attempt/$attempts failed; retrying after cold-start delay."
        }

        if ($attempt -lt $attempts) {
            Start-Sleep -Seconds 10
        }
    }

    Stop-Deploy "Health verification failed."
}

function Assert-OpenApiContract {
    param(
        [Parameter(Mandatory)][string]$BaseUrl,
        [Parameter(Mandatory)][string]$Path
    )

    Write-Step "Checking OpenAPI contract"
    $openApi = Invoke-RestMethod -Method Get -Uri "$BaseUrl/openapi.json" -TimeoutSec 45
    $pathProperty = $openApi.paths.PSObject.Properties |
        Where-Object Name -eq $Path |
        Select-Object -First 1

    if ($null -eq $pathProperty) {
        Stop-Deploy "$Path is missing from OpenAPI."
    }

    $getOperation = $pathProperty.Value.get
    if ($null -eq $getOperation) {
        Stop-Deploy "GET $Path is missing from OpenAPI."
    }

    $parameterNames = @($getOperation.parameters | ForEach-Object { $_.name })

    foreach ($requiredName in @("page", "limit")) {
        if ($parameterNames -notcontains $requiredName) {
            Stop-Deploy "OpenAPI is missing the '$requiredName' parameter on GET $Path."
        }
    }

    Write-Success "OpenAPI contains $Path with page and limit"
}

try {
    $git = Get-Command "git" -ErrorAction SilentlyContinue
    if ($null -eq $git) {
        Stop-Deploy "Git was not found."
    }

    $repoRoot = (& $git.Source rev-parse --show-toplevel 2>$null | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($repoRoot)) {
        Stop-Deploy "This directory is not inside a Git repository."
    }

    $currentDir = (Get-Location).Path.TrimEnd('\', '/')
    $normalizedRoot = (Resolve-Path $repoRoot).Path.TrimEnd('\', '/')

    if (-not $currentDir.Equals($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        Stop-Deploy "Run this script from the repository root: $normalizedRoot"
    }

    if (-not (Test-Path $Dockerfile -PathType Leaf)) {
        Stop-Deploy "Expected API Dockerfile was not found at '$Dockerfile'. The API build context must be '$BuildContext'."
    }

    Assert-NoSensitiveBuildFiles

    $script:GCloud = Get-GCloudCommand
    $activeAccount = Ensure-GCloudAuthentication

    Write-Step "Configuring project $ProjectId and region $Region"
    Invoke-GCloud -Arguments @("config", "set", "project", $ProjectId) | Out-Null
    Invoke-GCloud -Arguments @("config", "set", "run/region", $Region) | Out-Null

    Write-Step "Reading the existing Cloud Run API service"
    Invoke-GCloud -Arguments @(
        "run", "services", "describe", $ApiService,
        "--project", $ProjectId,
        "--region", $Region
    ) | Out-Null

    $timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

    $PreviousRevision = Get-GCloudText @(
        "run", "services", "describe", $ApiService,
        "--project", $ProjectId,
        "--region", $Region,
        "--format=value(status.latestReadyRevisionName)"
    )

    $currentImage = Get-GCloudText @(
        "run", "services", "describe", $ApiService,
        "--project", $ProjectId,
        "--region", $Region,
        "--format=value(spec.template.spec.containers[0].image)"
    )

    $ApiUrl = Get-GCloudText @(
        "run", "services", "describe", $ApiService,
        "--project", $ProjectId,
        "--region", $Region,
        "--format=value(status.url)"
    )

    if ([string]::IsNullOrWhiteSpace($PreviousRevision)) {
        Stop-Deploy "Could not determine the current ready revision."
    }
    if ([string]::IsNullOrWhiteSpace($currentImage)) {
        Stop-Deploy "Could not determine the current API image."
    }

    Write-Step "Backing up the current API service"
    $serviceYaml = Get-GCloudText @(
        "run", "services", "describe", $ApiService,
        "--project", $ProjectId,
        "--region", $Region,
        "--format=export"
    )
    $serviceYaml | Out-File (Join-Path $BackupDir "api-$timestamp.yaml") -Encoding utf8

    $apiImageBase = Remove-ImageTagOrDigest $currentImage
    $gitSha = (& $git.Source rev-parse --short HEAD | Out-String).Trim()
    $dirtyStatus = (& $git.Source status --short -- $BuildContext | Out-String).Trim()

    if (-not [string]::IsNullOrWhiteSpace($dirtyStatus)) {
        Write-WarningMessage "The API build context has uncommitted or untracked changes:"
        Write-Host $dirtyStatus

        if (-not (Confirm-DeployAction "Build and deploy these local backend changes?")) {
            Stop-Deploy "Deployment cancelled."
        }

        $buildTag = "$gitSha-dirty-$timestamp"
    }
    else {
        $buildTag = "$gitSha-$timestamp"
    }

    $ApiImage = "${apiImageBase}:$buildTag"

    @"
project=$ProjectId
region=$Region
service=$ApiService
previous_revision=$PreviousRevision
previous_image=$currentImage
service_url=$ApiUrl
build_context=$BuildContext
dockerfile=$Dockerfile
"@ | Out-File (Join-Path $BackupDir "api-$timestamp-before.txt") -Encoding utf8

    Invoke-LocalTestsIfAvailable

    Write-Host ""
    Write-Host "API-only deployment summary" -ForegroundColor White
    Write-Host "  Account:           $activeAccount"
    Write-Host "  Project:           $ProjectId"
    Write-Host "  Region:            $Region"
    Write-Host "  Service:           $ApiService"
    Write-Host "  Previous revision: $PreviousRevision"
    Write-Host "  Current image:     $currentImage"
    Write-Host "  Build context:     $BuildContext"
    Write-Host "  Dockerfile:        $Dockerfile"
    Write-Host "  New image:         $ApiImage"
    Write-Host "  Worker job:        NOT TOUCHED"
    Write-Host ""

    if (-not (Confirm-DeployAction "Build and deploy this API revision?")) {
        Stop-Deploy "Deployment cancelled."
    }

    Write-Step "Building and pushing the API image through Cloud Build"
    Invoke-GCloud -Arguments @(
        "builds", "submit", ".\$BuildContext",
        "--project", $ProjectId,
        "--region", $Region,
        "--tag", $ApiImage
    )
    Write-Success "Cloud Build completed successfully"

    Write-Step "Deploying the new image to the existing API service"
    $DeploymentAttempted = $true

    Invoke-GCloud -Arguments @(
        "run", "services", "update", $ApiService,
        "--project", $ProjectId,
        "--region", $Region,
        "--image", $ApiImage,
        "--max", "1",
        "--max-instances", "1",
        "--quiet"
    )

    $NewRevision = Get-GCloudText @(
        "run", "services", "describe", $ApiService,
        "--project", $ProjectId,
        "--region", $Region,
        "--format=value(status.latestReadyRevisionName)"
    )

    $ApiUrl = Get-GCloudText @(
        "run", "services", "describe", $ApiService,
        "--project", $ProjectId,
        "--region", $Region,
        "--format=value(status.url)"
    )

    if ([string]::IsNullOrWhiteSpace($NewRevision)) {
        Stop-Deploy "Cloud Run did not report a ready revision."
    }
    if ($NewRevision -eq $PreviousRevision) {
        Stop-Deploy "Cloud Run still reports the previous revision as latest ready."
    }

    Write-Success "Cloud Run revision $NewRevision is ready"

    Invoke-HealthCheck -BaseUrl $ApiUrl
    Assert-OpenApiContract -BaseUrl $ApiUrl -Path $ExpectedOpenApiPath

    Write-Step "Recent API logs"
    try {
        Invoke-GCloud -Arguments @(
            "run", "services", "logs", "read", $ApiService,
            "--project", $ProjectId,
            "--region", $Region,
            "--limit", "50"
        )
    }
    catch {
        Write-WarningMessage "Could not read recent API logs: $($_.Exception.Message)"
    }

    @"
project=$ProjectId
region=$Region
service=$ApiService
previous_revision=$PreviousRevision
new_revision=$NewRevision
new_image=$ApiImage
service_url=$ApiUrl
health=passed
openapi_path=$ExpectedOpenApiPath
openapi_contract=passed
rollback_command=gcloud run services update-traffic $ApiService --project $ProjectId --region $Region --to-revisions $PreviousRevision=100
"@ | Out-File (Join-Path $BackupDir "api-$timestamp-deployed.txt") -Encoding utf8

    Write-Host ""
    Write-Success "API-only deployment completed"
    Write-Host "  Revision: $NewRevision"
    Write-Host "  Image:    $ApiImage"
    Write-Host "  URL:      $ApiUrl"
    Write-Host "  Record:   $(Join-Path $BackupDir "api-$timestamp-deployed.txt")"
    Write-Host ""
    Write-Host "Rollback command:"
    Write-Host "  gcloud run services update-traffic $ApiService --project $ProjectId --region $Region --to-revisions $PreviousRevision=100"
}
catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red

    if ($DeploymentAttempted -and -not [string]::IsNullOrWhiteSpace($PreviousRevision)) {
        Write-Host ""
        Write-WarningMessage "The previous revision is still available. Roll back with:"
        Write-Host "  gcloud run services update-traffic $ApiService --project $ProjectId --region $Region --to-revisions $PreviousRevision=100"
    }

    exit 1
}
