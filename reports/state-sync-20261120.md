# State Sync - sky-sync-20261120

Kapsam: READ-ONLY. Kod degisikligi yok, merge yok. Tek cikti bu rapor branch'i. Kaynak: `git fetch --all --prune` ve `gh` ciktilari (2026-11-20).
Kaynagi olmayan her sey UNKNOWN yazildi.

## 1. main

- `origin/main` = `490b6dd40ae99c08fe79f8b669c0d9c1652abb25`, tarih 2026-11-08T10:54:57+03:00, "docs(OD26, OD27): record rulings and the Railway-wording backlog line".
- `git reflog show origin/main` en ust girdi: `490b6dd ... 2026-11-08 10:55:00 fetch: fast-forward`. 11-08'den beri main hareket etmedi.
- Son 20 commit: 490b6dd, 2a5191c, 0fe978b (#40), 5fcc081 (#34), c496d74 (#39), c8da308 (#38), a198ae7 (#37), 93ff73f (#36), 19b149e (#35), a9939ee (#31), d1f260d (#33), 2557f67 (#32), 1cfb8a8 (#24), 7604fa8 (#22), 0fc9904 (#21), a0fdcf6 (#20), 2503da0 (#28), 59934eb (#29), 646e91c (#27), 4667fb6 (#25).

## 2. Hipotez tablosu

| H | Karar | Kanit |
|---|---|---|
| H1 main SHA | CONFIRMED | `git rev-parse origin/main` = 490b6dd40ae99c08fe79f8b669c0d9c1652abb25 |
| H2 acik PR'lar #42,#43,#45,#47,#48 | CONFIRMED | Hepsi OPEN, mergedAt bos (`gh pr list`). #42/#43/#45/#48 mergeStateStatus=CLEAN, 6/6 yesil. #47 BLOCKED: `hold` label, `merge-guard` FAIL (run 43625782668), 5/6. |
| H3 #44,#46,#49,#50 | CONFIRMED (durum artik biliniyor) | Dordu de OPEN, CLEAN, 6/6. Son guncelleme #44 11-14T22:34Z, #46 22:53Z, #49 22:38Z, #50 22:34Z. Merge edilmedi. |
| H4 owner-ruling run'i calisti mi | NOT RUN (iz yok) | Bkz. bolum 6. |
| H5 required_approving_review_count=0 | CONFIRMED | `reports/ruleset-26770388-before.json`, `rules[type=pull_request].parameters.required_approving_review_count` = 0. Ek: bypass_actors=[], strict_required_status_checks_policy=true, require_code_owner_review=false. |
| H6 staging olu, canli ortam yok | PARTIAL | Staging olu: CONFIRMED. "Hicbir canli ortam yok": REFUTED. Bkz. bolum 7. |
| H7 #43 vs #45 spend-hold celiskisi | CONFIRMED (semantik, CI celiskisi degil) | Bkz. bolum 5. |

## 3. PR tablosu (acik PR'lar)

Required check'ler ruleset'ten (`rules[type=required_status_checks]`): `unit`, `roster`, `console-black`, `website`, `integration`, `merge-guard`. Her check iki event'te (pull_request + push) kosuyor, ikisi de yesil. #47'de merge-guard sadece bir kez kosmus ve FAIL.

| PR | Baslik | Head SHA | Merge state | Checks | Label | Guncelleme |
|---|---|---|---|---|---|---|
| 42 | Drive create_permission no retry | 9341847 | CLEAN | 6/6 | - | 11-14 |
| 43 | stripe.refund test-mode, flag-off (OD8) | 6556487 | CLEAN | 6/6 | - | 11-14 |
| 44 | OAuth connect broker (OD5) | c25d2f4 | CLEAN | 6/6 | - | 11-14 |
| 45 | typed ToolOutcomeUnknownError | b8399c8 | CLEAN | 6/6 | - | 11-14 |
| 46 | Langfuse v3+ bridge | 891cea6 | CLEAN | 6/6 | - | 11-14 |
| 47 | atomic refresh rotation, reuse detection, BFF single-flight | c850d00 | BLOCKED | 5/6 (merge-guard FAIL) | hold | 11-14 |
| 48 | honest roster (OD6) | 67c4755 | CLEAN | 6/6 | - | 11-14 |
| 49 | commerce department flow skeletons | fae5308 | CLEAN | 6/6 | - | 11-14 |
| 50 | REPO_STATE regen at 490b6dd | c7ab539 | CLEAN | 6/6 | - | 11-14 |

Not: `CodeRabbit` = skipping, required degil. Tum acik PR'larin tabani `main`. CLOSED (merge edilmemis): #1, #5, #23, #30.

## 4. Worktree tablosu

19 kayitli worktree (`git worktree list`). Birincil agac: `fix/notifications-fk-cascade` @2503da0, 5 kirli yol, benim degil (2 `docs/vault/.obsidian/*`, `.vscode/`, `awesome-free-llm-apis/`). CLAUDE.md pre-flight kurali geregi dokunulmadi.

| Worktree | Branch | Son commit | Kirli |
|---|---|---|---|
| skylize-agent-contracts | feat/agent-contracts-121-fill | 2026-09-20 | 0 |
| skylize-audit-readiness | feat/audit-product-readiness | 2026-10-05 | 0 |
| skylize-console-billing / knowledge / models / notifications / security / workflows-history | feat/console-* | 2026-09-20 | 0 |
| skylize-console-org-settings / permissions | feat/console-* | 2026-09-21 | 0 |
| skylize-e2e-fix | fix/e2e-deliverable-schema-drift | 2026-09-08 | 0 |
| skylize-hitl-resumption | detached 0fc9904 | 2026-09-29 | 0 |
| skylize-manifest-recon | docs/manifest-escalation-reconciliation | 2026-09-14 | 0 |
| skylize-myday-csrf | feat/myday-csrf | 2026-10-14 | 0 |
| skylize-myday-identity | feat/myday-identity-path | 2026-09-26 | 0 |
| skylize-myday-revocation-design | feat/myday-revocation-design | 2026-10-14 | 0 |
| skylize-pr-merge-scratch | work/pr22 | 2026-09-29 | 0 |
| skylize-stripe-foundation | feat/stripe-oauth-refund-foundation | 2026-09-19 | 0 |
| skylize-state-sync (yeni, bu run) | chore/state-sync-20261120 | main @490b6dd | - |

Uclu kontrol (reflog + mtime + process):

- Commit tarihi: 18 worktree'nin hicbirinde 2026-10-14'ten sonra commit yok.
- Dizin mtime: en yeni worktree dizini 2026-10-14. Git'e kayitli olmayan 3 dizin diskte: `skylize-console`, `skylize-frontend`, `skylize-myday-revocation` (mtime 2026-11-07 23:41).
- Process: bugun 17:57-18:05'te baslamis 6 `claude-code` CLI process'i + Claude Desktop calisiyor (PID 8304, 16736, 28712, 25928, 30060, 28508). cwd'leri Windows'tan okunamadi: UNKNOWN. Biri bu oturum.
- Sonuc: hicbir worktree icin 10-14 sonrasi etkinlik kaniti yok. Process cwd UNKNOWN oldugu icin "terk edilmis" KESIN degil. Hicbir worktree silinmedi.
- Gozlem: d1/d3/d4/d5 branch'leri (11-14/15) yerelde var ama worktree'leri yok. `feat/d1-langfuse-bridge` reflog: "Created from 490b6dd" 2026-11-14 20:44. 11-14 oturumu ya birincil agaci kullandi ya worktree'lerini sildi: UNKNOWN hangisi.

## 5. Celiski kaydi (H7) - ARCHITECTURE CONFLICT, cozulmedi

- #43 `tests/unit/test_stripe_refund_proxy_spend.py:360-379` @6556487, `@pytest.mark.xfail(strict=True)`, test `test_ambiguous_post_timeout_keeps_the_spend_hold`, satir 379:
  `assert world.ledger.releases == []`  -> istenen davranis: timeout'ta hold KORUNUR.
- #45 `tests/unit/test_tool_outcome_unknown.py:104-110` @b8399c8 (docstring satir 8-9: "open owner decision; these tests pin today's behaviour (released)"), satir 110:
  `assert len(repo.releases) == 1`  -> bugunku davranis: outcome-unknown'da hold BIRAKILIR.
- Bugunku kod: `src/skylize/tools/proxy.py:494` `except Exception` handler hold'u birakiyor. #43'un xfail reason'i da bunu soyluyor.
- CI etkisi simdi yok: dosyalar ayri, ikisi de CLEAN ve 6/6 yesil, strict xfail beklendigi gibi basarisiz.
- Gercek celiski: owner "hold korunur" derse #43'un strict xfail'i XPASS ile kirmizi olur ve #45'in assertion'i kirilir. Iki PR ayni karari ters yonde sabitliyor.
- Sinir: #45 testi hubspot/genel `ToolOutcomeUnknownError` yolunu, #43 Stripe `httpx.ReadTimeout` yolunu kapsiyor. #45'in Stripe yoluna uygulanip uygulanmadigi UNVERIFIED (#45 dosya listesinde `stripe_tools.py` yok: base.py, hubspot_tools.py, proxy.py + 2 test).

## 6. H4 - sky-autorun-20261115-owner-ruling: KAPANDI

Cevap: iz yok. Calismamis kabul edilir (outcome-equivalent: hicbir sonuc yok).

- Remote branch: `git ls-remote --heads origin | grep -ciE 'owner-ruling|wt/'` = 0.
- Worktree: `git worktree list` icinde `wt/owner-ruling/*` yok.
- `reports/`: origin/main'de yok.
- `docs/decisions/`: tek dosya `2026-11-07_owner_rulings.md`. OD26-OD27 11-08'de eklenmis (satir 195, 210). OD25 icin ayri dosya yok; `git grep -il OD25 origin/main` ve `git log --all --grep=OD25` bos.
- PR'lar: #47a/#47b benzeri PR yok. Son acilan PR #50.
- Sinir: "iz birakmadan baslayip yarida kesilen run" kanitlanamaz. 11-15'ten kalma process yok (hepsi bugun baslamis).

## 7. H6 - ortamlar

- AWS staging: provision edilmemis. `.github/workflows/deploy-staging.yml:3-7` (OD14/OD16/OD27). `gh run list --workflow deploy-staging.yml`: son 5 run failure (42407492832, 42406965841, 42406222420 11-07; 38620369540, 38618835846 10-14). Backend icin `railway.json` yok. OD16: son Railway deploy 2026-07-27, hepsi inactive.
- `infra/terraform/staging/modules/ecs/variables.tf:42-44` `desired_count` default 1. Sadece config, uygulanmamis.
- Canli: GitHub Deployments `Production – skylize` (id 7633191067) ve `Production – skylize-website` (7633185457): `success`, 2026-11-08T07:56Z / 07:55Z, SHA 490b6dd. Bu Vercel prod deploy'u. URL'lerin cevap verip vermedigi kontrol EDILMEDI.
- Backend API canli mi: UNKNOWN (deployment kaydi yok).
- Env var isimleri (deger YOK). BFF, `website/.env.local.example`: SKYLIZE_BACKEND_URL, SKYLIZE_SERVICE_API_KEY, SKYLIZE_CONSOLE_PASSWORD, SKYLIZE_CONSOLE_SESSION_SECRET, N8N_API_URL, N8N_API_KEY, SKYLIZE_ENABLE_N8N_ADMIN, RESEND_API_KEY, CONTACT_FROM_EMAIL, CONTACT_TO_EMAIL. Backend, `.env.example`: SKYLIZE_DB_URL, SKYLIZE_DB_APP_URL, SKYLIZE_JWT_SECRET, SKYLIZE_CREDENTIAL_ENCRYPTION_KEY, SKYLIZE_GOVERNANCE_SIGNING_KEY_PEM, SKYLIZE_REDIS_URL, SKYLIZE_DECISION_ENGINE, SKYLIZE_ANTHROPIC_API_KEY ve digerleri.
- Hangileri set degil: UNKNOWN. Vercel/Railway ortam degiskenleri okunmadi. Bu makinede 4 BFF degiskeni absent (yerel shell, prod ile ilgisiz). `gh secret list` bos dondu (repo secret yok ya da yetki yok). `AWS_GITHUB_ACTIONS_ROLE_ARN` listede yok.

## 8. BFF topolojisi: UNKNOWN (T1 blocker)

- `website/railway.json`: replica/scale ayari yok.
- `website/docs/DEPLOY_RAILWAY.md` Railway diyor, ama GitHub Deployments'ta website icin Vercel prod deploy'u var. Gercek host: UNKNOWN.
- Vercel ise serverless: coklu instance dogal. Railway ise tek ya da coklu: config belirtmiyor.
- #47 `website/src/lib/skylize/myday-refresh-lock.ts:12` @c850d00: single-flight in-process. Baska instance'lar (Node process'leri, serverless invocation'lar) korunmaz. `website/src/app/api/my-day/me/route.refresh-race.test.ts:11`: "Multi-instance behaviour is not tested."
- Sonuc: single-flight tek instance'ta calisir, coklu instance'ta degil. Instance sayisi bilinmedigi icin T1 bloklu.

## 9. bootstrap.py / config.py ayak izi

- main'de ancestor olmayan son commit: `origin/feat/commerce-stripe-testmode-wiring` 6556487 (2026-11-14T20:44:20+03:00), yani #43. Sadece origin ref'leri tarandi. Yerel-only branch'ler taranmadi.
- `origin/fix/toolproxy-ledger-commit-accounting` 1c2620c (2026-09-14) de ancestor degil, ama PR #14 MERGED: eski branch kalintisi.
- Dosyalar main'de en son 5fcc081 (2026-11-08, #34) ile degisti.
- Sahiplik: `reports/ownership.json`: iki dosya FROZEN, sahip sky-org-20261114-1738, serbest kalma kosulu #43 merge.

## 10. Owner'in kendisinin yapmasi gerekenler

1. Spend-hold karari (ARCH-CONFLICT-1): outcome-unknown'da hold korunur mu, birakilir mi. Karar gelmeden #43 ve #45'i birlikte merge etme.
2. #47: `hold` label'i ve P1 durumu. Label kalkana kadar merge-guard kirmizi.
3. Backend API ve BFF gercekte nerede kosuyor, kac instance (T1 blocker).
4. Ruleset 26770388: `required_approving_review_count` = 0. Yazma yetkili biri CLEAN PR'i tek basina merge edebilir (bypass_actors bos, 6 check hala zorunlu). Artirip artirmamak owner karari.
5. owner-ruling run'i yeniden calistirilacak mi (iz yok).
6. Vercel/Railway ortam degiskenlerinden hangileri set: sadece owner dashboard'dan gorebilir.
7. Worktree temizligi: 18 eski worktree + 3 kayitsiz dizin. Silme owner onayi bekler.
