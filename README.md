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
| `python -m sitefinder surround` | นับ POI รอบตลาดตามรัศมีใน config + โรงงานจาก OSM + ไฟล์ภายนอก (7-11) | `data/surroundings.csv` |
| `python -m sitefinder score --persona factory_worker` | จัดอันดับตาม persona | `data/ranked.csv` |
| `python -m sitefinder map` | แผนที่ interactive (คลิกหมุดดูคะแนน/เหตุผล/ลิงก์ Google Maps) | `data/map.html` |
| `python -m sitefinder all` | รันทั้งหมดต่อกัน | |

- ทุก response จาก API ถูก cache ใน `data/cache/` — รันซ้ำไม่เสีย quota ซ้ำ
- `--max-calls N` (default 4900) หยุดเมื่อ call ที่ไม่ได้มาจาก cache ครบ N ครั้งในการรันนั้น; รันใหม่จะทำต่อจากที่ค้างเพราะของเดิมอยู่ใน cache
- รัน `discover` ซ้ำได้ — ค่า `keep`/`note` ที่คุณแก้ไว้จะถูกเก็บไว้ ตลาดที่เจอใหม่จะมี `is_new=1`
- **อย่าลบแถว** ให้ตั้ง `keep=0` แทน (ถ้าลบ แล้วรัน discover ซ้ำ แถวนั้นจะกลับมา)

## ข้อมูลสาขา 7-11 (หรือ POI อื่น) จากแหล่งภายนอก

วางไฟล์ CSV ที่ `data/external/7eleven.csv` ต้องมีคอลัมน์ `lat`, `lng` (ดูตัวอย่าง `data/external/7eleven.example.csv`)
ถ้ามีคอลัมน์ `category` (เช่น ปั๊ม / โรงพยาบาล / ชุมชน) จะได้ feature แยกต่อประเภทด้วย เช่น `seven_ปั๊ม_500`
เพิ่มไฟล์อื่น (CJ, Lotus's ฯลฯ) ได้ใน `external_pois` ของ `config.yaml`

## ปรับการให้คะแนน

แก้ `scoring.personas` ใน `config.yaml` — key คือชื่อ feature (`<feature>_<รัศมี>` เช่น `conv_store_500`,
`industrial_1500`, `seven_500`, หรือ `reviews`, `rating`, `open_evening`, `days_open`) ค่าคือน้ำหนัก (ติดลบได้ ถ้าอยากให้เป็นตัวลบคะแนน)

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
  | surround | Places Aggregate (Pro) | 5,000 | ตลาด × `google_types` (6) × รัศมี (2) → ฟรีถึง ~416 ตลาด |

  ส่วนเกินของ Aggregate คิด $10/1,000 call — ลด `google_types` หรือ `radii_m` ได้ถ้าตลาดเยอะ
  (school/hospital/transit/industrial ดึงจาก OSM ฟรี ไม่กิน quota Google)
- ลองรัน `--max-calls 20` ก่อนรันเต็ม เพื่อเช็กว่า key/API เปิดถูกต้อง
- Google ไม่มีข้อมูล Popular Times ใน API — จำนวนรีวิวคือ proxy ที่ใกล้ที่สุด ต้องยืนยันด้วยการลงพื้นที่
- ข้อมูลจาก Google ใช้วิเคราะห์ภายใน ไม่ควรเผยแพร่ต่อ (เงื่อนไขของ Google Maps Platform) — `data/` ถูก gitignore ไว้แล้ว

## Tests

```bash
python -m pytest -q
```
