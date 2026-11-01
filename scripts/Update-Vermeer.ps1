$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$settings = @{}
foreach ($line in Get-Content -LiteralPath (Join-Path $root '.env')) {
    if ($line -match '^\s*(?:export\s+)?(TELEGRAM_BOT_TOKEN|TELEGRAM_CHANNEL)\s*=\s*(.*?)\s*$') {
        $settings[$matches[1]] = $matches[2].Trim().Trim('"').Trim("'")
    }
}
if (!$settings['TELEGRAM_BOT_TOKEN'] -or $settings['TELEGRAM_CHANNEL'] -ne '@lartdevivre_journal') {
    throw 'Проверьте настройки Telegram. Отправка остановлена.'
}
function Invoke-Telegram([string]$Method, [hashtable]$Payload) {
    $endpoint = 'https://api.telegram.org/bot' + $settings['TELEGRAM_BOT_TOKEN'] + '/' + $Method
    $json = $Payload | ConvertTo-Json -Depth 8 -Compress
    try {
        $reply = Invoke-RestMethod -Method Post -Uri $endpoint -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($json)) -TimeoutSec 45
    } catch {
        throw 'Telegram не подтвердил действие. Проверьте канал перед повторным запуском. Подробности с секретным URL скрыты.'
    }
    if (!$reply.ok -or !$reply.result.photo) { throw 'Нет подтверждения сообщения с изображением.' }
    return $reply.result
}
$caption = Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $root 'content/2026-11-01-vermeer-short.txt')
if ($caption.Length -gt 1024) { throw 'Подпись слишком длинная.' }
$mainImage = 'https://www.mauritshuis.nl/media/wlola5to/0670_repro_2.jpg?height=0&quality=70&rxy=0.4724310776942356%2C0.4206012998019995&v=1dc16940cfd4cd0&width=2000'
$detailImage = 'https://www.mauritshuis.nl/media/wgmhiu1g/vermeer-meisje-met-de-parel-detail-second-canvas.jpg?height=0&quality=70&v=1dcaa2b97b15850&width=1200'
$editReceipt = Join-Path $root 'content/2026-11-01-vermeer-media.receipt.json'
if (!(Test-Path -LiteralPath $editReceipt)) {
    $edited = Invoke-Telegram 'editMessageMedia' @{
        chat_id = '@lartdevivre_journal'; message_id = 17
        media = @{ type = 'photo'; media = $mainImage; caption = $caption }
    }
    @{ message_id = $edited.message_id; edit_date = $edited.edit_date; image_source = $mainImage; caption = $edited.caption } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $editReceipt -Encoding utf8
    Write-Output ('Updated: https://t.me/lartdevivre_journal/' + $edited.message_id)
}
$detailReceipt = Join-Path $root 'content/2026-11-01-vermeer-detail.receipt.json'
if (!(Test-Path -LiteralPath $detailReceipt)) {
    $detail = Invoke-Telegram 'sendPhoto' @{
        chat_id = '@lartdevivre_journal'; photo = $detailImage
        caption = 'Деталь: блик и отражение воротника создают объём серьги. Изображение: Mauritshuis, The Hague.'
        reply_parameters = @{ message_id = 17 }; disable_notification = $true
    }
    @{ message_id = $detail.message_id; date = $detail.date; image_source = $detailImage } | ConvertTo-Json | Set-Content -LiteralPath $detailReceipt -Encoding utf8
    Write-Output ('Detail: https://t.me/lartdevivre_journal/' + $detail.message_id)
}
