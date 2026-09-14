---
state: READY_FOR_CLI
assigned_agent: DEEPSEEK_PRO
last_update: 2026-09-06T23:00:00
ticket_id: "TICKET-0001"
---
# SKYLIZE PROMPTS PIPELINE

# ❓ KOD AJANININ SORUSU
Konu: Veritabanı ve Hafıza Mimarisi
Soru: Skylize Core için Postgres RLS mimarisini mi yoksa Qdrant vector memory katmanını mı öncelikli bağlamalıyız?

# 💡 CEVAP
Once Postgres RLS (tenant isolation), sonra Qdrant.

Gerekce:
- Skylize governance-first, multi-tenant bir platform (CLAUDE.md: ADR'ler, RLS testleri "money/tenancy/RLS" iddialarinin kosulu olarak tanimlanmis). Tenant izolasyonu, uzerine her seyin insa edildigi guven sinirini olusturur.
- Vector memory (Qdrant) bir ozellik/performans katmanidir; RLS ise bir guvenlik sinirdir. Guvenlik sinirini sonradan eklemek, once yazilmis olan memory/vector kayitlarinin tenant bazinda izole olmadigi bir pencere yaratir - geriye donuk temizlik ve olasi veri sizintisi riski dogurur.
- RLS once kurulursa, Qdrant katmani entegre edilirken her koleksiyon/sorgu zaten net bir tenant_id sozlesmesine (skylize_app rolu, non-superuser erisim) oturur; tersi sirada Qdrant sema kararlari RLS kisitlarini gozetmeden alinip sonradan yeniden isle riski var.
- Pratik sira: (1) Postgres RLS + skylize_app role + entegrasyon testleri yesil, (2) Qdrant vector memory, semasinda tenant_id/RLS-esdeger izolasyon sartiyla, ayni tenant sinirina baglanarak eklenir.

Sonuc: RLS mimarisini once bagla, Qdrant'i tenant izolasyon sozlesmesi uzerine ikinci adimda kur.
