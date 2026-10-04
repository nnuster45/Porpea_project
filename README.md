# Porpea — หาทำเลตลาดสำหรับตั้งร้าน

Pipeline ดึงรายชื่อตลาดทุกแบบ (ตลาดนัด, walking street, ตลาดเช้า/เย็น, โต้รุ่ง) ในจังหวัดที่กำหนด (ตั้งต้น: ชลบุรี)
แล้วให้คะแนนจาก **ความนิยมของตัวตลาด** + **สิ่งที่อยู่รอบตลาด** (ร้านสะดวกซื้อ, ห้าง, มหาลัย, หอพัก, โรงงาน ฯลฯ)
+ **เวลาเปิดที่ตรงกับลูกค้า** เพื่อกรอง candidate ก่อนลงไปดูหน้างานจริง

```
discover ──► enrich ──► surround ──► dashboard
หาตลาด       รีวิว/เรตติ้ง   นับ POI รอบตลาด   ปรับน้ำหนัก/กรอง/ดูแผนที่ แบบ live
(Text Search) /เวลาเปิด    (Aggregate/OSM/   (score / map = เวอร์ชัน CSV/แผนที่อย่างเดียว)
              (Details)     ไฟล์ 7-11/นิคมฯ)
```

## Dashboard (หน้าหลักที่ใช้ดูผล)

`python -m sitefinder dashboard` → ดับเบิลคลิกเปิด `data/dashboard.html` (ไฟล์เดียว ไม่ต้องรัน server)

- **① ให้ความสำคัญกับอะไร** — slider 4 หมวด (ดู "วิธีให้คะแนน") คะแนน อันดับ สีหมุดบนแผนที่ เปลี่ยนทันที
  - **ตั้งค่าขั้นสูง**: ย้ายตัววัดเข้า/ออกหมวด และปรับน้ำหนักภายในหมวด
- **② กรอง** ตามย่าน (ย่านคึกคัก / ที่พัก / ที่ทำงาน), ประเภทตลาด, อำเภอ, รีวิวขั้นต่ำ, **ช่วงเวลาที่เปิด**, เฉพาะที่ติดดาว
- ตารางแสดงคะแนนรวม + คะแนนแต่ละหมวด (คลิกหัวตารางเพื่อเรียง)
- คลิกตลาด → แผงรายละเอียด: **คะแนนมาจากไหน** (แต่ละหมวดได้กี่คะแนน และตัววัดข้างใน เทียบกับตลาดอื่น),
  เวลาเปิด, ลิงก์ Google Maps, **ติดดาว ★** และ **บันทึกตอนลงพื้นที่**
- **⬇ CSV** ดาวน์โหลดรายการที่กรองอยู่ (รวมคะแนนหมวด ดาว และบันทึก) เปิดใน Excel ได้
- **บันทึกชุดนี้** / **ส่งออกไป config** — เก็บชุดน้ำหนักไว้เรียกใช้ หรือเอาไปใส่ `config.yaml` ให้คำสั่ง `score` ใช้ชุดเดียวกัน
- ดาว บันทึก และน้ำหนักที่ปรับ เก็บในเบราว์เซอร์ที่เปิด (เปิดเครื่อง/เบราว์เซอร์อื่นจะไม่เห็น — ใช้ CSV แทน)
- ต้องต่อเน็ตเพื่อโหลดแผนที่และฟอนต์ — ถ้าออฟไลน์ ตารางและคะแนนยังใช้ได้
- รัน `dashboard` ใหม่หลังดึงข้อมูลใหม่ (discover / enrich / surround) — ดาวและบันทึกเดิมยังอยู่

## รันบน GitHub Actions + Cloudflare Pages

ไม่ต้องรันบนเครื่อง: GitHub รัน pipeline (ใช้ API key จาก GitHub Secrets) แล้ว deploy dashboard ขึ้น Cloudflare Pages
**Cloudflare ไม่ต้องรู้ API key ของ Google** — dashboard เป็นไฟล์ HTML ที่มีข้อมูลฝังอยู่แล้ว ไม่เรียก Google เลย

### ตั้งค่าครั้งเดียว

1. **Merge branch นี้เข้า branch หลัก** (main) — ปุ่ม Run workflow จะขึ้นก็ต่อเมื่อไฟล์ workflow อยู่ใน branch หลัก
2. **Cloudflare**
   - Account ID: หน้า Workers & Pages (แถบขวา) หรือ URL ของ dashboard
   - API token: My Profile → API Tokens → Create Token → Custom → สิทธิ์ **Account · Cloudflare Pages · Edit**
3. **GitHub** → repo → Settings → Secrets and variables → Actions
   - Secrets: `GOOGLE_MAPS_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`
   - Variables (ไม่บังคับ): `CLOUDFLARE_PAGES_PROJECT` = ชื่อโปรเจกต์ Pages (ตั้งต้น `porpea-markets` — สร้างให้อัตโนมัติ)
4. **Google key**: จำกัดให้ใช้ได้แค่ Places API (New) + Places Aggregate API และตั้ง quota cap รายวัน
   (จำกัดด้วย IP ไม่ได้ เพราะเครื่องของ GitHub เปลี่ยน IP ทุกครั้ง)
5. **ล็อกหน้า dashboard**: Cloudflare Zero Trust → Access → Applications → Add (Self-hosted)
   ใส่โดเมน `<project>.pages.dev` แล้วตั้ง policy ให้เข้าได้เฉพาะอีเมลของคุณ (ฟรีถึง 50 คน)
   — ข้อมูลจาก Google ไม่ควรเปิดสาธารณะ (workflow ใส่ `noindex` ให้แล้ว แต่ไม่ได้กันคนที่มีลิงก์)
6. ไฟล์ที่คุณเตรียมเอง (`data/external/industrial_estates.csv`, `7eleven.csv`) — commit เข้า repo ได้เลย

### ใช้งาน: Actions → Market pipeline → Run workflow

| stages | ทำอะไร | เรียก Google |
|---|---|---|
| `discover` | หาตลาด → โหลด `markets.csv` จาก artifact ของ run ไปตรวจ | Text Search |
| `refresh` | enrich → surround → dashboard → deploy (ใช้รายชื่อตลาดเดิม) | Details + Aggregate |
| `all` | ทั้งหมดต่อกัน | ทั้งหมด |
| `dashboard` | สร้าง dashboard ใหม่จากข้อมูลเดิม → deploy (เช่น หลังแก้ `review/keep.csv` หรือ config) | ไม่เรียก |

ลำดับที่แนะนำ:
1. รัน `discover` → เปิด artifact **results** → ดู `markets.csv` → ใส่ `place_id` ที่ไม่ใช่ตลาดใน [`review/keep.csv`](review/README.md) → commit
2. ดู step **Estimate quota** ใน log ของ run นั้นว่าอยู่ใน free cap → รัน `refresh` → เปิด `https://<project>.pages.dev`
3. ใช้ dashboard ไปเรื่อย ๆ เจอที่ไม่ใช่ตลาดกด **🚫 ไม่ใช่ตลาด** → **⬇ keep.csv** → แทนที่ `review/keep.csv` → commit → รัน `dashboard`

ข้อควรรู้
- ข้อมูล OSM (ป้ายรถ, โรงเรียน, โรงพยาบาล, พื้นที่โรงงาน) มาจากไฟล์ทั้งประเทศของ Geofabrik
  (ดาวน์โหลดสัปดาห์ละครั้ง) แทน Overpass API ซึ่งจำกัดความถี่ IP ของ GitHub จนรันไม่จบ
  — เปลี่ยนประเทศ/ภูมิภาคได้ด้วย Variable `OSM_PBF_URL`
- ผลจาก API เก็บใน **Actions cache** ของ repo — รันซ้ำไม่เสีย quota ซ้ำ (`use_cache` = true)
  อยากได้ข้อมูลใหม่จริง ๆ (เช่น ทุก 2–3 เดือน) ให้ปิด `use_cache` — จะเสีย quota ตามจริง
- cache ที่ไม่ถูกใช้ **7 วัน** GitHub จะลบทิ้ง → ต้องรัน `discover`/`all` ใหม่ (ค่า keep ใน `review/keep.csv` ยังอยู่)
- ชน `max_calls` กลางทาง? รันซ้ำได้ — ทำต่อจากที่ค้าง (บันทึก cache แม้ run ล้ม)
- ดาว/บันทึก/น้ำหนักใน dashboard เก็บในเบราว์เซอร์ — deploy ใหม่ไม่หาย (ถ้าโดเมนเดิม)

## สิ่งที่ต้องเตรียม (checklist)

**จำเป็น**
- [ ] Google Cloud project ที่ผูก billing account แล้ว (ต้องผูกบัตรถึงจะได้ free cap)
- [ ] เปิด API: **Places API (New)** และ **Places Aggregate API**
- [ ] สร้าง API key → จำกัด key ให้ใช้ได้แค่ 2 API นี้ (Credentials → API restrictions)
- [ ] ตั้ง quota cap รายวันใน Console (APIs & Services → Quotas) กันพลาด
- [ ] ใส่ key ใน `.env`
- [ ] เครื่องที่รันต้องต่อเน็ตไปที่ `places.googleapis.com`, `areainsights.googleapis.com` และ `overpass-api.de` ได้

**ไม่บังคับ แต่ช่วยให้แม่นขึ้น** (ไม่มีไฟล์ = ข้าม feature นั้นไปเฉย ๆ)
- [ ] `data/external/industrial_estates.csv` — นิคมฯ/สวนอุตสาหกรรมในจังหวัด + จำนวนคนงาน
  (ใช้ในหมวด "คนทำงาน") — copy จาก `industrial_estates.example.csv` แล้วกรอก:
  - `lat`, `lng`: เปิด Google Maps → คลิกขวากลางนิคมฯ → คลิกพิกัดเพื่อ copy
  - `workers`: จำนวนคนงานโดยประมาณ (หาได้จากเว็บนิคมฯ, ข่าว, รายงาน กนอ./BOI) — ตัวเลขหลักหมื่นหยาบ ๆ ก็พอ
    เพราะคะแนนดูจากลำดับเทียบกัน ไม่ใช่ตัวเลขจริง
  - รายชื่อในไฟล์ตัวอย่างยังไม่ครบ — เทียบกับรายชื่อนิคมฯ ในชลบุรีจากเว็บ กนอ. (ieat.go.th) แล้วเพิ่มได้
- [ ] `data/external/7eleven.csv` — สาขา 7-11 (`lat`, `lng`, ถ้ามี `category` ยิ่งดี)

## เริ่มใช้งาน

```bash
pip install -r requirements.txt
cp .env.example .env          # แล้วใส่ GOOGLE_MAPS_API_KEY
python -m sitefinder estimate # ดูจำนวน call ก่อนใช้ quota จริง
```

ใน Google Cloud Console ต้องเปิด API:
- **Places API (New)** — ใช้ใน discover + enrich
- **Places Aggregate API** — ใช้ใน surround (ถ้าไม่อยากเปิด ตั้ง `surroundings.source: osm` ใน `config.yaml` ใช้ OpenStreetMap ฟรีแทน)

แนะนำตั้ง quota cap ต่อวันใน Console (APIs & Services → Quotas) กันพลาด

## รันทีละขั้น

| คำสั่ง | ทำอะไร | ผลลัพธ์ |
|---|---|---|
| `python -m sitefinder discover` | ค้นหาตลาดด้วยคำค้นใน `config.yaml` ทั่ว grid ของจังหวัด, ตัดผลนอกจังหวัด/ไม่ใช่ตลาด/ปิดถาวร | `data/markets.csv` |
| **คัดกรอง** `data/markets.csv` | ตั้ง `keep=0` ให้แถวที่ไม่ใช่ตลาดจริง (ระบบเดาให้แล้วบางส่วน ดูเหตุผลในคอลัมน์ `note`) — ขั้นต่อจากนี้ใช้เฉพาะ `keep=1` | |
| `python -m sitefinder estimate` | เช็กว่าจำนวนตลาดที่เหลืออยู่ใน free cap ไหม | |
| `python -m sitefinder enrich` | ดึงเรตติ้ง, จำนวนรีวิว, เวลาเปิด → แปลงเป็น `open_morning/evening/night`, `days_open` | `data/market_details.csv` |
| *(ไม่บังคับ)* `python -m sitefinder osm-extract --pbf thailand-latest.osm.pbf` | ใช้ไฟล์ OSM ทั้งประเทศ ([Geofabrik](https://download.geofabrik.de/asia/thailand.html)) แทน Overpass API — เร็วกว่า ไม่ติด rate limit | `data/osm/features.json` |
| `python -m sitefinder surround` | นับ POI รอบตลาด (Google + OSM), ขนาดพื้นที่อุตสาหกรรม, จำนวนคนงานนิคมฯ, 7-11 | `data/surroundings.csv` |
| `python -m sitefinder score` | จัดอันดับตาม `scoring.pillars` (คะแนนรวม + คะแนนหมวด + ป้ายย่าน) | `data/ranked.csv` |
| `python -m sitefinder map` | แผนที่อย่างเดียว (คลิกหมุดดูคะแนน/เหตุผล/ลิงก์ Google Maps) | `data/map.html` |
| `python -m sitefinder dashboard` | **dashboard ปรับน้ำหนัก/กรอง/ติดดาว/จดบันทึก แบบ live** | `data/dashboard.html` |
| `python -m sitefinder all` | รันทั้งหมดต่อกัน | |

- ทุก response จาก API ถูก cache ใน `data/cache/` — รันซ้ำไม่เสีย quota ซ้ำ
- `--max-calls N` (default 4900) หยุดเมื่อ call ที่ไม่ได้มาจาก cache ครบ N ครั้งในการรันนั้น; รันใหม่จะทำต่อจากที่ค้างเพราะของเดิมอยู่ใน cache
- รัน `discover` ซ้ำได้ — ค่า `keep`/`note` ที่คุณแก้ไว้จะถูกเก็บไว้ ตลาดที่เจอใหม่จะมี `is_new=1`
- **อย่าลบแถว** ให้ตั้ง `keep=0` แทน (ถ้าลบ แล้วรัน discover ซ้ำ แถวนั้นจะกลับมา)
- หรือใส่ใน `review/keep.csv` (commit ได้ ใช้ได้ทั้งบนเครื่องและบน GitHub Actions — ชนะค่าใน markets.csv)

## Feature รอบตลาด

| feature | ที่มา | วัดอะไร |
|---|---|---|
| `conv_store`, `supermarket`, `mall`, `university`, `apartment` (500/1500 ม.) | Google Aggregate | จำนวน |
| `lodging_1500` | Google Aggregate | จำนวนที่พัก/โรงแรม |
| `workplace_500` | Google Aggregate | ออฟฟิศ + หน่วยราชการ (ศาลากลาง, อบต./เทศบาล, ศาล) ในระยะเดินพักเที่ยง |
| `school`, `hospital`, `transit` (500/1500 ม.) | OSM (ฟรี) | จำนวน |
| `industrial_ha_500`, `industrial_ha_1500` | OSM (ฟรี) | **ขนาดพื้นที่อุตสาหกรรม (เฮกตาร์)** ในรัศมี — โรงงานใหญ่นับมากกว่าโรงงานเล็ก |
| `estate_workers_3000` | ไฟล์นิคมฯ ของคุณ | **จำนวนคนงานรวม** ของนิคมฯ ในรัศมี 3 กม. |
| `seven_500`, `seven_1500` (+ แยก category) | ไฟล์ 7-11 ของคุณ | จำนวนสาขา |

**ที่ทำงาน vs ที่พัก วัดคนละช่วงเวลา:** ใกล้ที่ทำงาน = ลูกค้าช่วงพักเที่ยง/หลังเลิกงานก่อนกลับ,
ใกล้หอพัก = ลูกค้าตอนเย็น/วันหยุด — จึงแยกเป็นคนละหมวด ("คนพักอาศัย" กับ "คนทำงาน")
(หลายโรงงานมีรถรับส่งไปถึงย่านหอพักเลย ตลาดย่านหอพักรอบนิคมฯ จึงอาจดีกว่าตลาดติดรั้วโรงงาน)

## ไฟล์ข้อมูลภายนอก (`external_pois` ใน `config.yaml`)

CSV ต้องมีคอลัมน์ `lat`, `lng` — ดูตัวอย่างใน `data/external/*.example.csv`
- `category` (ถ้ามี) → แยก feature ต่อประเภท เช่น `seven_ปั๊ม_500`
- `weight: <คอลัมน์>` ใน config → รวมค่าคอลัมน์นั้นในรัศมีแทนการนับจุด (เช่น `workers` ของนิคมฯ)
- เพิ่มไฟล์อื่นได้ (CJ, Lotus's ฯลฯ) — ตั้ง `name` ใหม่ แล้วเอา `<name>_<รัศมี>` ไปใส่ในหมวดใน `scoring.pillars`

## วิธีให้คะแนน

ยึดตลาดเป็นหลัก → ดูสิ่งรอบข้าง → รวมตามน้ำหนักที่คุณกำหนด → จัดอันดับ (weighted scoring)
ไม่ได้เดาว่าลูกค้าเป็นใคร — ตัววัดถูกจัดเป็น 4 หมวด (`scoring.pillars` ใน `config.yaml`):

| หมวด | ตัววัดตั้งต้น | น้ำหนักหมวด |
|---|---|---|
| ตลาดนี้มีคนมาจริงไหม | จำนวนรีวิว (2), เรตติ้ง (1) | **2** — หลักฐานตรงที่สุด |
| ย่านนี้คึกคักไหม | ร้านสะดวกซื้อ ≤500 ม. (2), ซูเปอร์ฯ, ห้าง, ป้ายรถ | 1 |
| คนพักอาศัยแถวนี้เยอะไหม | หอพัก (2), โรงแรม, มหาวิทยาลัย | 1 |
| คนทำงานแถวนี้เยอะไหม | ออฟฟิศ/ราชการ, พื้นที่โรงงาน, คนงานนิคมฯ | 1 |

1. แต่ละตัววัด → percentile เทียบกับตลาดอื่น (0–1) — ไม่มีข้อมูล = กลาง ๆ (0.5)
2. ในหมวด: เฉลี่ยตามน้ำหนักตัววัด → **คะแนนหมวด 0–100**
3. รวมหมวดตามน้ำหนักหมวด → **คะแนนรวม 0–100**

- จัดเป็นหมวดเพื่อ **กันนับซ้ำ** — ร้านสะดวกซื้อ 3 ตัววัดในหมวดเดียว ไม่ได้น้ำหนักมากกว่าหมวดอื่น
  (อย่าใส่ `conv_store_500` กับ `seven_500` ในคะแนนพร้อมกัน — 7-11 ก็คือร้านสะดวกซื้อ)
- **เวลาเปิดไม่อยู่ในคะแนน** — ใช้เป็นตัวกรองตามช่วงที่คุณจะไปขาย
- **ป้ายย่าน** (`profile`): ตลาดที่อยู่ top 30% ของหมวดไหน ได้ป้ายหมวดนั้น เช่น "ย่านที่พัก" (`profile_top_pct`)
  — อ่านจากข้อมูลหลังคำนวณ ไม่ใช่สมมติฐานล่วงหน้า
- `rating` ใช้ Bayesian average — ตลาดรีวิวน้อยจะถูกดึงเข้าหาค่าเฉลี่ย (`rating_prior_reviews`)
- น้ำหนักตั้งต้นยังเป็นค่าเดา — ลงพื้นที่ top 10–15 แล้วปรับน้ำหนักให้อันดับตรงกับที่เห็นจริง
- คะแนนเป็นการ **เทียบกันเอง** ในรายการ ใช้คัดกรองว่าควรไปดูที่ไหนก่อน ไม่ใช่ทำนายยอดขาย

## ข้อควรรู้

- **Free cap แยกต่อ SKU ต่อเดือน (ต่อ billing account):**

  | ขั้น | SKU | ฟรี/เดือน | ใช้ |
  |---|---|---|---|
  | discover | Text Search Pro | 5,000 | tiles × keywords × ≤3 หน้า |
  | enrich | Place Details Enterprise | 1,000 | 1 ต่อตลาด (keep=1) |
  | surround | Places Aggregate (Pro) | 5,000 | ตลาด × 7 (รวมรัศมีทุก feature ใน `google_types`) → ฟรีถึง ~714 ตลาด |

  ส่วนเกินของ Aggregate คิด $10/1,000 call — ลด feature หรือรัศมีใน `google_types` ได้ถ้าตลาดเยอะ
  (school/hospital/transit/โรงงาน ดึงจาก OSM ฟรี ไม่กิน quota Google)
- ลองรัน `--max-calls 20` ก่อนรันเต็ม เพื่อเช็กว่า key/API เปิดถูกต้อง
- Google ไม่มีข้อมูล Popular Times ใน API — จำนวนรีวิวคือ proxy ที่ใกล้ที่สุด ต้องยืนยันด้วยการลงพื้นที่
- ข้อมูลจาก Google ใช้วิเคราะห์ภายใน ไม่ควรเผยแพร่ต่อ (เงื่อนไขของ Google Maps Platform) — `data/` ถูก gitignore ไว้แล้ว

## Tests

```bash
python -m pytest -q
```
