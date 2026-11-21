# CFO Test - demo runbook (yerel, tekrarlanabilir)

Run: `sky-demokit-20261121` - taban: `origin/main` `78458d9f2e9e6cc6ffb68037075310381af5c96d`.
Kaynak: `scripts/demo/cfo_test_local.py` (bu branch). Her iddia ya bir komut ciktisi
ya da `file:line`. Kodda dogrulanamayan sey **UNVERIFIED** diye isaretli.

> Bu demo **yerelde** kosar: yerel Postgres + Redis (Docker), yerel uvicorn,
> **demo LLM** (`DemoLLMAdapter`). Hosted backend yok, gercek LLM yok, gercek
> Stripe / Google / Slack cagrisi yok. Anlatirken bunu saklama - bkz.
> [honest_claims.md](honest_claims.md).

## 0. Hazirlik (kayittan 10 dk once)

| Gereken | Kontrol |
|---|---|
| Docker Desktop calisiyor | `docker info --format '{{.ServerVersion}}'` |
| `py -3.12` + repo bagimliliklari (`cryptography`, `alembic`, `uvicorn`, `asyncpg`) | `py -3.12 -c "import fastapi,uvicorn,asyncpg,alembic,cryptography"` |
| Konsol UI de gosterilecekse: `website/node_modules` | `cd website; npm ci` (bir kerelik) |
| Imajlar yerelde (`postgres:16-alpine`, `redis:7-alpine`) | ilk `compose up` indirir (Docker Hub) - **kayittan once bir kez kos** |
| Portlar bos: 55432 (pg), 56379 (redis), 58000 (api), 53000 (console) | script `--teardown` sonunda dinleyen kalmadigini dogrular |

Komutlar (repo kokunden):

```powershell
py -3.12 scripts/demo/cfo_test_local.py                 # 20 adim, ~19 sn, API'yi durdurur
py -3.12 scripts/demo/cfo_test_local.py --console       # + website dev server + BFF probelari, her sey acik kalir
py -3.12 scripts/demo/cfo_test_local.py --teardown      # her seyi siler ve dogrular
```

Script her kosuda once teardown yapar (temiz baslangic), anahtarlari (governance
signing key, Fernet key, JWT secret) **bellekte** uretir, yalnizca uvicorn
cocugunun ortamina verir; dosyaya yazmaz, ekrana basmaz.

## 1. Anlatim - 6 adim

Her adimin kaniti: `scripts/demo/cfo_test_local.py` ciktisindaki adim kodu (S#),
ilgili kod ve test. Adim kodlari script ciktisinda `[PASS] S#` olarak gorunur.

### Adim 1 - Kayit: organizasyon + owner (S1, S2)

- **Soyle:** "Self-serve kayit yeni bir organizasyon ve onun owner'ini yaratir; sonra giris
  yapip kisa omurlu bir access JWT alirim."
- **Kanit:** `POST /api/v1/auth/register` -> `201 roles=['owner']`, `POST /auth/login` -> `200`
  (S1, S2). Kod: `src/skylize/edge/routes/auth.py:94-148` (register), `:151-168` (login).
  Dev auth kapali: `SKYLIZE_DEV_AUTH=false`, postgres backend'de zaten acilamaz
  (`src/skylize/config.py:390-403`).
- **DIKKAT - bilinen defect:** `register` tek basina patlar (500, `users_org_id_fkey`),
  cunku `tenants` satiri olusmaz (`docs/08_operations/DAY1_PROVISIONING.md:262`).
  Script, tenant satirini SQL ile seed eder (S0.3) - ayni workaround
  `tests/integration/test_registration_org_claim_pg.py:67-73`. **Canlida "kayit ol
  butonu calisiyor" deme**; "kayit tenant provision edildikten sonra calisiyor" de.

### Adim 2 - Governed execute: karar kapisi islemi insana erteler (S3)

- **Soyle:** "`cfo_agent` bir butce ozeti uretir. Ajan hicbir seyi kendi basina calistiramaz:
  karar kapisi istegi degerlendirir ve burada bir insan onayi ister."
- **Kanit:** `POST /api/v1/agents/execute` -> `202 status=deferred_to_human`, `hitl_id` (S3).
  Neden: `cfo_agent` kontrati `human_in_loop_triggers=[SPEND_OVER_CEILING,
  LOW_CONFIDENCE_IRREVERSIBLE]` (`src/skylize/contracts/mvp/finance.py:183`); evaluator bunlari
  senkron yolda onurlandiramaz, fail-closed erteler (`src/skylize/app/decision_engine/evaluator.py:198-276`).
  Gate sadece **governed org**'larda aktif: script `SKYLIZE_DECISION_ENGINE_ORG_IDS=["demo-cfo"]`
  verir (`src/skylize/bootstrap.py:955`). Ayni davranisin test kaniti:
  `tests/integration/test_cfo_deliverable_e2e.py::test_cfo_agent_defers_then_approves_to_a_deliverable_with_recomputed_totals`.
- **Onemli:** LLM henuz cagrilmadi, deliverable yok. Sadece karar + kuyruk satiri var.

### Adim 3 - HITL: insan onaylar, is calisir, deliverable olusur (S4, S5, S6)

- **Soyle:** "Bekleyen is insan kuyrugunda. Onayladigimda bekleyen istek yeniden calisir ve cikti uretilir."
- **Kanit:** `GET /api/v1/hitl` -> satir `pending`, `agent_id=cfo_agent`,
  `trigger_reason="spend_over_ceiling, low_confidence_irreversible"` (S4).
  `POST /api/v1/hitl/{id}/approve` -> `200 approved` + `deliverable_id` (S5).
  Kod: `src/skylize/edge/routes/hitl.py:99-117` (liste), `:120-182` (approve).
- **Deliverable (S6):** `**Total:** $100,000.00` ve `paid_media is 60% of total spend ... exceeds the
  40% concentration threshold`. Toplam ve flag'ler **modelin degil Python'un hesabi**: model
  ciktisi dogrulandiktan sonra `total`/`flags` ezilir
  (`src/skylize/app/agents/execution.py:510-513`, `_compute_budget_summary` `:1491`).
  Testle kanit: `test_cfo_deliverable_e2e.py` yanlis toplam (7.77) ve uydurma flag donen
  saglayiciyla kosar, kalici deliverable'da dogru toplam/flag'i arar.
- **DEMO etiketi:** metin alanlari `[DEMO] ...` ile baslar (`src/skylize/adapters/llm/demo_adapter.py:96-101`).
  Bu, gercek LLM ciktisi **degildir**; hesap (toplam, flag) gercek mantiktir, anlatim metni degil.

### Adim 4 - Audit: governance token'i (S7)

- **Soyle:** "Her yönetilen eylem imzali bir governance token'iyla yapilir ve audit'e o token kimligiyle yazilir."
- **Kanit:** `GET /api/v1/audit?limit=200` -> bir `agent.executed` satirinda `governance_token_id` dolu;
  ayrica `governance.token_issued`, `decision.deferred_to_human`, `hitl.approved`, `tool.invoked`
  (S7 ciktisi: `entries=5 with_token=3 agent.executed_with_token=1`).
  Kod: `src/skylize/edge/routes/audit.py:44-73`.
- **Sinir:** audit'te **insan aktor** kolonu yok; `source_agent_id` agent kimligidir
  (`audit.py:30`), hash'ler icerik hash'idir, imza degil
  (`website/src/app/api/console/audit/route.ts` basindaki not). "Kim yapti"yi hash'lerle iddia etme.

### Adim 5 - Kill switch (S9b-S13)

- **Soyle:** "Owner tenant'i durdurur: hem senkron ajan calismasi hem bekleyen onayin icrasi reddedilir.
  Kaldirinca bekleyen is kaybolmamistir."
- **Kanit (hepsi script'te PASS):**
  - S9b kill switch kapaliyken senkron ajan (`seo_keyword_agent`) -> `201` deliverable.
  - S10 `POST /api/v1/kill-switch/engage` (tenant kapsami, **API key** ile) -> `200 engaged`.
  - S11b ayni senkron istek -> `403 code=governance_denied ... tenant kill switch engaged`.
  - S11 bekleyen HITL onayi -> `502 execution failed: tenant kill switch engaged`.
  - S12 `disengage` -> `200`. S13 ayni HITL satiri tekrar approve -> `200` (satir pending'e geri birakilmisti).
  Kod: `src/skylize/edge/routes/kill_switch.py:26-50` (owner-only), `src/skylize/app/governance/snapshot.py:74-82`
  (red nedenleri), `src/skylize/edge/routes/hitl.py:171-175` (502), `src/skylize/edge/routes/agents.py:130` (403).
- **API key neden?** Kill-switch route'u `require_role("owner")` ile `get_context` kullanir; Bearer JWT'yi kabul
  etmez (`src/skylize/edge/deps.py:81-94,187-203`). Script owner-scope'lu bir API key uretir (S9).
- **Nüans (dogru anlat):** kill switch acikken **yeni bir istek yine de `202 deferred`** olabilir
  (probe: `hook_generator_agent` -> 202). Kill switch, token uretim/icra noktasinda keser; erteleme
  karari bundan once verilir. "Kill switch her istegi aninda reddeder" **deme**; "hicbir ajan eylemi
  yurutulemez" de.

### Adim 6 (opsiyonel) - Konsol UI

`--console` ile website dev server yerel backend'e baglanir. Ekran ekran sonuc (Playwright ile kosuldu):

| Ekran | Durum | Kanit |
|---|---|---|
| Login (`/console/login`) | CALISIYOR | sifre -> `/console` yonlendirmesi, BFF `204` + `skylize_console` cookie (C4) |
| System health / Tenant kartlari | CALISIYOR | `ok`, `backend · postgres`, `demo-cfo · active` (C5, C6) |
| Governance demo (Agent runner) | CALISIYOR | `cfo_agent` + JSON line_items -> "DECISION ENGINE VERDICT: DEFERRED TO HUMAN", hitl id gorunur |
| Approvals / HITL paneli | CALISIYOR | bekleyen satir listelenir; "Approve & execute" -> Deliverable karti `executed by human approval`, toplam/flag dogru |
| Kill switch paneli | KISMEN | Engage calisir (onay diyaloğu + "Engaged · status: engaged", sonra approve -> 502). **Disengage UI'si yok** (BFF'de de route yok) |
| Audit ekrani | **YOK** | `/console/audit-log` -> `404`. `app/console/` altinda yalniz `login` ve `page.tsx` var. BFF `GET /api/console/audit` calisir (C8) ama gosteren sayfa yok |

Demo notu: Kill switch'i UI'dan acarsan geri almak icin terminalden `disengage` cagrisi gerekir (S12 gibi) -
UI'dan geri alma yok. Provas yapmadan canlida UI'dan engage etme.

## 2. KANITLI vs KANITSIZ

| Iddia | Durum | Kanit / neden |
|---|---|---|
| Register -> login -> governed execute -> HITL approve -> deliverable -> audit token -> kill switch (yerel) | **KANITLI** | script 20/20 PASS, iki temiz kosu (bkz. rapor) |
| Kayit tek basina calisir | **KANITSIZ / defect** | `users_org_id_fkey` 500; demo tenant'i SQL ile seed eder (S0.3) |
| Toplam/flag deterministik, modelden bagimsiz | **KANITLI** | `execution.py:510-513` + e2e test (yanlis model ciktisi ezilir) |
| Kill switch senkron yolu reddeder (403) ve HITL icrasini keser (502), kaldirinca devam | **KANITLI** (yerel, demo LLM) | S9b/S10/S11/S11b/S12/S13 |
| Governance token audit'te | **KANITLI** | S7 |
| Konsol: governance demo + HITL + engage | **KANITLI** (yerel dev server) | Playwright oturumu, C0-C9 |
| Konsol audit ekrani, kill switch disengage UI | **YOK** | 404 / UI'da kontrol yok |
| Stripe pillar (refund gate) | **YALNIZ TEST** | 10 dosyalik hedefli kosuda 102 passed, 0 skipped (Stripe/GCP/CFO dosyalari; Stripe HTTP cagrisi sahte). **Canli gateway'de Stripe araci kayitli degil:** `bootstrap.py:928-940` `ToolProxy`/`default_tool_registry`'ye `stripe_repo`/`stripe_executor_factory` vermiyor; `tools/builtin/stripe_tools.py:144-145` bunlar yoksa `[]` doner. Repoda gercek Stripe cagrisi yok (`tests/integration/test_stripe_refund_tool_pg.py` docstring) |
| GCP VM stop (kill switch containment) | **MOCK** | `tests/integration/test_gcp_killswitch_e2e_pg.py`: gercek Postgres + gercek gate, Google'in iki endpoint'i yakalanmis sahte. Bu demoda GCP araci da kayitli degil (WIF signing key + `SKYLIZE_WIF_ISSUER_BASE_URL` verilmedi, `bootstrap.py:910-923`) |
| Gercek LLM (Claude) | **KOSULMADI** | demo LLM; metinler `[DEMO]`. Gercek anahtarla hicbir adim denenmedi |
| Hosted backend (Railway/AWS) | **YOK** | calisan bir dagitim yok; Railway adimlari [railway_backend_runbook.md](railway_backend_runbook.md) - **calistirilmadi**. `deploy-staging.yml` hic yesile donmedi (`docs/REPO_STATE.md` bolum 2, madde 1 - T1 orchestrator'a atfedilmis) |
| OPA arbiter | **KAPALI** | `SKYLIZE_DECISION_ENGINE="opa"` bootstrap'te fail-closed (CLAUDE.md ADR-0004); demo `inline` evaluator kullanir |

## 3. Kayit kontrol listesi

- [ ] `--teardown` kos, `RESULT: CLEAN` gor (onceki kosudan artik yok).
- [ ] Tam bir deneme kosusu: `20/20 PASS`. Bir kez de `--console` (30/30 PASS beklenir), konsol ilk acilista derleme yapar - **kayittan once isit**.
- [ ] Kayda baslamadan `--teardown`, sonra kayitta komutu **canli** kos (~19 sn). Cikti zaten adim adim PASS yazar.
- [ ] Terminal fontu buyuk, `[PASS]` satirlari ve `expected/actual` okunur.
- [ ] Ekranda gizli bir sey yok: script sir basmaz; yine de `$env:SKYLIZE_*` kendi shell'inde set mi bak (script bunlari **siler**, ama sen gostermeden temizle).
- [ ] Anlatimda "demo LLM" ve "yerel" kelimeleri en az bir kez geciyor.
- [ ] Kill switch'i UI'dan degil, script'ten goster (UI'dan disengage yok).
- [ ] Kayit sonrasi `--teardown` ve ciktisini sakla.

## 4. Canlida patlarsa - yedek plan

| Belirti | Neden | Hamle |
|---|---|---|
| `S0.1 compose up` FAIL | Docker kapali / port dolu / imaj yok | Docker Desktop'i ac; `--teardown`; tekrar. Imaj indirme gerekiyorsa kayit oncesi yapilmis olmali |
| `S0.2 alembic` FAIL | eski DB volume'u | `--teardown` (volume siler) ve tekrar |
| `S1 register` FAIL | sifre <8 karakter (`SKYLIZE_DEMOKIT_PASSWORD`) veya tenant seed edilmemis | env degiskenini kaldir; tekrar |
| `S3`/`S5` beklenmedik kod | ortamda artik `SKYLIZE_*` degiskeni | script bunlari siler; yine de yeni terminal ac |
| `--console` C3 timeout | `next dev` ilk derleme yavas | 240 sn'ye kadar bekler; tekrar kos, ikincisi hizli |
| Her sey patlar | - | **Yedek A:** onceden kaydedilmis tam kosu videosu. **Yedek B:** daha once kaydedilmis iki temiz kosunun terminal ciktisini goster (kayit oncesi `... > run.txt` ile al). **Yedek C:** hedefli test kosusu: `tests/integration/test_cfo_deliverable_e2e.py` (DB env'leriyle) - ayni akisi sahte saglayici ile kosar |

Her durumda dur, durumu duzelt, **ekrandaki cikti ile konusma**: PASS gormedigin adimi anlatma.
