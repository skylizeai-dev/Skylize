# Railway backend kurulumu - runbook (CALISTIRILMADI)

Run: `sky-demokit-20261121` - taban `origin/main` `78458d9f2e9e6cc6ffb68037075310381af5c96d`.

> **Bu dosya bir plan, bir kayit degil.** Hicbir adimi Railway'de calistirmadim; Railway
> hesabina, projesine ya da gercek bir anahtara dokunmadim. Su an **hosted backend yok**.
> Env **isimleri** koddan dogrulandi (her satirda `file:line`); Railway'e ozgu davranislar
> (**UNVERIFIED**) ilk denemede dogrulanmali.
>
> Not: gorev metni "recon bolum 2" listesine atif yapiyordu; `sky-demo-20261121` recon raporu
> bu makinede/repoda bulunamadi. Liste bu yuzden koddan yeniden cikarildi. Recon'daki liste
> ile farki varsa recon'u degil **kodu** esas al (CLAUDE.md EVIDENCE DISCIPLINE).

Etiketler: **[OWNER]** yalniz sen (hesap, para, gizli deger). **[CLAUDE]** repo icinde hazirlanabilir.
**[OWNER+CLAUDE]** sen degeri uretir/girersin, dogrulamayi ben yaparim.

## 0. Mimari (hedef)

```
Browser -> Next.js website (Railway svc #2, root=website/) -> [X-API-Key] -> FastAPI gateway (Railway svc #1, root Dockerfile)
                                                                                |-> Railway Postgres (plugin)
                                                                                |-> Railway Redis    (plugin)
```

Website zaten Railway icin hazir (`website/railway.json`, `website/docs/DEPLOY_RAILWAY.md`).
Eksik olan **backend servisi** ve ikisi arasindaki anahtar.

## 1. Servisler **[OWNER]**

1. Railway projesinde **Postgres** ve **Redis** plugin'i ekle.
2. Yeni servis (GitHub repo, root dizin = repo koku). Imaj = kok `Dockerfile` (tek gateway imaji;
   `Dockerfile` basligi ve `infra/docker-compose.yml:58-61`). `CMD` her baslangicta
   `alembic upgrade head && uvicorn skylize.edge.gateway:app --host 0.0.0.0 --port 8000`
   calistirir (`Dockerfile:83`), yani **migrasyon admin DSN'i ile boot'ta kosar**.
3. Servis portu: Dockerfile `8000`'e sabit (`Dockerfile:76,83`). Railway genelde `$PORT` bekler -
   **UNVERIFIED:** `PORT=8000` degiskenini servise ekle ve public networking'i 8000'e bagla; ilk
   deploy'da `/health` 200 donuyor mu bak (`Dockerfile:79` healthcheck `curl :8000/health`).

## 2. Backend env isimleri (gateway servisi)

| Env adi | Zorunlu? | Deger nereden / nasil uretilir | Kim set eder | Kaynak (file:line) |
|---|---|---|---|---|
| `SKYLIZE_BACKEND` | evet | sabit `postgres` | OWNER | `config.py:31` (varsayilan `memory`!) |
| `SKYLIZE_DB_URL` | evet | **admin** DSN. Railway Postgres'in baglanti URL'i (`postgresql://...`). Yalniz migrasyon/uzanti icin | OWNER (Railway referans degiskeni) | `config.py:49`; `migrations/env.py:34` |
| `SKYLIZE_APP_DB_PASSWORD` | evet (taze DB) | **sen uret**: `py -3.12 -c "import secrets;print(secrets.token_urlsafe(32))"`. Migration 0003'un yaratacagi `skylize_app` rolunun sifresi | OWNER | `migrations/versions/0003_app_role_rls_subject.py:49`; `config.py` (app_db_password aciklamasi) |
| `SKYLIZE_DB_APP_URL` | evet | `postgresql://skylize_app:<APP_DB_PASSWORD>@<host>:<port>/<db>`. **Sifre `SKYLIZE_APP_DB_PASSWORD` ile ayni olmali; hicbir sey bunu dogrulamaz**, uyumsuzluk boot'ta auth hatasi. `SKYLIZE_DB_URL`'den **farkli** olmak zorunda | OWNER | `config.py:50,408-440`; `DAY1_PROVISIONING.md` 1b |
| `SKYLIZE_REDIS_URL` | evet | Railway Redis baglanti URL'i | OWNER | `config.py:51` |
| `SKYLIZE_DEV_AUTH` | evet | sabit `false` (postgres'te `true` Settings'i boot ettirmez) | OWNER | `config.py:390-403` |
| `SKYLIZE_JWT_SECRET` | evet | `py -3.12 -c "import secrets;print(secrets.token_urlsafe(48))"`; dev auth kapaliyken zorunlu | OWNER | `config.py:112,363-370` |
| `SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM` | evet | `py -3.12 scripts/gen_governance_key.py` (ECDSA P-384 PKCS8 PEM; stdout'a basar, **commit etme**). Coklu satir: Railway'de "raw editor" ile yapistir | OWNER | `scripts/gen_governance_key.py`; `config.py:89`; `app/governance/keys.py:55-58` (bos ise fail-closed) |
| `SKYLIZE_CREDENTIAL_ENCRYPTION_KEY` | evet | Fernet anahtari: `py -3.12 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`. **Kaybedersen saklanan tum credential'lar cozulemez** - yedekle | OWNER | `config.py:129`, `bootstrap.py:106` (resolve_credential_encryption_key) |
| `SKYLIZE_ANTHROPIC_API_KEY` | LLM icin evet | Anthropic konsolundan **senin** anahtarin. Bos + `LLM_DEMO_MODE` kapaliysa servis boot'ta `LLMConfigurationError` ile durur | OWNER (gizli, para) | `bootstrap.py:866-873` |
| `SKYLIZE_LLM_DEMO_MODE` | hayir | `true` yalniz gercek anahtar yoksa ve **gostermelik** isteniyorsa; gercek dagitimda **asla** | OWNER | `config.py:306`; `bootstrap.py:865` |
| `SKYLIZE_DECISION_ENGINE` | hayir | `inline` (varsayilan). `opa` henuz acilamaz | - | `config.py:268`; CLAUDE.md ADR-0004 |
| `SKYLIZE_DECISION_ENGINE_ORG_IDS` | demo icin evet | JSON dizisi, ornek `["acme-demo"]`. **Gate yalniz burada listelenen org'larda aktif**; listede degilse `/agents/execute` kapisiz calisir | OWNER+CLAUDE | `config.py:274`; `bootstrap.py:955` |
| `SKYLIZE_CORS_ORIGINS` | hayir | **ayarlama** - tarayici trafigi same-origin BFF'ten gecer; `*` yasak | - | `config.py:352-360`; `website/docs/DEPLOY_RAILWAY.md` |

Ayarlanmamasi gerekenler (gercek dis cagri acar): `SKYLIZE_SLACK_*`, `SKYLIZE_GITHUB_APP_*`,
`SKYLIZE_WIF_*`, Stripe/Google credential'lari. Demo icin bunlari **bos birak**.

> Pin: `SKYLIZE_LANGFUSE_*` bu dagitimda **etkisiz** (kullanan istemci yok) - `DAY1_PROVISIONING.md` 3d.

## 3. Veritabani hazirligi

1. **[OWNER]** Uzantilar: migrasyonlar `pgcrypto` ve `btree_gist`'i kendisi yaratir
   (`migrations/versions/0005_memory_provenance.py:47`, `0019_principal_authority.py:135`), ama
   `infra/postgres/init/00_extensions.sql` ayrica `pg_trgm` ve `btree_gin` ister ve compose'ta
   init-time calisir. **UNVERIFIED:** Railway'de bu ikisinin migrasyondan once olusup olusmadigi.
   Hata alirsan admin baglantisiyla `00_extensions.sql`'i elle calistir.
2. **[OWNER]** Ilk deploy -> `alembic upgrade head` calisir -> `skylize_app` rolu olusur
   (`0003`). Log'da `0037 (head)` gor.
3. **[OWNER]** RLS: runtime rolu superuser/BYPASSRLS olmamali; `bootstrap.py verify_app_role_is_rls_subject`
   (`bootstrap.py:296`) boot'ta bunu reddeder - bu **istenen** davranis.

## 4. Ilk tenant + ilk anahtar (kilit-ac) **[OWNER]**

Bos veritabaninda uc kapi birbirine kilitli (tenant satiri, kayit, API key): `DAY1_PROVISIONING.md` 5a.
Cozum, HTTP disi operasyonel komut (`src/skylize/ops/bootstrap_api_key.py`). Railway servisinin
shell'inde veya `railway run` ile, **uygulama rolu** DSN'iyle:

```powershell
# degerleri shell'e yaz, repoya/log'a degil
$env:SKYLIZE_BACKEND = "postgres"
$env:SKYLIZE_DB_APP_URL = "<app-rol DSN'i>"
py -3.12 -m skylize.ops.bootstrap_api_key --org-id <org> --create-tenant --display-name "<ad>" --expires-in-days 30
```

Anahtar **yalniz stdout'a, bir kez** basilir (`DAY1_PROVISIONING.md` 5c). Hemen bir sirlar yoneticisine
ya da dogrudan website servisinin `SKYLIZE_SERVICE_API_KEY`'ine aktar; ekrana/sohbete yapistirma.
Ardindan HTTP ile "gercek" bir key uret ve bootstrap key'i iptal et (5c Adim 4).

**Insan kullanicisi** (konsolun degil, backend `/auth/login` icin): bu org'a `POST /api/v1/auth/register`
cagir; tenant satiri artik var, bu yuzden `users_org_id_fkey` defect'ine takilmaz (kaynak: yerel demo S0.3
ayni durumu SQL ile saglar).

## 5. Website servisi (zaten hazir) **[OWNER]**

`website/docs/DEPLOY_RAILWAY.md` ile ayni, yalniz isimler:

| Env adi | Deger | Kim |
|---|---|---|
| `SKYLIZE_BACKEND_URL` | gateway servisinin Railway-internal ya da public URL'i, sonda `/` yok | OWNER |
| `SKYLIZE_SERVICE_API_KEY` | Bolum 4'teki owner-scope'lu `sky.` anahtari | OWNER |
| `SKYLIZE_CONSOLE_PASSWORD` | sen belirle (tek-sahip gecici kapi) | OWNER |
| `SKYLIZE_CONSOLE_SESSION_SECRET` | >= 32 karakter rastgele: `py -3.12 -c "import secrets;print(secrets.token_urlsafe(48))"` | OWNER |

Hicbiri `NEXT_PUBLIC_` ile baslamamali (`website/src/lib/skylize/config.ts` bunu reddeder).

## 6. Dogrulama **[CLAUDE]** (anahtarlar sende, ben yalniz kosarim)

Yerel kitin ayni adimlarini hosted URL'e karsi calistirmak icin script'te bir `--base-url` modu **yok**;
bugunku script yalniz yerel yigini kurar. Hosted dogrulama icin el ile:

1. `GET <backend>/health` -> 200.
2. Konsol girisi -> `GET /api/console/health` ve `/tenant`.
3. Governed org icin `cfo_agent` execute -> `202 deferred_to_human` -> approve -> deliverable.
4. Kill switch engage/disengage, ardindan approve davranisi (`cfo_test_runbook.md` Adim 5).

Bunlar yapilmadan "hosted prod var" iddiasi **yapilmaz** ([honest_claims.md](honest_claims.md)).

## 7. Kesinlikle sen: karar ve para **[OWNER]**

- Railway plani/maliyeti, domain, hangi org'un governed olacagi.
- Gercek Anthropic anahtari (demo LLM'den gercek LLM'e gecis = maliyet + veri kararı).
- Anahtar rotasyonu (`SKYLIZE_JWT_SECRET` degisirse tum oturumlar duser; Fernet anahtari degisirse credential'lar kaybolur).
- Staging/AWS yolu ayri: `deploy-staging.yml` hic yesil olmadi (`docs/REPO_STATE.md`), bu runbook onunla ilgili degil.

## 8. Bilinen belirsizlikler (UNVERIFIED)

- `$PORT` ile sabit 8000 uyumu (Bolum 1.3).
- Railway Postgres'te `pg_trgm`/`btree_gin` (Bolum 3.1).
- Railway'in coklu satirli PEM degiskenini nasil sakladigi: ilk boot'ta `governance` anahtari yuklenemezse
  `keys.py:55-58` acik hata verir.
- Railway Postgres'in superuser kullanicisi olup olmadigi (`0003` rol yaratir ve `ALTER ROLE` yapar).
