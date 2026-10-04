# review/keep.csv

ระบุตลาดที่ **ไม่ใช่ตลาดจริง** (หรือที่อยากเอากลับมา) — ใช้ทั้งตอนรันบนเครื่องและบน GitHub Actions

```csv
place_id,keep,note
ChIJxxxxxxxxxxxxxxxx,0,ร้านขายของในตลาด ไม่ใช่ตัวตลาด
ChIJyyyyyyyyyyyyyyyy,1,ระบบเดาผิด เป็นตลาดจริง
```

- `keep=0` = ตัดออก (ไม่เสีย quota ขั้น enrich/surround และไม่ขึ้นใน dashboard), `keep=1` = เอากลับมา
- ค่าในไฟล์นี้ชนะค่าใน `data/markets.csv` เสมอ
- หา `place_id` ได้จาก `markets.csv` (artifact ของ workflow) หรือกด **ไม่ใช่ตลาด** ใน dashboard แล้ว **⬇ keep.csv**
- เก็บแค่ place ID ของ Google (เก็บถาวรได้ตามเงื่อนไข Google) — อย่าใส่ชื่อ/ที่อยู่จาก Google ลงไฟล์นี้
