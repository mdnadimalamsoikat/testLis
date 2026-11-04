# Real শুরু করবেন কীভাবে — ৩ ধাপে ল্যাব ডে

> এখন অবস্থান: ডেমো দেখেছেন, ভালো লেগেছে ✅
> প্রশ্ন: "আসলটা কীভাবে শুরু করব?" — উত্তর: **আজ সিমুলেটরে, কাল নিজের কনফিগে,
> পরশু ল্যাবে।** ল্যাবে গিয়ে শুধু মেশিনের Host সেটিং-এ IP/পোর্ট বসাবেন।
> আর "Mirth ছাড়া অন্য সফটওয়্যার?" — হ্যাঁ, অনেক আছে (§২)।

---

## ০. এক নজরে

| কবে | কোথায় | কী করবেন | সফলতার মাপকাঠি |
|---|---|---|---|
| **আজ (২-৩ ঘণ্টা)** | ঘরে | আসল মেশিনের মতো simulator চালিয়ে সব ফরম্যাট/ফল্ট টেস্ট | `device_sim` ✅ + ৫৩ টেস্ট পাস |
| **কাল (২ ঘণ্টা)** | ঘরে | আপনার DB-তে `schema.sql`, `preflight`, `--dry-run` ছাড়া আসল ইনসার্ট | APEX-এ একটা রো দেখা যায় |
| **পরশু (ল্যাব)** | ল্যাব | মেশিনের Host সেটিং + এক কন্ট্রোল স্যাম্পল | লগে `parsed N result(s)` + APEX রিপোর্ট |

---

## ১. আজ — আসল মেশিনের সিমুলেশন (Mirth ছাড়া)

### ১.১ আমাদের নিজের টুলটাই আগে

```bash
cd lis-middleware
python -m unittest discover -s tests -v        # ৫৩টি টেস্ট পাস হচ্ছে কি?
python main.py --dry-run                       # টার্মিনাল ১ (পোর্ট 6661/6662 খোলে)
```

নতুন টার্মিনালে:

```bash
python tools/device_sim.py -s astm_cbc_normal -p 6661 --trace
```

দেখবেন আসল E1381 ডায়ালগ: `ENQ → ACK → STX 1 … ETX ⟨cs⟩ → ACK … → EOT`।
তারপর কেবল-এর ঝামেলা বানিয়ে দেখুন:

```bash
python tools/device_sim.py -s astm_cbc_normal --fault checksum --fault-rate 0.4
python tools/device_sim.py -s astm_cbc_normal --fault noise    --fault-rate 0.4
python tools/device_sim.py -s astm_cbc_normal --fault drop     --fault-rate 0.25
python tools/device_sim.py -s hl7_critical   -p 6662 --trace
```

### ১.২ আর কী কী সিমুলেটর আছে (Mirth ছাড়া)

| টুল | কী ভালো | সীমা | কখন ব্যবহার করবেন |
|---|---|---|---|
| **`tools/device_sim.py`** (এই প্রজেক্টে) | E1381 হ্যান্ডশেক + চেকসাম + NAK-রিট্রাই + নয়েজ + HLA MLLP; CLI, স্ক্রিপ্টেবল | নিজের সিনারিও বানাতে হবে | **প্রথম পছন্দ** — এটাই আপনার মিডলওয়্যারের সাথে মিলিয়ে বানানো |
| **Lab Analyzer Simulator** (ওপেন সোর্স, GitHub) | ব্রাউজার প্যানেল, HL7+ASTM রেকর্ড, Docker, ফিক্সচার, ফল্ট সিনারিও | থার্ড-পার্টি; আপনার ফরম্যাটে মেলাতে হবে | দৃশ্যমান ডেমো চাইলে, বা টিমকে দেখাতে |
| **Sand Lake Simulator** | লো-লেভেল ASTM/HL7 ডিবাগিং, ট্রান্সমিশন ফাইল করে রাখা | কমার্শিয়াল/লাইসেন্স | ইন্টারফেস নিয়ে ভেন্ডরের সাথে ঝগড়া হলে |
| **HEDONICS ASTM Simulator** | ইনস্ট্রুমেন্ট ↔ LIS দুই দিকেই খেলে | কমার্শিয়াল | ASTM ল্যাবে বড় প্রজেক্ট হলে |
| **Roche ASTM Host Simulator** | Roche-এর নিজের টুল | শুধু Roche কাস্টমারদের জন্য | Roche মেশিনের ক্ষেত্রে ভেন্ডরের কাছে চাইবেন |
| **HL7 Inspector / 7Edit** | HL7 মেসেজ দেখা/এডিট (MLLP) | ASTM বোঝে না | শুধু Mindray/HL7 পথ |
| **com0com / HHD Free Virtual Serial Ports** | ভার্চুয়াল COM পেয়ার (`COM3↔COM4`) | ড্রাইভার ইনস্টল করতে হয় | সিরিয়াল (RS-232) পথটা ঘরে টেস্ট করতে |

> ⚠️ কমার্শিয়াল টুলগুলোর দাম/লাইসেন্স সময়ে সময়ে বদলায় — কেনার আগে নিজে যাচাই করুন।
> আপনার কাজের ৯০% আমাদের `device_sim` দিয়েই হয়ে যাবে।

### ১.৩ সিরিয়াল পথটাও ঘরে টেস্ট করুন (com0com)

1. **com0com** (বা HHD-এর ফ্রি ভার্সন) দিয়ে `COM3 ↔ COM4` পেয়ার বানান।
2. `config.yaml`-এ: `connection_type: SERIAL`, `serial_port: "COM3"`, `baud_rate: 9600`, `parity: "EVEN"`, `data_bits: 7`।
3. `python main.py --dry-run`
4. অন্য দিক থেকে পাঠান: যেকোনো টার্মিনাল সফটওয়্যার COM4-এ খুলে ASTM লাইন টাইপ করুন, বা
   `python tools/mock_analyzer.py -s astm_cbc_normal --serial COM4`

এতে pyserial + সিরিয়াল লিসেনার + E1381 রিসিভ — তিনটাই আসলভাবে চলবে।

---

## ১.৪ নিয়ন্ত্রণ কোথায় — ফাইল নয়, APEX

ল্যাবে যাওয়ার আগে এই সিদ্ধান্তটা পাকা করে নিন: **অ্যানালাইজার যোগ করা, পোর্ট বদলানো,
একটা মেশিন সাময়িক বন্ধ করা — সব APEX থেকে হবে।** সার্ভারে গিয়ে `config.yaml`
এডিট করা চলবে না, কারণ ল্যাবের লোক সার্ভারে ঢুকবে না (এবং ঢোকা উচিতও নয়)।

```bash
# একবার: কন্ট্রোল টেবিল বানান
sqlplus lis_user/password@//192.168.1.100:1521/ORCL @database/apex_install.sql
```

এরপর সার্ভারে চালান `python service.py` (সার্ভিস হিসেবে ইনস্টল করলে নিজেই চলে)।
APEX-এ একটা পোর্ট বদলে Save করলেই ~৫ সেকেন্ডে কার্যকর — রিস্টার্ট লাগে না।
কোন ফিল্ডে কী নিয়ম বসাবেন, ভুল হলে কী হবে — [`APEX_CONTROL.md`](APEX_CONTROL.md)।

> ঘরে rehearsal করার সময়ও একই কোড পথ চালানো যায়: `python service.py --mode file`
> → `config.yaml` সেভ করলেই নিজে থেকে reload হয় (লগে "configuration changed")।
> এতে ল্যাবে যাওয়ার আগেই hot-reload টা যাচাই হয়ে যায়।

## ২. কাল — নিজের কনফিগে rehearsal

```bash
# ০) কন্ট্রোল টেবিল (APEX থেকে সব চালাতে)
sqlplus lis_user/password@//192.168.1.100:1521/ORCL @database/apex_install.sql

# ১) ডেটাবেস তৈরি
sqlplus lis_user/password@//<host>:1521/<service> @database/schema.sql

# ২) পাসওয়ার্ড env-এ রাখুন (config.yaml-এ নয়)
export LIS_DB_PASSWORD='...'          # Windows: setx LIS_DB_PASSWORD "..."

# ৩) সব চেক একসাথে — এটাই সবচেয়ে দরকারি কমান্ড
python tools/preflight.py
```

`preflight` যা যা দেখে: Python ভার্সন, প্যাকেজ, config-এর ভুল (পোর্ট ডুপ্লিকেট, ভুল protocol),
পোর্ট খোলা যায় কি না, Oracle-এ পৌঁছায় কি না, তিনটি টেবিল+ভিউ আছে কি না, লগ ফোল্ডার লেখা যায় কি না।
সব ✅ হলে:

```bash
python main.py            # --dry-run ছাড়া: এখন আসল Oracle-এ লিখবে
python tools/device_sim.py -s astm_cbc_normal -p 6661     # নকল মেশিন
```

তারপর APEX-এ `SELECT * FROM v_sample_results ORDER BY received_at DESC` — রো দেখা গেলে
মিডলওয়্যার + DB + APEX তিনটাই ঠিক। **এটাই ল্যাবে যাওয়ার আগে শেষ পরীক্ষা।**

---

## ৩. পরশু — ল্যাব ডে (ঘণ্টা ধরে)

### ৩.১ আগের রাতে

- [ ] `python tools/preflight.py` → ❌ নেই
- [ ] সার্ভিস হিসেবে ইনস্টল (`docs/PROJECT_GUIDE_BN.pdf` §৮) যাতে পিসি রিস্টার্ট হলেও চলে
- [ ] ল্যাপটপের **স্থির IP** ঠিক করা (DHCP-তে IP বদলালে মেশিন হারিয়ে যাবে)
- [ ] ফায়ারওয়ালে পোর্ট খোলা (6661/6662)
- [ ] `config.yaml`-এ মেশিনের `enabled: true/false` আর পোর্ট লিখে রাখা
- [ ] একটা খাতায় মেশিনের মডেল + কেবল টাইপ + বাউড রেট লিখে রাখা

### ৩.২ মেশিনে যা যা বসাতে হবে (এটাই আসল "config")

**সব মেশিনে একই মর্ম — নামগুলো শুধু আলাদা:**

| মেশিনের সেটিং | কী দিতে হবে | কেন |
|---|---|---|
| **Host / LIS IP** | মিডলওয়্যার চলা পিসির IP (যেমন `192.168.1.50`) | মেশিন এই ঠিকানায় ডেটা পাঠাবে |
| **Host Port** | `6661` (ASTM) / `6662` (HL7) — `config.yaml`-এর সাথে হুবহু | ভুল পোর্ট = `connection refused`, মেশিন আটকে যাবে |
| **Protocol** | ASTM E1394 (Sysmex/Roche) / HL7 v2 (Mindray) | ভুল হলে লগে `no results parsed` |
| **Transmission mode** | ASTM হলে **E1381** (কিছু মেশিনে "Basic/RAW") | হ্যান্ডশেক না মিললে কিছুই আসবে না |
| **Checksum** | **চালু** (আমরা যাচাই করি) | নষ্ট চেকসামে NAK → মেশিন রিপিট করবে |
| **Baud / Data / Parity / Stop** | `9600 / 7 / EVEN / 1` (RS-232 ASTM) · TCP হলে সাধারণত প্রযোজ্য নয় | ভুল parity = আজেবাজে অক্ষর |
| **Sample ID / Barcode** | মেশিনে স্ক্যান করা বারকোডই LIS-এর sample_id | না মিললে রেজাল্ট ভুল স্যাম্পলে বসবে |
| **Auto-send results** | চালু ("Send results automatically", "Auto transmit") | অটো না হলে অপারেটরকে "Send" চাপতে হবে |
| **Query mode / Host query** | **বন্ধ** রাখুন (প্রথম দিনে) | host query চাইলে মিডলওয়্যার ওয়ার্কলিস্ট দিতে হয় — সেটা পরে |
| **Units** | মেশিন যা পাঠায় সেটাই থাকুক, LIS-এ ম্যাপ করব `test_code_map`-এ | ভুল ইউনিট = ভুল রিপোর্ট |
| **Control/QC sample** | আলাদা prefix (`QC`, `CTRL`) দিয়ে চালান | আমরা ওগুলো রিপোর্টে ঢুকতে দিই না |

**কোন পর্দায় পাবেন (সাধারণ নাম, ব্র্যান্ডভেদে আলাদা):**
`Host Communication` · `LIS Connection` · `Communication → ASTM/HL7` · `Interface Setup` · `HIS/LIS` · `Online`
(মেশিনের ম্যানুয়ালের "Host Interface" অধ্যায়ে নামগুলো হুবহু লেখা থাকে — সেটাই দেখুন)

### ৩.৩ ল্যাবে প্রথম ৩০ মিনিট — ক্রম মেনে

1. **নেটওয়ার্ক**: ল্যাপটপ থেকে `ping <মেশিনের IP>`; মেশিন থেকে পিসিতে পৌঁছায় কি না দেখুন।
2. **পোর্ট শোনা হচ্ছে কি না**: `netstat -ano | findstr 6661` (LISTENING থাকতে হবে)।
3. **মেশিন থেকে এক কন্ট্রোল স্যাম্পল পাঠান** (QC বা একটা ফাঁকা স্যাম্পল)। লগ টেইল করুন:
   ```
   python main.py          → অন্য টার্মিনালে: Get-Content logs/lis_middleware.log -Wait
   ```
   - `connection from …` → নেটওয়ার্ক ঠিক ✅
   - `ASTM: parsed 4 result(s)` → ডেটা ঠিক ✅
   - কিছুই না → §৩.৫-এর টেবিল
4. **রিপোর্ট দেখুন**: APEX → `v_sample_results`। স্যাম্পল আইডি, রোগীর নাম, ভ্যালু মিলছে?
5. **আসল রোগীর স্যাম্পল** — একটা, তারপর মিলিয়ে দেখুন।
6. **ক্যাপচার রাখুন** (ভবিষ্যতের রিগ্রেশন টেস্টের জন্য):
   ```bash
   python tools/sniff_com.py --port COM4 --baud 9600 --parity EVEN --tap 127.0.0.1:6661 --out logs/capture.jsonl
   ```
   অথবা TCP হলে: `--listen 0.0.0.0:6663 --tap 127.0.0.1:6661` (মেশিনে পোর্ট 6663 দিন)।

### ৩.৪ ল্যাব ডে-তে সবচেয়ে বেশি ভুল হয় যেখানে

| লক্ষণ | ৯০% কারণ | সাথে সাথে করুন |
|---|---|---|
| মেশিন কিছুই পাঠায় না, "sending…" ঘুরছে | ACK পাচ্ছে না | লগে `ENQ` আছে? থাকলে ACK পাঠানো হচ্ছে কি দেখুন; না থাকলে IP/পোর্ট/ফায়ারওয়াল |
| লগে আজেবাজে অক্ষর | parity/baud ভুল | 9600 **7E1** → তারপর 9600 **8N1** |
| `no results parsed` | protocol ভুল (ASTM না HL7) | `config.yaml`-এ `protocol: AUTO` |
| মেশিন থেমে যায় মাঝপথে | ব্লকের ACK পায়নি | লগে `bad block → NAK`; কেবল/parity দেখুন |
| রেজাল্ট এসেছে কিন্তু ফাঁকা | ফিল্ড ম্যাপিং | `samples/`-এর সাথে মিলিয়ে ফিল্ড ইনডেক্স দেখুন |
| ভুল স্যাম্পলে রেজাল্ট | sample ID মেলেনি | মেশিনের বারকোড সেটিং বনাম APEX-এর আইডি |
| একই রেজাল্ট দুবার | মেশিন রিট্রান্সমিট করছে | স্বাভাবিক; dedupe কাজ করছে (`DUPLICATE`) |

### ৩.৫ কোনো কারণে আটকে গেলে (রোলব্যাক)

1. মেশিনের Host সেটিং-এ **"LIS/Host communication: Off"** — মেশিন আবার নিজের মতো কাজ করবে,
   রিপোর্ট মেশিনের প্রিন্টারেই যাবে। (মেশিন কখনো LIS-এর অভাবে আটকে থাকবে না — এটা আগেই বলে রাখুন।)
2. লগ + `lis_raw_messages` টেবিল সংরক্ষণ করুন (ক্যাপচারও)।
3. ঘরে ফিরে `replay_capture` / `device_sim` দিয়ে সমস্যাটা রিপ্রোডিউস করুন, ঠিক করুন, আবার যান।
   **ল্যাবে ডিবাগ করার দরকার নেই** — এটাই এই ২-ধাপ পদ্ধতির সুবিধা।

---

## ৪. এক নজরে কমান্ড

```bash
# ঘরে
python -m unittest discover -s tests -v
python tools/device_sim.py -s astm_cbc_normal -p 6661 --trace
python tools/preflight.py

# ল্যাবের আগে
sqlplus lis_user/password@//host:1521/service @database/schema.sql
export LIS_DB_PASSWORD='...'
python tools/preflight.py              # সব ✅
python main.py                         # সার্ভিস হিসেবে হলে: systemctl start lis-middleware

# ল্যাবে
python tools/sniff_com.py --listen 0.0.0.0:6663 --tap 127.0.0.1:6661   # ক্যাপচার (ঐচ্ছিক)
python tools/replay_capture.py logs/capture.jsonl --port 6661          # পরে রিপ্লে
```
