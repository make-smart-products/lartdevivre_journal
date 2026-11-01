param(
    [Parameter(Mandatory = $true)][string]$PostPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$postFile = (Resolve-Path -LiteralPath $PostPath).Path
$receiptFile = "$postFile.receipt.json"
if (Test-Path -LiteralPath $receiptFile) {
    throw 'Для этого материала уже сохранена квитанция. Повторная отправка остановлена.'
}

# Read values without displaying the token or executing .env as shell code.
$settings = @{}
foreach ($line in Get-Content -LiteralPath (Join-Path $projectRoot '.env')) {
    if ($line -match '^\s*(?:export\s+)?(TELEGRAM_BOT_TOKEN|TELEGRAM_CHANNEL)\s*=\s*(.*?)\s*$') {
        $settings[$matches[1]] = $matches[2].Trim().Trim('"').Trim("'")
    }
}
if (!$settings['TELEGRAM_BOT_TOKEN'] -or !$settings['TELEGRAM_CHANNEL']) {
    throw 'В .env отсутствуют настройки Telegram.'
}
if ($settings['TELEGRAM_CHANNEL'] -ne '@lartdevivre_journal') {
    throw 'Канал в .env не совпадает с @lartdevivre_journal. Отправка остановлена.'
}
$message = Get-Content -LiteralPath $postFile -Raw -Encoding utf8
if ([string]::IsNullOrWhiteSpace($message) -or $message.Length -gt 4096) {
    throw 'Текст пуст или превышает лимит Telegram.'
}
$payload = @{ chat_id = $settings['TELEGRAM_CHANNEL']; text = $message } | ConvertTo-Json -Compress
$endpoint = 'https://api.telegram.org/bot' + $settings['TELEGRAM_BOT_TOKEN'] + '/sendMessage'
try {
    $response = Invoke-RestMethod -Method Post -Uri $endpoint -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 30
} catch {
    # Do not print exception details: request URLs contain the bot token.
    throw 'Не получено подтверждение Telegram. Проверьте канал перед повторной отправкой, чтобы избежать дубля.'
}
if (!$response.ok -or !$response.result.message_id) {
    throw 'Telegram не подтвердил публикацию.'
}
$receipt = [ordered]@{
    message_id = $response.result.message_id
    channel = $settings['TELEGRAM_CHANNEL']
    telegram_date = $response.result.date
    url = 'https://t.me/lartdevivre_journal/' + $response.result.message_id
    post_sha256 = (Get-FileHash -LiteralPath $postFile -Algorithm SHA256).Hash
}
$receipt | ConvertTo-Json | Set-Content -LiteralPath $receiptFile -Encoding utf8
$receipt | ConvertTo-Json -Compress | Write-Output
