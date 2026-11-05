# LOCAL_SETUP.md — আপনার নিজের ল্যাপটপে (APEX + 19c আগেই আছে)

> আপনি ইতিমধ্যে APEX আর Oracle 19c ইনস্টল করেছেন। তাই আর কিছু ইনস্টল করার দরকার নেই —
> শুধু **LIS-এর টেবিলগুলো বানানো, APEX-এ দুটো পেজ বসানো, আর সার্ভিস চালু করা।**
> মোট সময়: ২০–৩০ মিনিট।

---

## ০. আগে দেখে নিন — কোন service name?

Windows-এ cmd/PowerShell-এ:

```powershell
lsnrctl status
```

`Service "..." has 1 instance(s)` লাইনে নাম দেখবেন। সাধারণত:

| আপনার ইনস্টল | service name | DSN |
|---|---|---|
| Oracle Database 18c/21c **XE** | `XEPDB1` | `localhost:1521/XEPDB1` |
| Oracle 19c (DB install, CDB) | `ORCLPDB1` | `localhost:1521/ORCLPDB1` |
| Oracle 19c, non-CDB | `ORCL` | `localhost:1521/ORCL` |

আর APEX কোন স্কিমায় আছে দেখতে (SYSDBA হিসেবে):

```sql
SELECT version_no FROM apex_release;                    -- APEX 19.1 / 21.2 / 22.1 …
SELECT username FROM all_users WHERE username LIKE 'APEX%';   -- APEX_190100 / APEX_210200 …
```

> APEX যে PDB-তে আছে, LIS-এর টেবিলও **সেই PDB-তেই** বানান — নাহলে APEX পেজ
> `ORA-00942: table or view does not exist` দেবে।

---

## ১. এক কমান্ডে সব (সবচেয়ে সহজ)

`lis-middleware` ফোল্ডারে:

```powershell
pip install oracledb

python tools\apex_setup.py --user lis_user --service XEPDB1
```

পাসওয়ার্ড জিজ্ঞেস করবে। যা হবে:

```
→ কানেক্ট করছি lis_user@localhost:1521/XEPDB1
✅ connected — XEPDB1/ORCL, Oracle 19.0.0.0.0
→ schema.sql            (৪টি টেবিল + ৩টি ভিউ)
→ apex_install.sql      (কন্ট্রোল টেবিল + v_lis_* ভিউ + ট্রিগার + ডেমো ডেটা)
→ যাচাই করছি
   ✓ TABLE    LIS_CONFIG
   ✓ VIEW     V_LIS_ANALYZERS
   ...
   ✓ APEX 21.2 এই ডেটাবেসেই আছে
   3 অ্যানালাইজার: Sysmex_XN_1000 ASTM port 6661 …
```

কোনো ধাপ ইতিমধ্যে হয়ে থাকলে (`ORA-00955`) স্ক্রিপ্ট নিজেই "already there" বলে এগিয়ে যায় —
তাই **আবার চালানো নিরাপদ**।

`lis_user` এখনো না থাকলে (একবার, SYSDBA লাগবে):

```powershell
python tools\apex_setup.py --create-user --user lis_user --service XEPDB1
```

কনফিগ ফাইলও চাইলে একসাথে লিখে দিতে পারে:

```powershell
python tools\apex_setup.py --user lis_user --service XEPDB1 --write-config
```

---

## ২. অথবা SQL*Plus দিয়ে (যদি সেটাই বেশি আরাম)

```powershell
sqlplus lis_user/password@//localhost:1521/XEPDB1 @database/schema.sql
sqlplus lis_user/password@//localhost:1521/XEPDB1 @database/apex_install.sql
```

কোন statement-এ আটকালে ওভাবে বোঝা যায় না — তাই ভাগ করে চালানোর টুল:

```powershell
python tools\install_sql.py --sqlplus "sqlplus -S lis_user/password@//localhost:1521/XEPDB1"
```

এটা প্রতিটা ব্লক আলাদা চালায়, "already there" চিনে নেয়, আর আসল এররে **সেই statement
কপি করে দেখায়** যাতে SQL Developer-এ চালিয়ে ঠিক করা যায়।

---

## ৩. APEX-এ পেজগুলো (একবার, ক্লিকে ক্লিকে)

App Builder → নতুন অ্যাপ (নাম `LIS`) → পেজ ২–৭:

| পেজ | ধরন | Source | কাজ |
|---|---|---|---|
| ২ | Interactive Report | `SELECT * FROM v_lis_analyzers` | অনলাইন/অফলাইন, শেষ মেসেজ |
| ৩ | **Form on a Table** (`LIS_CONFIG`) | key: `CONFIG_ID` | **এখানেই পোর্ট/প্রোটোকল বদলান** |
| ৪ | IR + Form | `v_lis_settings` / `lis_config` | ACK mode, log level |
| ৫ | IR + Form | `lis_test_code_map` | মেশিন কোড → রিপোর্ট কোড |
| ৬ | Form | `lis_commands` | Reload / Start / Stop / Restart / Selftest |
| ৭ | Classic Report (auto-refresh 10s) | `v_lis_health` | ড্যাশবোর্ড |

পেজ ২ → ৩ লিংক: `f?p=&APP_ID.:3:&SESSION.::&DEBUG.:3:P3_CONFIG_ID.:#CONFIG_ID#`

প্রতিটি ফিল্ডের নিয়ম (কোনটা Required, `status = DRAFT` কেন, LOV কী) —
[`APEX_CONTROL.md`](APEX_CONTROL.md) §২.৩।

---

## ৪. সার্ভিস চালু

```powershell
# পাসওয়ার্ড ফাইলে নয়, environment-এ
$env:LIS_DB_PASSWORD = "your-password"

cd lis-middleware
python service.py
```

লগে যা দেখা উচিত:

```
configuration source: Oracle control tables (LIS_USER.lis_config)
Sysmex_XN_1000         | TCP | listening on 0.0.0.0:6661
Mindray_BS240          | TCP | listening on 0.0.0.0:6662
service started with 2 listener(s) (database)
watching for APEX changes every 5s
```

তারপর অ্যানালাইজারের বদলে simulator:

```powershell
python tools\device_sim.py -s astm_cbc_normal -p 6661 --trace
```

আর APEX পেজ ২-এ `Sysmex_XN_1000` → **ONLINE**, `messages_received` বাড়ছে — এটাই প্রমাণ
যে দুই পাশ জোড়া লেগেছে।

### আসল সার্ভিস হিসেবে (বন্ধ হলে নিজেই ফিরে আসবে)

```powershell
.\ops\install_service_windows.ps1 -ProjectDir "C:\lis\lis-middleware"
nssm status LISMiddleware
```

---

## ৫. এখন "APEX থেকেই সব" যাচাই করার ২ মিনিটের টেস্ট

1. APEX পেজ ৬ → নতুন রো: `command_type = SELFTEST`, `analyzer_name = Sysmex_XN_1000`,
   `payload = astm_cbc_normal` → Save
2. ৫ সেকেন্ড পর ওই রো-তে `status = DONE`, `result_message` = `astm_cbc_normal: ACCEPTED, 4 stored …`
3. পেজ ৩-এ সবুজ `APPLIED` + `last_applied_at`
4. এবার পোর্ট `6661` → `6671` করে Save → লগে `listening on 0.0.0.0:6671`, পুরোনো পোর্ট বন্ধ
5. মেশিনের Host সেটিং-এও পোর্ট বদলাতে হবে (এটা APEX থেকে হয় না — মেশিনের প্যানেলে)

ভুল করে পোর্ট `80` দিয়ে Save করলে: পোর্ট খুলবে না, `last_error = port 80 is outside 1024-65535`
দেখাবে, আর **পুরোনো কনফিগ চালু থাকবে** — মেসেজ আসতেই থাকবে।

---

## ৫.২ `ORA-00932` — APEX ইনস্টলের সাথে এর কোনো সম্পর্ক নেই

```
ORA-00932: inconsistent datatypes: expected NUMBER got INTERVAL DAY TO SECOND
   at: CREATE OR REPLACE VIEW v_lis_analyzers ...
```

APEX ইনস্টল আছে কি নেই, তার সাথে এই এররের সম্পর্ক শূন্য। এটা **আমার SQL-এর ভুল**:

> Oracle-এ `TIMESTAMP − TIMESTAMP = INTERVAL DAY TO SECOND`, কোনো NUMBER নয়।
> তাই `(SYSTIMESTAMP − last_seen_at) * 86400` লিখলে 19.3 সরাসরি ORA-00932 দেয়।

**সমাধান — এক ফাইল, এক মিনিট** (`fix_02` নিজেই বাকি সব শেষ করে দেয়):

```powershell
# SQL Developer → TESTLIS সংযোগ → database\fix\fix_02_finish_install.sql খুলুন → F5
# অথবা
python tools\install_sql.py --sqlplus "sqlplus -S TESTLIS/pass@//localhost:1521/ORCL" ^
    --file database\fix\fix_02_finish_install.sql
```

ফাইলটা ক্রম মেনে কাজ করে: **(১) কলাম** (শুধু যেগুলো নেই — `ORA-01430` এড়াতে) → **(২) চারটি ভিউ**
(INTERVAL-নিরাপদ হিসাব সহ) → **(৩) দুটো ট্রিগার** → **(৪) ডেমো ডেটা** (`NOT EXISTS`, তাই আবার
চালালে ডুপ্লিকেট হয় না) → **(৫) দুটো যাচাই-কুয়েরি**।

শেষে যা দেখা উচিত:

```
ANALYZER_NAME          PROTOCOL ENDPOINT               STATE     MESSAGES_RECEIVED
Sysmex_XN_1000         ASTM     0.0.0.0:6661           UNKNOWN                   0
Mindray_BS240          HL7      0.0.0.0:6662           UNKNOWN                   0
Roche_Cobas_c311       ASTM     COM3 @ 9600            UNKNOWN                   0

VIEWS_CREATED
            4
```

তারপর:

```powershell
python tools\apex_setup.py --verify-only --user TESTLIS --service ORCL
```

সব ✓ এলে ইনস্টল শেষ। (`STATE = UNKNOWN` স্বাভাবিক — `service.py` চালু হলেই ONLINE হবে।)

## ৬. আটকে গেলে

| যা দেখছেন | কারণ | সমাধান |
|---|---|---|
| `ORA-12514` | service name ভুল | `lsnrctl status` দেখে ঠিক করুন |
| `ORA-12541` | listener চলছে না | `lsnrctl start` |
| `ORA-01017` | ইউজার/পাস ভুল | `sqlplus lis_user/pass@//localhost:1521/XEPDB1` দিয়ে যাচাই |
| `DPY-6005` | host/port/service-ই মেলেনি | উপরের তিনটা |
| APEX পেজে `ORA-00942` | টেবিল অন্য PDB-তে | যে PDB-তে APEX, সেখানেই স্ক্রিপ্ট চালান |
| `listening on` আসছে না | পোর্ট অন্য প্রোগ্রামে | `netstat -ano | findstr 6661` |
| APEX-এ `STOPPED` | `status = DRAFT` বা পোর্ট খোলা যায়নি | `v_lis_analyzers.last_error` দেখুন |
| কিছুই বদলাচ্ছে না | `service.py` নয়, `main.py` চলছে | বন্ধ করে `python service.py` চালান |

সব একসাথে দেখতে:

```powershell
python tools\preflight.py            # ❌ থাকলে আগে সেটা ঠিক করুন
python tools\apex_setup.py --verify-only --user lis_user --service XEPDB1
```
