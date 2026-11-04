# ops/ — সার্ভিস হিসেবে চালানো

| ফাইল | কী কাজ | কখন |
|---|---|---|
| `lis-middleware.service` | systemd ইউনিট (Linux) | লিনাক্স সার্ভার |
| `lis-middleware.env.example` | DB পাসওয়ার্ড রাখার env ফাইল | `/etc/lis-middleware.env` (chmod 600) |
| `install_service_windows.ps1` | NSSM দিয়ে Windows সার্ভিস | Windows সার্ভার |
| `healthcheck.sh` | সার্ভিস জীবিত? পোর্ট খোলা? | cron / Zabbix / APEX "check" |

দুটোই `service.py` চালায় — অর্থাৎ **APEX-চালিত মোড**, আর কিছু বদলালে নিজে থেকেই
রিলোড হয়। `main.py` শুধু ডেভেলপারের জন্য (একবার পড়ে, একবার চালু)।

```bash
# Linux
sudo cp ops/lis-middleware.service /etc/systemd/system/
sudo install -m 600 ops/lis-middleware.env.example /etc/lis-middleware.env
sudo systemctl enable --now lis-middleware

# Windows (Administrator PowerShell)
.\install_service_windows.ps1 -ProjectDir "C:\lis\lis-middleware"
```

কেন `Restart=always` / `AppExit Default Restart`: কোনো অপ্রত্যাশিত এররেও
সার্ভিস ৫ সেকেন্ডে ফিরে আসে, আর অ্যানালাইজার তার নিজের রিট্রাই লজিক চালায় —
ল্যাবের মানুষ কিছু টের পায় না।

পাসওয়ার্ড কোথাও ফাইলে থাকে না: systemd-এ `EnvironmentFile`, Windows-এ
machine environment variable। `config.yaml`-এ শুধু host/service/user।
