# اجرا روی VPS لینوکس

راهنمای گام‌به‌گام برای بالا آوردن ponishabot روی یک VPS اوبونتو/دبیان.

---

## ۰) پیش‌نیاز: یک بار لاگین

لاگین پونیشا **از داخل داشبورد** انجام می‌شود (شماره موبایل → کد پیامک).
نیازی به نمایشگر یا Selenium برای لاگین نیست.

---

## ۱) بسته‌های سیستمی

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git \
    chromium-browser chromium-chromedriver \
    fonts-noto fonts-liberation \
    libnss3 libatk-bridge2.0-0 libgbm1 libasound2 libxss1
```

> اگر `chromium-browser` در مخزن نبود، Chrome رسمی را نصب کن:
> `wget -qO- https://dl.google.com/linux/linux_signing_key.pub | sudo gpg --dearmor -o /usr/share/keyrings/google.gpg`
> سپس `.deb` گوگل را نصب کن. در این حالت `chromedriver` را هم دانلود کن.

---

## ۲) کاربر سرویس + کد

```bash
sudo useradd -r -m -d /opt/ponishabot -s /bin/bash ponisha
sudo -u ponisha git clone <REPO_URL> /opt/ponishabot
cd /opt/ponishabot
sudo -u ponisha python3 -m venv venv
sudo -u ponisha venv/bin/pip install -r requirements.txt
```

---

## ۳) تنظیمات

```bash
sudo -u ponisha cp config.example.yaml config.yaml
sudo -u ponisha nano config.yaml
```

موارد **الزامی** در `config.yaml`:

| کلید | مقدار روی VPS | چرا |
|---|---|---|
| `browser.headless` | `true` | VPS نمایشگر ندارد |
| `browser.show_browser` | `false` | پنجرهٔ زنده معنا ندارد |
| `ai.base_url` | آدرس قابل‌دسترس روتر AI | `localhost` یعنی خود VPS |
| `telegram.bot_token` / `chat_id` | مقادیر واقعی | اعلان‌ها |
| `bid.dry_run` | `true` (تا اطمینان) | هیچ bid واقعی زده نشود |

سپس فایل محیط سرویس:

```bash
sudo -u ponisha cp deploy/bot.env.example deploy/bot.env
sudo chmod 600 deploy/bot.env
sudo -u ponisha nano deploy/bot.env      # SECRET_KEY و ALLOWED_HOSTS را پر کن
```

---

## ۴) فایل‌های ضروری که باید کپی شوند

```bash
# فونت‌ها (برای UI فارسی) — از مخزن یا سیستم
ls assets/fonts/     # باید سه TTF وزیرمتن داشته باشد

# کش مهارت‌ها (اختیاری، خودش می‌سازد)
# data/skills_cache.json
```

`ponisha.db` لازم **نیست** — با OTP از داشبورد لاگین می‌کنی و خودش ساخته می‌شود.

---

## ۵) سرویس systemd

```bash
sudo cp deploy/ponishabot.service /etc/systemd/system/
sudo mkdir -p /var/log/ponishabot && sudo chown ponisha:ponisha /var/log/ponishabot
sudo systemctl daemon-reload
sudo systemctl enable --now ponishabot
sudo systemctl status ponishabot
```

> **مهم:** ربات در **یک process** زندگی می‌کند (singleton + thread + Selenium).
> هرگز چند worker/instance همزمان اجرا نکن — دو برابر bid می‌زند.

---

## ۶) دسترسی از بیرون (امنیت!)

داشبورد **هیچ احراز هویتی ندارد** و `/api/settings` توکن تلگرام و کلید AI را
برمی‌گرداند. پس:

**گزینهٔ پیشنهادی — nginx + رمز:**

```bash
sudo apt install -y nginx apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd ponisha
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/ponishabot
sudo ln -s /etc/nginx/sites-available/ponishabot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

**گزینهٔ جایگزین — SSH tunnel (بدون باز کردن پورت):**

```bash
ssh -L 8000:127.0.0.1:8000 user@VPS_IP
# بعد در مرورگر خودت: http://127.0.0.1:8000
```

**هرگز** پورت ۸۰۰۰ را بدون رمز روی `0.0.0.0` باز نکن.

---

## ۷) بررسی سلامت

```bash
sudo systemctl status ponishabot
sudo journalctl -u ponishabot -n 50 --no-pager
tail -f /var/log/ponishabot/service.log
tail -f ponishabot.log              # لاگ خود ربات
```

در داشبورد: `/api/status` باید `has_cookies: true` و `quota` برگرداند.

---

## ۸) نکات عملیاتی

- **کوکی منقضی شد؟** دکمهٔ Login در داشبورد → شماره → کد پیامک.
- **آپدیت کد:** `sudo -u ponisha git pull && sudo systemctl restart ponishabot`
- **لاگ بی‌نهایت رشد می‌کند:** `/etc/logrotate.d/ponishabot` برای `ponishabot.log` بگذار.
- **روتر AI روی ویندوز خودت است؟** یا `base_url` را به آدرس عمومی/تانل عوض کن،
  یا `ai.enabled: false` بگذار (heuristic کار می‌کند، فقط قیمت‌گذاری ساده‌تر).
- **چند worker:** ممنوع. فقط یک process.
