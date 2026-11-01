param([Parameter(Mandatory = $true)][string]$PostPath)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$path = (Resolve-Path -LiteralPath $PostPath).Path
$post = Get-Content -Raw -Encoding utf8 -LiteralPath $path | ConvertFrom-Json
$receiptPath = "$path.receipt.json"
$pendingPath = "$path.pending.json"
if (Test-Path -LiteralPath $receiptPath) { Write-Output 'Already published; skipped.'; exit 0 }
if (Test-Path -LiteralPath $pendingPath) { throw 'Предыдущая попытка требует проверки канала. Автоповтор остановлен.' }
if (!$post.caption -or $post.caption.Length -gt 1024 -or $post.caption -match 'https?://|www\.') { throw 'Нужна короткая подпись без URL.' }
$photos = @($post.images)
if ($photos.Count -lt 1 -or $photos.Count -gt 3 -or !$post.source_url) { throw 'Нужны 1–3 изображения и сохранённый первоисточник.' }
foreach ($photo in $photos) { if ($photo -notmatch '^https://') { throw 'Изображения должны использовать HTTPS.' } }
$settings = @{}
foreach ($line in Get-Content -LiteralPath (Join-Path $root '.env')) {
    if ($line -match '^\s*(?:export\s+)?(TELEGRAM_BOT_TOKEN|TELEGRAM_CHANNEL)\s*=\s*(.*?)\s*$') {
        $settings[$matches[1]] = $matches[2].Trim().Trim('"').Trim("'")
    }
}
if (!$settings['TELEGRAM_BOT_TOKEN'] -or $settings['TELEGRAM_CHANNEL'] -ne '@lartdevivre_journal') { throw 'Проверьте настройки Telegram.' }
$payload = @{ chat_id = '@lartdevivre_journal' }
if ($photos.Count -eq 1) {
    $method = 'sendPhoto'; $payload.photo = $photos[0]; $payload.caption = $post.caption
} else {
    $method = 'sendMediaGroup'
    $media = @()
    for ($i = 0; $i -lt $photos.Count; $i++) {
        $item = @{ type = 'photo'; media = $photos[$i] }
        if ($i -eq 0) { $item.caption = $post.caption }
        $media += $item
    }
    $payload.media = $media
}
$endpoint = 'https://api.telegram.org/bot' + $settings['TELEGRAM_BOT_TOKEN'] + '/' + $method
$json = $payload | ConvertTo-Json -Depth 8 -Compress
@{ attempted_at = [DateTime]::UtcNow.ToString('o'); source_url = $post.source_url } | ConvertTo-Json | Set-Content -LiteralPath $pendingPath -Encoding utf8
try {
    $reply = Invoke-RestMethod -Method Post -Uri $endpoint -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($json)) -TimeoutSec 45
} catch {
    throw 'Нет подтверждения Telegram. Проверьте канал перед повтором; секретный URL скрыт.'
}
if (!$reply.ok) { throw 'Telegram не подтвердил публикацию.' }
$messages = @($reply.result)
if ($messages.Count -ne $photos.Count -or @($messages | Where-Object { !$_.photo -or !$_.message_id }).Count) { throw 'Неполное подтверждение Telegram.' }
$receipt = @{ message_ids = @($messages | ForEach-Object { $_.message_id }); published_at = [DateTime]::UtcNow.ToString('o'); source_url = $post.source_url; post_sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash }
$receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receiptPath -Encoding utf8
Write-Output ('Published: https://t.me/lartdevivre_journal/' + $messages[0].message_id)
