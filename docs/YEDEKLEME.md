# MexAy veritabanı yedekleme ve geri yükleme rehberi

> Render kaydı (27 Ekim 2026 itibarıyla): `ai-isletme-asistani-db`, ücretsiz plan, PostgreSQL 18, Frankfurt, **süresi 28 Ekim 2026 13:14 UTC'de doluyor**.

Amaç: Render'daki PostgreSQL veritabanını (`ai-isletme-asistani-db`) kaybetmeden yedeklemek ve yedeğin gerçekten geri yüklenebildiğini kanıtlamak.

> **Önemli:** Yedek dosyası müşteri verisi içerir. GitHub'a yüklemeyin, herkese açık bir yerde paylaşmayın. Bağlantı adresini (`postgresql://kullanıcı:parola@...`) bir parola gibi saklayın; sohbete, e-postaya veya depoya yazmayın.

## 1. Hazırlık

1. Render panelinde veritabanını açın. Planını, durumunu, **PostgreSQL sürümünü** ve olası askıya alma/silinme tarihini not edin.
2. **Connections** bölümünde **External Database URL** değerini bulun. (Bilgisayarınızdan bağlanmak için dış adres gerekir; *Internal* adres çalışmaz.)
3. **IP izin listesi (Networking):** Bu veritabanında izin listesi **boş** olabilir; bu durumda dışarıdan gelen **tüm** bağlantılar engellenir ve `pg_dump` bağlanamaz. Render'da veritabanı sayfası → **Networking → Inbound IP Rules** bölümüne yedeği alacağınız bilgisayarın genel IP adresini `/32` ile ekleyin (ör. `203.0.113.7/32`). Colab'ın IP adresi her oturumda değişir; Colab kullanacaksanız yedek süresince geçici olarak `0.0.0.0/0` eklemek gerekebilir. Bu, adresi **herkese** açar (parola ve TLS hâlâ korur), bu yüzden **yedek biter bitmez kuralı silin.**
4. Bilgisayarınızda PostgreSQL **istemci** araçları (`pg_dump`, `pg_restore`, `psql`) kurulu olmalı. İstemci sürümü, sunucu sürümünden **eski olmamalı** (bu veritabanı **PostgreSQL 18** olduğundan istemci 18 veya üstü olmalı).
5. Kendi bilgisayarınız yoksa/uygun değilse aşağıdaki **Colab** yolunu kullanın.

## 2. Yedek alma (bilgisayar veya sunucu)

```bash
export DATABASE_URL='postgresql://...'      # Render External Database URL
./scripts/backup_db.sh ./yedekler
```

Betik: yedeği `mexay_YYYYMMDD_HHMMSS.dump` olarak yazar, arşivin okunabildiğini ve boş olmadığını doğrular, `.sha256` özet dosyası üretir. Bağlantı adresini ekrana yazmaz.

Aynı dosyayı **ikinci bir güvenli yere** de kopyalayın (ör. şifreli harici disk veya erişimi kısıtlı özel bulut klasörü).

### Şifreleme anahtarını da saklayın
Google Calendar / WhatsApp erişim anahtarları veritabanında `NEXORA_ENCRYPTION_KEY` ile şifreli durur. Bu değer Render'daki servis ortam değişkenlerindedir. Yedekle **birlikte** (ama yedek dosyasıyla aynı yerde değil) bir parola yöneticisinde saklayın. Anahtar kaybolursa veri geri gelir fakat bu entegrasyonlar yeniden bağlanmak zorunda kalır.

## 3. Geri yükleme testi (yedeğin gerçekten çalıştığını kanıtlar)

Üretim veritabanına **asla** geri yükleme yapmayın. Boş bir test veritabanı oluşturun (yerel PostgreSQL veya ayrı bir ücretsiz/geçici veritabanı):

```bash
export TARGET_DATABASE_URL='postgresql://...boş-test-veritabanı'
export SOURCE_DATABASE_URL="$DATABASE_URL"   # isteğe bağlı: yalnızca satır sayısı karşılaştırır
./scripts/restore_test.sh ./yedekler/mexay_YYYYMMDD_HHMMSS.dump
```

Betik; hedef boş değilse, hedef üretimle aynıysa veya SHA-256 tutmuyorsa **durur**. Başarılıysa tablo bazında satır sayılarını yan yana gösterir (`businesses`, `users`, `customers`, `appointments`, `messages`, `subscriptions`, `audit_logs`).

## 4. Colab ile yedek alma (bilgisayara kurulum istemez)

Yeni bir Colab not defterinde hücreleri sırayla çalıştırın. Bağlantı adresi **ekranda görünmeden** `getpass` ile istenir; not defterini kaydederken çıktıya yazılmaz.

Hücre 1 — istemciyi kur (sunucu sürümü 18 olduğu için istemci 18 kurulur):
```python
!apt-get -qq update && apt-get -qq install -y postgresql-common
!/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
!apt-get -qq install -y postgresql-client-18
!pg_dump --version
```

Hücre 2 — yedek al:
```python
import getpass, os, subprocess, datetime
url = getpass.getpass("Render External Database URL: ")
out = f"mexay_{datetime.datetime.now():%Y%m%d_%H%M%S}.dump"
subprocess.run(["pg_dump","--format=custom","--no-owner","--no-privileges","--file",out,url], check=True)
print(out, os.path.getsize(out), "bayt")
print(subprocess.run(["pg_restore","--list",out],capture_output=True,text=True).stdout.count(" TABLE DATA "), "tablo verisi")
```

Hücre 3 — bilgisayarınıza indirin ve Colab'daki kopyayı silin:
```python
from google.colab import files
files.download(out)
os.remove(out)
```

## 5. Plan kararı (sizin onayınızla)

| Seçenek | Artı | Dikkat |
|---|---|---|
| Render veritabanını ücretli plana geçirmek | Aynı adres, kesinti yok, genelde en kolayı | Aylık ücret; güncel fiyatı Render panelinde doğrulayın |
| Başka bir yönetilen PostgreSQL'e taşımak | Ücretsiz katmanlar olabilir | Yedeği yeni veritabanına geri yükleyip `DATABASE_URL` değiştirmek gerekir; ücretsiz katmanların da süre/limit kuralları vardır |

Hangisi seçilirse seçilsin: önce yedek, sonra geri yükleme testi, ancak ondan sonra geçiş.

## 6. Düzenli yedek
- En az haftada bir yedek alın ve en az iki geri yükleme testi yapın.
- Yedek dosyalarını **herkese açık bir GitHub deposunda Actions artifact olarak tutmayın**; müşteri verisi sızabilir.
- Eski yedekleri `KEEP_DAYS=30 ./scripts/backup_db.sh ./yedekler` ile temizleyebilirsiniz.
