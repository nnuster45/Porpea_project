# Porpea — หาทำเลตลาดสำหรับตั้งร้าน

Pipeline ดึงรายชื่อตลาดทุกแบบ (ตลาดนัด, walking street, ตลาดเช้า/เย็น, โต้รุ่ง) ในจังหวัดที่กำหนด (ตั้งต้น: ชลบุรี)
แล้วให้คะแนนจาก **ความนิยมของตัวตลาด** + **สิ่งที่อยู่รอบตลาด** (ร้านสะดวกซื้อ, ห้าง, มหาลัย, หอพัก, โรงงาน ฯลฯ)
+ **เวลาเปิดที่ตรงกับลูกค้า** เพื่อกรอง candidate ก่อนลงไปดูหน้างานจริง

```
discover ──► enrich ──► surround ──► score ──► map
หาตลาด       รีวิว/เรตติ้ง   นับ POI รอบตลาด   จัดอันดับ    แผนที่ HTML
(Text Search) /เวลาเปิด    (Aggregate/OSM/   ตาม persona
              (Details)     ไฟล์ 7-11)
```

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
  (ใช้ใน persona `factory_worker`) — copy จาก `industrial_estates.example.csv` แล้วกรอก:
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
| `python -m sitefinder surround` | นับ POI รอบตลาด (Google + OSM), ขนาดพื้นที่อุตสาหกรรม, จำนวนคนงานนิคมฯ, 7-11 | `data/surroundings.csv` |
| `python -m sitefinder score --persona factory_worker` | จัดอันดับตาม persona | `data/ranked.csv` |
| `python -m sitefinder map` | แผนที่ interactive (คลิกหมุดดูคะแนน/เหตุผล/ลิงก์ Google Maps) | `data/map.html` |
| `python -m sitefinder all` | รันทั้งหมดต่อกัน | |

- ทุก response จาก API ถูก cache ใน `data/cache/` — รันซ้ำไม่เสีย quota ซ้ำ
- `--max-calls N` (default 4900) หยุดเมื่อ call ที่ไม่ได้มาจาก cache ครบ N ครั้งในการรันนั้น; รันใหม่จะทำต่อจากที่ค้างเพราะของเดิมอยู่ใน cache
- รัน `discover` ซ้ำได้ — ค่า `keep`/`note` ที่คุณแก้ไว้จะถูกเก็บไว้ ตลาดที่เจอใหม่จะมี `is_new=1`
- **อย่าลบแถว** ให้ตั้ง `keep=0` แทน (ถ้าลบ แล้วรัน discover ซ้ำ แถวนั้นจะกลับมา)

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
ใกล้หอพัก = ลูกค้าตอนเย็น/วันหยุด — persona `factory_worker` จึงให้น้ำหนักทั้งนิคมฯ และหอพัก
(หลายโรงงานมีรถรับส่งไปถึงย่านหอพักเลย ตลาดย่านหอพักรอบนิคมฯ จึงอาจดีกว่าตลาดติดรั้วโรงงาน)

## ไฟล์ข้อมูลภายนอก (`external_pois` ใน `config.yaml`)

CSV ต้องมีคอลัมน์ `lat`, `lng` — ดูตัวอย่างใน `data/external/*.example.csv`
- `category` (ถ้ามี) → แยก feature ต่อประเภท เช่น `seven_ปั๊ม_500`
- `weight: <คอลัมน์>` ใน config → รวมค่าคอลัมน์นั้นในรัศมีแทนการนับจุด (เช่น `workers` ของนิคมฯ)
- เพิ่มไฟล์อื่นได้ (CJ, Lotus's ฯลฯ) — ตั้ง `name` ใหม่ แล้วเอา `<name>_<รัศมี>` ไปใส่ใน persona

## ปรับการให้คะแนน

แก้ `scoring.personas` ใน `config.yaml` — key คือชื่อ feature (`<feature>_<รัศมี>` เช่น `conv_store_500`,
`industrial_ha_1500`, `estate_workers_3000`, `workplace_500`, หรือ `reviews`, `rating`, `open_evening`, `days_open`) ค่าคือน้ำหนัก (ติดลบได้ ถ้าอยากให้เป็นตัวลบคะแนน)

แต่ละ feature ถูกแปลงเป็น percentile (0–1) ก่อนถ่วงน้ำหนัก แล้วรวมเป็นคะแนน 0–100
คอลัมน์ `why` บอก 3 feature ที่ดันคะแนนตลาดนั้นมากที่สุด
`rating` ใช้ Bayesian average — ตลาดรีวิวน้อยจะถูกดึงเข้าหาค่าเฉลี่ย (ตั้งที่ `rating_prior_reviews`)

Persona ตั้งต้น: `general`, `factory_worker`, `student`, `office`, `tourist`

## ข้อควรรู้

- **Free cap แยกต่อ SKU ต่อเดือน (ต่อ billing account):**

  | ขั้น | SKU | ฟรี/เดือน | ใช้ |
  |---|---|---|---|
  | discover | Text Search Pro | 5,000 | tiles × keywords × ≤3 หน้า |
  | enrich | Place Details Enterprise | 1,000 | 1 ต่อตลาด (keep=1) |
  | surround | Places Aggregate (Pro) | 5,000 | ตลาด × 12 (รวมรัศมีทุก feature) → ฟรีถึง ~416 ตลาด |

  ส่วนเกินของ Aggregate คิด $10/1,000 call — ลด feature หรือรัศมีใน `google_types` ได้ถ้าตลาดเยอะ
  (school/hospital/transit/โรงงาน ดึงจาก OSM ฟรี ไม่กิน quota Google)
- ลองรัน `--max-calls 20` ก่อนรันเต็ม เพื่อเช็กว่า key/API เปิดถูกต้อง
- Google ไม่มีข้อมูล Popular Times ใน API — จำนวนรีวิวคือ proxy ที่ใกล้ที่สุด ต้องยืนยันด้วยการลงพื้นที่
- ข้อมูลจาก Google ใช้วิเคราะห์ภายใน ไม่ควรเผยแพร่ต่อ (เงื่อนไขของ Google Maps Platform) — `data/` ถูก gitignore ไว้แล้ว

## Tests

```bash
python -m pytest -q
```
