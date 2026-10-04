# Сборка и (пере)запуск CatDetect на Windows.
# Запускается задачей Планировщика «CatDetectDeploy» в сессии пользователя: так Docker Desktop
# имеет доступ к хранилищу учётных данных Windows (из SSH-сессии оно недоступно).
#   schtasks /Run /TN CatDetectDeploy
# Лог: C:\catdetect-data\deploy.log

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
$log = "C:\catdetect-data\deploy.log"

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') deploy start ===" | Out-File -FilePath $log -Encoding utf8
Set-Location $repo
docker compose -f docker/compose.yml up -d --build *>&1 | Out-File -FilePath $log -Append -Encoding utf8
"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') deploy exit $LASTEXITCODE ===" | Out-File -FilePath $log -Append -Encoding utf8
