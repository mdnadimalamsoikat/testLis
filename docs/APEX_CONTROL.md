# APEX দিয়ে সব নিয়ন্ত্রণ — অপারেটর গাইড

> **এক লাইনে:** ল্যাবের মানুষ শুধু APEX পেজ দেখবে। অ্যানালাইজার যোগ করা, পোর্ট বদলানো, বন্ধ-চালু করা — সব APEX থেকে। Python কোথায় আছে, কীভাবে চলছে, তা তাঁদের জানার দরকার নেই।

---

## ১. কেন এভাবে করা হচ্ছে

সাধারণ সেটআপে সার্ভারে গিয়ে `config.yaml` এডিট করতে হয়, তারপর সার্ভিস রিস্টার্ট। এতে তিনটা সমস্যা:

1. যিনি ল্যাব চালান, তিনি সার্ভারে ঢুকতে চান না — এবং ঢোকা উচিতও নয়।
2. কে, কখন, কী বদলেছে — তার কোনো হিসাব থাকে না।
3. ভুল করে একটা অক্ষর লিখলে পুরো সার্ভিস বন্ধ, অথচ অ্যানালাইজার চলছে।

তাই এখানে **Oracle টেবিলই একমাত্র সত্য** (single source of truth):

```
   APEX পেজ  ──►  Oracle টেবিল  ──►  Python সার্ভিস
 (বস/টেকনিশিয়ান)   (lis_config)      (প্রতি ৫ সেকেন্ডে দেখে)
        ▲                                    │
        └──── lis_analyzer_status ◄──────────┘
              (online / last message / error)
```

অপারেটর APEX-এ একটা ফর্মে পোর্ট বদলালেন → `config_version` বাড়ল → ৫ সেকেন্ডের মধ্যে শুধু সেই অ্যানালাইজারের লিসেনার রিস্টার্ট হল → APEX-এ সবুজ টিক ("applied") দেখা গেল। বাকি অ্যানালাইজারগুলোর কানেকশন ভাঙল না, সার্ভিস বন্ধ হল না, কেউ কিছু টের পেল না।

---

## ২. একবারের কাজ — ইনস্টল

### ২.১ ডেটাবেসে টেবিল তৈরি

SQL Developer-এ LIS স্কিমা ইউজার দিয়ে `database/schema.sql` চালান, তারপর:

```sql
@database/apex_install.sql
```

তৈরি হবে: `lis_config`, `lis_test_code_map`, `lis_commands`, `lis_analyzer_status`, এবং APEX-এর জন্য ভিউ `v_lis_analyzers`, `v_lis_settings`, `v_lis_health`, `v_lis_command_log`।

`apex_install.sql`-এ তিনটা ডেমো অ্যানালাইজার (Sysmex XN-1000, Mindray BS-240, Roche c311) আর পাঁচটা সেটিং সিড হয়ে যায় — নাম/পোর্ট নিজের ল্যাব অনুযায়ী বদলে নিন।

### ২.২ APEX-এ দুটো পেজ বানানো

`apex_install.sql` চালানোর পর APEX-এ (App Builder → Create Page → **Interactive Report**) এই ভিউগুলো দিয়ে পেজ বানান:

| # | পেজের নাম | Region source | কাজ |
|---|---|---|---|
| 1 | **অ্যানালাইজার** | `v_lis_analyzers` | তালিকা + ফর্ম (যোগ/সম্পাদন/বন্ধ-চালু) |
| 2 | **অ্যানালাইজার ফর্ম** | `lis_config` (form) | পোর্ট, প্রোটোকল, সিরিয়াল সেটিং |
| 3 | **সেটিংস** | `v_lis_settings` | ACK mode, log level, QC prefix |
| 4 | **টেস্ট কোড ম্যাপ** | `lis_test_code_map` | মেশিনের কোড → রিপোর্ট কোড |
| 5 | **কন্ট্রোল বাটন** | `lis_commands` | Reload / Start / Stop / Restart / Selftest |
| 6 | **হেলথ ড্যাশবোর্ড** | `v_lis_health` | কত অনলাইন, আজ কত রেজাল্ট, কত ক্রিটিক্যাল |

Interactive Report বানানোর সময় **Create Form → Form on a Table** দিয়ে পেজ ২ যোগ করলে সবচেয়ে কম কাজে হয়।

### ২.৩ ফর্মে যেসব নিয়ম বসাবেন (গুরুত্বপূর্ণ)

| কলাম | ফিল্ড টাইপ | নিয়ম |
|---|---|---|
| `analyzer_name` | Text | Required, Unique, **Read Only on edit** (নাম বদলালে হিস্টোরি ভেঙে যায়) |
| `protocol` | Select List | `ASTM`, `HL7`, `AUTO` — static LOV |
| `connection_type` | Select List | `TCP`, `SERIAL` |
| `port` | Number | 1024–65535, শুধু `TCP` হলে required |
| `serial_port` | Text | শুধু `SERIAL` হলে required (যেমন `COM3`) |
| `baud_rate`/`data_bits`/`parity`/`stop_bits` | Select List | 9600 / 7 / EVEN / 1 — ASTM সিরিয়ালের ডিফল্ট |
| `enabled` | Switch (Y/N) | বন্ধ করলে লিসেনার উঠে যায়, ডেটা হারায় না |
| `status` | Hidden | ডিফল্ট `ACTIVE` (নিচে দেখুন) |
| `test_codes` | Text | কমা দিয়ে: `WBC,RBC,HGB` |
| `test_code_map` | Textarea | JSON: `{"HGB":"HB"}` |

> **`status` কেন `DRAFT`?** নতুন অ্যানালাইজার যোগ করার সময় প্রথমে `DRAFT` রাখুন: সার্ভিস সেটা পড়বে না, তাই অর্ধেক লেখা পোর্ট কখনো লাইভ লিসেনারে যাবে না। সব লিখে ফেলার পর `ACTIVE` করুন → তখনই কার্যকর হবে। এডিটের সময়ও এই নিয়ম কাজে দেয়।

### ২.৪ বাটনগুলো (`lis_commands`)

APEX-এ ছোট একটা ফর্ম + "Create" বাটন, অথবা প্রসেস দিয়ে `INSERT INTO lis_commands (command_type, analyzer_name, payload) VALUES (...)` — দুইভাবেই হবে।

| `command_type` | কাজ | কখন চাপবেন |
|---|---|---|
| `RELOAD` | পুরো কনফিগ নতুন করে পড়ে | কোনো পরিবর্তন "applied" না হলে |
| `START` | এক অ্যানালাইজার চালু | ভুল করে বন্ধ হয়ে গেলে |
| `STOP` | এক অ্যানালাইজার বন্ধ | মেশিন সার্ভিসিং-এ গেলে |
| `RESTART` | বন্ধ করে চালু | পোর্ট আটকে গেলে |
| `SELFTEST` | ভেতরের নমুনা দিয়ে টেস্ট (ডেটাবেসে কিছু লেখে না) | নতুন অ্যানালাইজার যোগ করার পর যাচাই |
| `PING` | সার্ভিস জীবিত কি না | সন্দেহ হলে |

`SELFTEST`-এর `payload`-এ সিনারিওর নাম দিতে পারেন (`astm_cbc_normal`, `astm_cbc_critical`, `hl7_chemistry_normal` …)। ফলাফল `lis_commands.result_message`-এ ফিরে আসে, যেমন:

```
astm_cbc_normal: HALF_ACCEPTED, 4 stored, ack=ASTM (E1381 checksum ok)
```

---

## ৩. রোজকার কাজ

### ৩.১ নতুন অ্যানালাইজার যোগ করা

1. APEX → **অ্যানালাইজার** → Create
2. নাম (যেমন `Sysmex_XN_1000`), প্রোটোকল, TCP হলে পোর্ট (যেমন `6661`)
3. `status` = **DRAFT**, Save
4. অ্যানালাইজারের Host সেটিং-এ বসান: middleware-এর IP, সেই পোর্ট, ASTM/HL7, checksum on, 9600·7·EVEN·1
5. APEX-এ ফিরে `status` = **ACTIVE**, Save
6. ৫–১০ সেকেন্ড পর **স্টেটাস** কলামে `ONLINE` দেখবেন
7. `SELFTEST` চালান — সবুজ হলে অ্যানালাইজার থেকে একটা নমুনা পাঠান

### ৩.২ পোর্ট বা প্রোটোকল বদলানো

ফর্মে পোর্ট বদলে Save → ৫ সেকেন্ডের মধ্যে নতুন পোর্টে লিসেনার উঠবে, পুরোনোটা বন্ধ হবে। **সার্ভিস রিস্টার্ট লাগবে না।** অ্যানালাইজারের Host সেটিং-ও সেই পোর্টে বদলাতে ভুলবেন না (এটা APEX থেকে হয় না — মেশিনের প্যানেলে করতে হয়)।

### ৩.৩ কিছু ভুল হলে

`v_lis_analyzers`-এর `last_error` কলাম লাল দেখাবে এবং ঠিক কী ভুল:

| মেসেজ | কারণ | সমাধান |
|---|---|---|
| `port 6661 is outside 1024-65535` | ভুল পোর্ট | ঠিক করুন |
| `port 6661 used by both A and B` | দুই অ্যানালাইজার একই পোর্টে | আলাদা পোর্ট দিন |
| `serial_port is required for SERIAL` | COM নাম নেই | `COM3` দিন |
| `cannot open port 6661: address in use` | অন্য প্রোগ্রাম পোর্টটা ধরে আছে | `netstat -ano | findstr 6661` দিয়ে দেখুন |
| `state = STOPPED` কিন্তু enabled | পোর্ট খোলা যায়নি | উপরের দ্বিতীয়/চতুর্থ সারি |

> ভুল কনফিগ কখনো চালু কনফিগকে ভাঙে না — সার্ভিস আগের ঠিকঠাক কনফিগ নিয়েই চলতে থাকে, আর `last_error`-এ কারণ লেখে।

---

## ৪. ভেতরে কী ঘটে (একবার বুঝে নিলেই যথেষ্ট)

```
প্রতি ৫ সেকেন্ডে:
  1. lis_commands-এ PENDING কিছু আছে?  → চালাও, ফলাফল ফেরত লেখো
  2. lis_config-এর fingerprint বদলেছে?  → নতুন স্ন্যাপশট বানাও
       ├─ ভুল থাকলে:  last_error লিখে পুরোনো কনফিগ চালু রাখো
       └─ ঠিক থাকলে:  শুধু বদলানো লিসেনার রিস্টার্ট, বাকিগুলো অপরিবর্তিত
  3. lis_analyzer_status-এ হার্টবিট লেখো (ONLINE/STOPPED, last message)
  4. সফল হলে last_applied_at = SYSTIMESTAMP  → APEX-এ সবুজ "applied"
```

`config/config.yaml` ফাইলে এখন শুধু ডেটাবেসের ঠিকানা আর লগ-লেভেল থাকে; পাসওয়ার্ড থাকে সার্ভিসের environment-এ। বাকি সব — অ্যানালাইজার, পোর্ট, প্রোটোকল, টেস্ট কোড ম্যাপ — ডেটাবেসে।

---

## ৫. যাচাই করার কমান্ড (শুধু ইঞ্জিনিয়ারের জন্য)

```bash
# APEX-এর টেবিল আছে কি না, আর কী কী পড়ছে
python3 -c "from database.control import DatabaseControl; from database.db_connection import OracleDB; \
d=OracleDB('config/config.yaml'); d.connect(); c=DatabaseControl(d); s=c.load_config(); \
print(s.source, s.version); [print(' ', a.name, a.protocol, a.port, 'enabled' if a.enabled else 'off') for a in s.analyzers]"
```

```bash
python service.py --mode db          # APEX-চালিত মোড (প্রোডাকশন)
python service.py --mode file        # config.yaml-চালিত মোড (পুরোনো ধাঁচ)
python service.py --dry-run          # Oracle-এ কিছু লিখবে না, শুধু দেখাবে
```

রিস্টার্ট ছাড়াই কনফিগ বদলানো যাচাই করার সহজ উপায় (`--mode file`-এও একই কোডপথ চলে):

```bash
# এক টার্মিনালে: python service.py --mode file --dry-run
# config.yaml-এ পোর্ট 6661 → 6671 করে সেভ করুন, ৫ সেকেন্ড অপেক্ষা করুন
# লগে দেখবেন: "configuration changed (config.yaml) — reloading"
#             "TCP | listening on 0.0.0.0:6671"
```

---

## ৬. কে কী দেখবে (গোপনীয়তার নিয়ম)

| ভূমিকা | যা দেখবে | যা দেখবে না |
|---|---|---|
| ল্যাব ইনচার্জ | APEX: অনলাইন/অফলাইন, আজকের রেজাল্ট | সার্ভার, ফাইল, লগ |
| টেকনিশিয়ান | APEX: অ্যানালাইজার তালিকা, Selftest বাটন | Python, কমান্ড লাইন |
| IT (আপনি) | সব | — |

অ্যানালাইজার নিজেও কিছু জানে না: সে শুধু একটা TCP পোর্টে ডেটা পাঠায় আর `ACK` পায় — ঠিক যেমনটা একটা আসল LIS-এর সাথে কথা বলে। মেশিনের স্ক্রিনে কোনো "Python" শব্দ থাকবে না।
