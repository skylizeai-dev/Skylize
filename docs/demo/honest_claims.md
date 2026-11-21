# Yatirimciya soylenebilecekler / soylenmemesi gerekenler

Run: `sky-demokit-20261121` - taban `origin/main` `78458d9f2e9e6cc6ffb68037075310381af5c96d`.
Her "guvenli iddia" ya bu oturumda kosulan bir komuta ya da okunan koda dayanir.
Kanit turu: **[KOSTUM]** bu oturumda calistirdim, **[KOD]** `file:line` okudum ama o davranisi
calistirmadim, **[TEST VAR]** repoda test var, ben kosmadim. Kapsam disi her sey asagidaki
"SOYLEME" listesinde.

Cerceve cumlesi (her konusmanin basinda): *"Bunu yerelde, demo LLM ile ve tek bir senaryoyla
gosteriyorum; hosted bir uretim ortamimiz yok."*

## A. Guvenli iddialar (9)

| # | Iddia (soylenecek hali) | Tur | Kanit |
|---|---|---|---|
| 1 | "Bir ajanin niyeti, ancak acik ve denetlenebilir bir yonetisim yolundan gectikten sonra eyleme donusur." Yerelde kayit -> governed execute -> HITL onayi -> audit -> kill switch zinciri uctan uca kosuyor. | KOSTUM | `scripts/demo/cfo_test_local.py` 20/20 PASS, iki temiz kosu (kosu ciktilari raporda) |
| 2 | "Karar kapisi, riskli bir istegi insana erteler; insan onaylayana kadar model cagrilmaz ve cikti uretilmez." | KOSTUM | S3 (`202 deferred_to_human`), S4, S5; `evaluator.py:198-276`; `contracts/mvp/finance.py:183` |
| 3 | "Finansal aritmetik modelden gelmez: toplam ve riskli-kalem flag'leri deterministik olarak Python'da yeniden hesaplanir; model yanlis soylese bile ezilir." | KOSTUM + TEST VAR | S6 (`$100,000.00`, paid_media %60 flag); `execution.py:510-513`; `tests/integration/test_cfo_deliverable_e2e.py` (hedefli kosuda gecti) |
| 4 | "Yonetilen her eylem, bir governance token kimligiyle audit kaydina yazilir." | KOSTUM | S7: `agent.executed` satirinda `governance_token_id` dolu; `audit.py:44-73` |
| 5 | "Owner tenant'i durdurabilir: senkron ajan istekleri 403 ile reddedilir, bekleyen onayin icrasi 502 ile kesilir; kaldirinca bekleyen is kaybolmamistir." (yerel, demo LLM) | KOSTUM | S9b, S10, S11, S11b, S12, S13 |
| 6 | "Audit kaydi icerik degil, SHA-256 hash tutar; yani ne oldugunu, icerigi ifsa etmeden kanitlar." | KOD | `src/skylize/edge/routes/audit.py:1-10` (modul aciklamasi) - hash'in *uretildigini* ben kosmadim |
| 7 | "Yanlis yapilandirma sessizce guvensiz calismaz, baslamayi reddeder": dev auth gercek backend'de acilamaz, imza anahtari yoksa boot etmez, anahtar yoksa sessiz demo LLM'e dusmez. | KOD (+ pozitif yol KOSTUM) | `config.py:390-403`, `app/governance/keys.py:55-58`, `bootstrap.py:866-873`; demo yigini bu gereksinimlerle (ephemeral anahtarlar) boot etti (S0.4) |
| 8 | "Calisma zamani, satir-seviyesi guvenligi (RLS) devre disi birakabilen bir DB rolunde baslamaz." | KOD (+ pozitif yol KOSTUM) | `bootstrap.py:296` (`verify_app_role_is_rls_subject`), `config.py:408-440`; demo `skylize_app` rolu ile boot etti. Reddetme dalini ben tetiklemedim |
| 9 | "Stripe iade ve GCP VM-durdurma aksiyonlari icin yonetisim kapilari ve testleri yazildi; dis servisler testte sahtelenerek dogrulaniyor." Test kaniti: hedefli 10 dosyalik kosuda 102 passed, 0 skipped. | KOSTUM (test) | `tests/unit/test_stripe_*`, `tests/unit/test_gcp_*`, `tests/integration/test_stripe_refund_*_pg.py`, `test_gcp_killswitch_e2e_pg.py`. **Bu bir "canlida calisiyor" iddiasi degil** |

> 9 numarali iddiada "yazildi ve testle dogrulandi" de; "canlida gate'leniyor" **deme** (asagida #1).

## B. SOYLEME listesi

| # | Soyleme | Neden - kanit |
|---|---|---|
| 1 | "Stripe, canli sistemde gate'leniyor / iadeleri yonetiyor." | Canli gateway'de Stripe araci **kayitli degil**: `bootstrap.py:928-940` `stripe_repo`/`stripe_executor_factory` gecirmiyor, `tools/builtin/stripe_tools.py:144-145` onlar yoksa `[]` doner. Repoda gercek Stripe cagrisi yok (`test_stripe_refund_tool_pg.py` docstring) |
| 2 | "Hosted/production backend'imiz var." | Calisan bir dagitim yok. Railway adimlari yazildi, **calistirilmadi** (`railway_backend_runbook.md`). `deploy-staging.yml` hic yesile donmedi (`docs/REPO_STATE.md` bolum 2 madde 1, T1 orchestrator'a atfedilmis) |
| 3 | "Gercek Claude/LLM ile calisiyor." | Hicbir adim gercek LLM ile kosulmadi. Demo LLM `[DEMO]` metinleri uretir (`demo_adapter.py:96-101`) |
| 4 | "GCP VM'ini gercekten durdurduk / durdurabiliriz." | Yalniz test: Google'in iki endpoint'i sahte (`test_gcp_killswitch_e2e_pg.py` basligi). Demoda GCP araci kayitli bile degil (`bootstrap.py:910-923`: WIF key + issuer URL gerekir) |
| 5 | "Self-serve kayit calisiyor." | `register` tenant olmadan 500 verir (`users_org_id_fkey`, `DAY1_PROVISIONING.md:262`); demo tenant'i SQL ile seed eder (S0.3) |
| 6 | "Konsolda audit ekrani var." | `/console/audit-log` -> 404; `app/console/` altinda yalniz login + tek sayfa. BFF route'u var, sayfa yok |
| 7 | "Konsoldan kill switch acilip kapatilabilir." | UI'da yalniz Engage var; Disengage kontrolu ve BFF route'u yok. Geri alma API'den (S12) |
| 8 | "Kill switch her istegi aninda reddeder." | Erteleme karari kill switch'ten once verilir: acikken `hook_generator_agent` istegi yine `202 deferred` dondu. Kesme, token/icra noktasinda (S11, S11b) |
| 9 | "Audit hangi insanin ne yaptigini imzayla kanitlar." | Audit'te insan aktor kolonu yok, `source_agent_id` ajan kimligi; hash'ler icerik hash'i, imza degil (`audit.py:30`; `website/src/app/api/console/audit/route.ts` ust notu) |
| 10 | "OPA/Rego uretim karar motoru olarak calisiyor." | `SKYLIZE_DECISION_ENGINE="opa"` bootstrap'te fail-closed; calisan `inline` evaluator (CLAUDE.md ADR-0004). OPA bundle'i placeholder, hepsini reddeder (`infra/docker-compose.yml:34-41`) |
| 11 | "Tum test seti yesil / CI 6/6." | Tum suite'i bu oturumda **kosmadim** (yasak). Yalniz hedefli dosyalar. CI durumu icin PR sayfasina bak, hafizadan konusma |
| 12 | "Musteri, gelir, pilot var" / "uretimde X ajan calisiyor." | Bu repoda ve bu oturumda bu iddiayi destekleyen hicbir kanit yok |
| 13 | "Guvenlik denetiminden gecti / SOC2 / production-ready multi-tenant." | Hicbiri kanitlanmadi. RLS icin yalniz kod yolu ve pozitif boot gozlemi var (iddia #8) |
| 14 | "Demo'daki ozet metinlerini LLM yazdi." | Metinler `[DEMO]` sabit metinlerdir; yalniz toplam/flag hesaplanir |

## C. Soru-cevap kisayollari

- *"Gercek mi?"* -> "Yonetisim zinciri gercek kod ve gercek Postgres uzerinde kosuyor; LLM demo adaptoru, ortam yerel. Hosted urun yok."
- *"Stripe?"* -> "Iade kapisi ve testleri yazildi, dis cagri testte sahte; canli gateway'e henuz baglanmadi."
- *"Kill switch?"* -> "Tenant seviyesinde senkron ajan isteklerini ve bekleyen onay icrasini durdurur; geri alinabilir. Konsol UI'da yalniz durdurma var."
- *"Ne zaman canli?"* -> Tarih verme. Railway runbook'u hazir, calistirilmadi; ilk adim sahibi tarafindan hesap + anahtar.
