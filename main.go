package main

import (
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

func main() {
	token := strings.TrimSpace(os.Getenv("TELEGRAM_BOT_TOKEN"))
	channel := strings.TrimSpace(os.Getenv("TELEGRAM_CHANNEL"))

	if token == "" || channel == "" {
		fmt.Println("Ошибка: TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL не заданы")
		os.Exit(1)
	}

	message := "🎨 L'ART DE VIVRE\n\n" +
		"Тестовая публикация!\n\n" +
		"Наш интеллектуальный журнал готовится к автоматическим публикациям. 🤍"

	apiURL := "https://api.telegram.org/bot" + token + "/sendMessage"

	data := url.Values{}
	data.Set("chat_id", channel)
	data.Set("text", message)

	client := &http.Client{Timeout: 20 * time.Second}

	resp, err := client.PostForm(apiURL, data)
	if err != nil {
		fmt.Println("Ошибка подключения:", err)
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if err != nil {
		fmt.Println("Ошибка чтения ответа:", err)
		return
	}

	if resp.StatusCode != http.StatusOK {
		fmt.Println("Telegram вернул HTTP", resp.StatusCode)
		fmt.Println("Проверь токен, канал и права бота.")
		return
	}

	if !strings.Contains(string(body), `"ok":true`) {
		fmt.Println("Telegram не подтвердил публикацию.")
		return
	}

	fmt.Println("Успешно! Сообщение опубликовано в", channel)
}
