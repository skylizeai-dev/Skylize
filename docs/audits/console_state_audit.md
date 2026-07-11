# Skylize Console — Durum Denetimi

**Kapsam:** `/console/chat`, `/console/dashboard`, `/console/agents`, `/console/network`, `/console/workflows`, `/console/logs`, `/console/settings`, marketing (`app/page.tsx` + hero/`altitude-dashboard.tsx`).
**Metodoloji:** Salt-okuma, statik kod incelemesi (dosya okuma + grep). Dev server çalıştırılmadı, ekran görüntüsü alınmadı — aşağıdaki bulgular kod okumasıyla doğrulanmış "statik" bulgulardır; runtime/görsel doğrulama işaretlenmemiştir.
**Değiştirilen dosya yok.**

---

## 0. Mimari bağlam — önce bunu bilin

Denetlenen 7 sayfa **iki paralel implementasyonun** yeni olanı:

| | Konum | Durum |
|---|---|---|
| **Eski/fonksiyonel** | `src/app/(console)/*` (route group → URL'de `/agents`, `/dashboard`, `/dashboard/deliverables`), `src/app/login`, `/register` | Gerçek backend'e bağlı (`lib/auth.ts`, `lib/agents.ts`, `lib/deliverables.ts` → `/api/v1/...`). Precision Altitude'a **hiç** giydirilmemiş — generic shadcn görünüm (rounded-lg input, mavi focus-ring). Jun 30 tarihli. |
| **Yeni/giydirilmiş** (bu denetimin konusu) | `src/app/console/*` — istenen 7 sayfanın tamamı burada | Precision Altitude'a tam giydirilmiş, ama **%100 mock veri** (`mock-orchestrator.ts`, sabit diziler). Gerçek backend endpoint'lerine (yukarıdaki `lib/*`) bağlı değil. Jul 2 (en yeni). |

Bu ayrım önemli: aşağıdaki "eksik" bulguların çoğu **kod hatası değil**, iki dalın henüz birleşmemiş olması. Backend gerçek (`org_id`'li auth, agents API, deliverables API çalışıyor) — sadece yeni konsol arayüzü ona bağlanmamış.

Ayrıca repo kökündeki `./console/` klasörü bir Next.js build-cache artığıdır (`next-env.d.ts` + `.next` + `node_modules`, `package.json` yok, kaynak kod yok) — gerçek bir ikinci uygulama değil, muhtemelen yanlışlıkla oraya `next dev` çalıştırılmış. Zararsız ama temizlenmeli.

---

## 1. Sayfa-uyum tablosu (Precision Altitude)

| Sayfa | Max radius | Hover deseni | Metrik mono/tabular | Sparkline | Yasak öğe | Transition | Yoğunluk | Verdict |
|---|---|---|---|---|---|---|---|---|
| `/console/chat` | 6px (`rounded-[4px]`/`[6px]`) | border-color (`hover:border-border-strong`) | ✅ evet (token stat footer) | — (metrik yüzeyi değil) | yok | 150–320ms | Merkezî sütun, iyi ritim | **OK** |
| `/console/dashboard` | 6px (`rounded-md`) | border-color / bg-tint | ✅ evet (StatCard + tablo) | ✅ evet (StatCard SVG polyline) | yok | 150–500ms | 4-stat grid + 2 kolon, dengeli | **OK** — referans sayfa |
| `/console/agents` (drag/pan canvas) | 6px (`rounded-md`) | border-color, **+ 3D tilt/lift + drop-shadow** (spec'te tanımlı değil) | ✅ evet | — | yok (ambient radial glow çok soluk, mevcut vinyet desenine tutarlı) | 150ms border + **spring physics** (sabit easing token'ı değil) | Canvas, whitespace kasıtlı | **GAP** — spring hareket "Precision Ascent" (`cubic-bezier`, "asla zıplamaz") sözleşmesini bypass ediyor |
| `/console/network` (org-map/radial) | 6px (`rounded-md`) | **hardcoded `hover:border-[#3B4260]`** (token değil, ~8 yerde) | ✅ evet (en disiplinlisi) | yok (ince budget bar var, sparkline değil) | ✅ **backdrop-blur-[1px]** (detay panel arka planı) + ✅ **renkli glow** (`0 0 22px`, `0 0 28px` box-shadow) | 150–600ms, `EASE_ALTITUDE` tutarlı | En zengin/tamamlanmış sayfa | **GAP** — 2 kesin yasak-öğe ihlali burada |
| `/console/workflows` | — | — | — | — | — | — | Dashed placeholder box | **SCAFFOLD** (kasıtlı "coming soon", bug değil) |
| `/console/logs` | 6px (`rounded-md`) | N/A (liste, interaktif değil) | ✅ evet (satırın tamamı mono) | — (metrik yüzeyi değil) | yok | 150ms | Sabit `max-h-[420px]` — bağımsız `/logs` sayfasında altında boş alan bırakıyor | **COSMETIC** — minor |
| `/console/settings` | — | — | — | — | — | — | Dashed placeholder box | **SCAFFOLD** (kasıtlı, bug değil) |
| **Marketing** `app/page.tsx` + **hero** (`hero.tsx` + `altitude-dashboard.tsx`) | **12px** (`rounded-xl`, hero dashboard + 6 diğer section kartı sitewide) | border-color, tutarlı | ⚠️ **kısmi** — hero footer KPI'ları (`1,284` / `37ms` / `99.98%`) `font-display` kullanıyor, mono değil; aynı bileşenin içindeki `+218%` ve agent-load % değerleri doğru şekilde mono | ✅ evet (throughput eğrisi) | yok (radial vinyet/mask'lar dolgu-gradient değil, fade-mask) | `EASE_ALTITUDE`, tutarlı | Bol whitespace, `clamp()` ritim | **GAP** — hero'nun en görünür KPI'larında mono ihlali (kesin) |

**Not — radius çelişkisi:** Marketing genelinde (`agents.tsx`, `case-studies.tsx`, `solution.tsx`, `testimonials.tsx`, `roi.tsx`, hero dashboard) `rounded-xl` (12px) **tutarlı ve kasıtlı görünen** bir dış-kart ölçeği; konsol yüzeyleri ise tutarlı şekilde `rounded-md` (6px) kullanıyor. Yani muhtemelen "6px tavan" kuralı konsolun yoğun/enstrüman-paneli estetiği için, marketingin daha büyük dış kartları için değil. Bunu bug olarak değil, **netleştirilmesi gereken kural kapsamı sorusu** olarak işaretliyorum — takım "6px kuralı sadece konsol mu, sitewide mi" diye karar vermeli.

---

## 2. Mimari/kabuk hazırlığı (B)

| Kontrol | Durum | Severity | Kanıt |
|---|---|---|---|
| Tüm `/console` route'larını saran tek app shell | **PRESENT** | OK | `console/layout.tsx` → `Sidebar` + `TopBar`, hem `(workspace)` grubunu hem `/chat`'i sarıyor |
| Kalıcı SAĞ-RAIL slotu (layout seviyesinde, jenerik) | **MISSING** | GAP | Yok. Chat'te `AgentActivityPanel` sadece `ChatContainer` içine hardcode; Dashboard/Agents/Network kendi sağ panelini (LogFeed / detail panel / DetailPanel) bağımsız olarak 4 farklı şekilde yeniden icat ediyor. Paylaşılan bir "sağ rail" primitive'i yok. |
| Sol sidebar collapse edilebilir mi | **PRESENT** | OK | `sidebar.tsx` — desktop'ta 48px↔224px toggle, mobilde tam slide-over |
| Top bar: breadcrumb | **PARTIAL** | GAP | Sadece tek seviye: `Logo › {SayfaAdı}` (pathname'den türetiliyor). Çok-seviyeli breadcrumb yok. Ayrıca `chat-container.tsx` bir `CONSOLE_EVENTS.title` event'i dispatch ediyor (yorum: "Container → topbar: update the breadcrumb conversation title") ama `top-bar.tsx` bu event'i **hiç dinlemiyor** — kablo yarım bırakılmış, muhtemelen henüz kurulmamış sohbet-geçmişi özelliğinin iskelesi. |
| Top bar: global token sayacı | **PRESENT** | OK | `"12,847 tok/hr"`, mono, her zaman görünür |

---

## 3. Feature-hook hazırlığı (C) — inşa edilmedi, sadece raporlanıyor

| Hook | Durum | Severity | Kanıt |
|---|---|---|---|
| Orchestrator client/hook | **PRESENT (mock)** | OK (bilinçli scaffold) | `lib/mock-orchestrator.ts` — async generator, gerçek network çağrısı yok, ama gerçek yaşam döngüsünü taklit ediyor (`resolve → governance gate → mint token → run → validate → emit → audit`). `ChatContainer` sadece async-generator arayüzü tüketiyor → gerçek API'ye geçiş tek dosyalık, izole bir değişiklik olur. **İyi kurulmuş scaffold, bozuk değil.** |
| n8n MCP client kodda wire'lı mı | **MISSING (sadece mock/metin referansı)** | OK — beklenen (kullanıcı hipotezi doğrulandı) | `N8N_MCP_URL` sadece mock cevap metninde bir string. Gerçek backend spesifikasyonu (`docs/06_integrations/n8n.md`) n8n çağrılarının **sunucu tarafında**, imzalı payload/HMAC ile yapılmasını öngörüyor — yani frontend'de wire olmaması aslında **doğru mimari**, frontend'in n8n'i doğrudan çağırmaması gerekiyor zaten. Gap yok, yanlış anlaşılmaya açık nokta yok. |
| Governance/approval bileşeni (Approve/Modify/Decline ActionCard) | **MISSING** | GAP | Sadece pasif `GovernanceBadge` var (token durumu: valid/expired/revoked/denied + tooltip). İnteraktif onay/red/değiştir arayüzü `/console/*` içinde hiçbir yerde yok. Backend spesifikasyonu (`docs/04_decision_engine/guardrails.md`) resmî bir `HumanInLoopTrigger` / `decision.deferred_to_human` kavramı tanımlıyor — yani bu özellik planlanmış ama konsolda henüz yüzeye çıkmamış. **Yakın bir referans var:** eski `(console)/dashboard/deliverables/[id]` sayfasında gerçek backend'e bağlı bir Approve/Revise/Archive akışı zaten mevcut (Dialog + confirm) — ama bu, deliverable (içerik) onayı için, agent-aksiyonu governance'ı için değil ve yeni konsola bağlı değil. |
| Memory katmanı + tenant-namespace | **PARTIAL** | GAP | Tenant kimliği (`org_id`) gerçek ve uçtan uca çalışıyor (`lib/auth.ts` register/login, `UserResponse.org_id`). Ama frontend'de gerçek bir memory katmanı kodu yok; `"namespace: org/*"`, `"namespace: marketing/brand"` gibi string'ler sadece `mock-orchestrator.ts` içinde mock `input_summary` metni — gerçek spesifikasyondaki (`docs/05_memory/organizational_memory.md`: `org:decisions`, `org:playbooks` …) namespace taksonomisini bilinçli şekilde taklit ediyor ama arkasında hiçbir depolama/retrieval kodu yok. |
| Playbook yüzeyi | **MISSING** | — (beklenen) | Kod tabanında "playbook" kelimesine hiç rastlanmadı. Kullanıcının kendi beklentisiyle uyumlu. |
| Onboarding akışı | **MISSING** | GAP | `/register` + `/login` gerçek (backend'e bağlı) ama sadece çıplak auth formu — ürün turu, ilk-kurulum, agent-org yapılandırma adımı yok. Ayrıca bu iki sayfa Precision Altitude'a **hiç** giydirilmemiş (generic shadcn `rounded-lg`, mavi `focus:ring`) — denetim kapsamının dışında ama görsel kopukluk olarak not edildi. |
| File ingestion | **MISSING** | — (beklenen) | `website/src` içinde upload/ingest kodu yok; `MessageInput` sadece metin kabul ediyor. |

---

## 4. Gerçek sorun vs. henüz-kurulmamış — net ayrım

**Doğrulanmış, kesin ihlaller (kodda okunarak teyit edildi — 4 adet, hepsi küçük/lokal düzeltme):**
1. `agent-network.tsx` (`/console/network`) — `backdrop-blur-[1px]` (glassmorphism, yasak).
2. `agent-network.tsx` (`/console/network`) — renkli glow box-shadow'lar (`0 0 22px`, `0 0 28px`), "no glow" kuralına aykırı.
3. `altitude-dashboard.tsx` (marketing hero) — footer KPI değerleri (`1,284` / `37ms` / `99.98%`) `font-mono` değil `font-display` kullanıyor; aynı bileşende diğer sayılar doğru mono.
4. `agent-network.tsx` — hover rengi (`#3B4260`) 8 yerde tema token'ı yerine hardcoded hex; bugün görsel fark yaratmıyor ama token güncellenirse sessizce sapar.

**Muhtemelen kasıtlı, henüz-kurulmamış iskele (bug değil — "unverified/not-yet-built"):**
- `/console/workflows`, `/console/settings` → açık "coming soon" placeholder'ları.
- n8n MCP frontend'de wire değil → spesifikasyona göre zaten olmaması gerekiyor.
- Governance ActionCard, playbook yüzeyi, file ingestion, onboarding akışı → hiçbiri inşa edilmemiş, ama hiçbiri de "kırık" değil; sadece yok.
- `CONSOLE_EVENTS.newChat` / `loadChat` hiçbir yerden dispatch edilmiyor (sidebar'da sohbet geçmişi UI'ı yok) → muhtemelen planlanan bir "chat history" özelliğinin önden yazılmış event-bridge'i, henüz UI'ı gelmemiş.
- Sağ-rail slotu, çok-seviyeli breadcrumb → henüz genelleştirilmemiş, her sayfa kendi çözümünü üretmiş; mimari borç ama runtime hatası değil.

**Değerlendirme gerektiren gri alan (bug ile tasarım kararı arasında):**
- `/console/agents` canvas'ındaki spring-based hover lift/tilt — "Precision Ascent asla zıplamaz" ilkesiyle gerginlik yaratabilir; görsel olarak overshoot/bounce olup olmadığı runtime'da (tarayıcıda) doğrulanmalı, statik kod okumasından kesin "bozuk" denemez.
- `rounded-xl` (12px) marketing genelinde tutarlı kullanılıyor — 6px kuralına aykırı ama sistematik/kasıtlı görünüyor, "hata" değil "kapsam belirsizliği".

**Toplam kesin ihlal sayısı: 4** — geçmişteki agent-network denetiminin abartma paterni burada tekrarlanmadı; çoğu bulgu GAP/SCAFFOLD, BLOCKER yok.

---

## 5. En yüksek-ROI ilk 5 düzeltme (öncelik sırasıyla)

1. **`agent-network.tsx`: `backdrop-blur-[1px]` ve iki glow box-shadow'u kaldır/nötrle.** Salt CSS, ~3 satır, tek kesin "yasak öğe" ihlalini konsolun en gelişmiş sayfasından temizler.
2. **`altitude-dashboard.tsx`: hero footer KPI'larını `font-display` → `font-mono` yap.** Tek satır × 3, ürünün en görünür yüzeyindeki tek "mono sayı" ihlalini kapatır.
3. **`agent-network.tsx`: hardcoded `hover:border-[#3B4260]` → `hover:border-border-strong` token'ına geçir.** Bugün görsel etkisi yok, ama gelecekteki token güncellemelerinde sessiz sapmayı önler; ucuz, risksiz.
4. **`TopBar`'ı `CONSOLE_EVENTS.title` event'ini dinleyecek şekilde tamamla (ya da bilinçli olarak ertelendiğini belgeleyerek event'i kaldır).** Yarım bırakılmış kablo bugün "unutulmuş bug" gibi duruyor; ~10 satırla ya bitirilir ya da niyet netleştirilir.
5. **`/console/agents` vs `/console/network` isim/kapsam çakışmasını çöz.** İkisi de "Agent Network" başlığı taşıyor ama tamamen farklı görselleştirmeler (serbest drag-canvas vs. org-map/radial). Bu bir kod hatası değil ürün/IA kararı, ama Governance ActionCard, playbook gibi üstüne inşa edilecek her şeyin hangi sayfaya bağlanacağını belirlediği için önce bunun netleşmesi, sonraki işi ikiye bölünmekten kurtarır.

*(6. sıradaki, opsiyonel: sağ-rail'i tek paylaşılan bir layout slot'una çıkarmak — 4 sayfanın ayrı ayrı ürettiği paneli tekilleştirir; mimari faiz öder ama bu turun 5-madde sınırının dışında tutuldu.)*
