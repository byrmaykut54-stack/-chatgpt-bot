#!/usr/bin/env bash
# MexAy PostgreSQL yedekleme betiği.
#
# Kullanım:
#   DATABASE_URL='postgresql://...' ./scripts/backup_db.sh [hedef_klasör]
#
# - Bağlantı adresi yalnızca ortam değişkeninden okunur; ekrana ASLA yazdırılmaz.
# - Çıktı: <klasör>/mexay_YYYYMMDD_HHMMSS.dump (pg_dump özel biçimi) + .sha256
# - Yedek dosyası müşteri verisi içerir. Git'e eklemeyin, herkese açık yerde paylaşmayın.
# - Şifrelenmiş entegrasyon anahtarları (Google/WhatsApp) veritabanında şifreli durur.
#   NEXORA_ENCRYPTION_KEY değerini de güvenli bir yerde ayrıca saklayın; yoksa
#   geri yüklenen veriden bu entegrasyonlar çözülemez.
#
# İsteğe bağlı: KEEP_DAYS=30  -> hedef klasörde 30 günden eski yedekleri siler.

set -euo pipefail
umask 077

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "HATA: DATABASE_URL ortam değişkeni tanımlı değil." >&2
  exit 2
fi
for tool in pg_dump pg_restore sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || { echo "HATA: '$tool' bulunamadı. PostgreSQL istemci araçlarını kurun." >&2; exit 2; }
done

DEST_DIR="${1:-./yedekler}"
mkdir -p "$DEST_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$DEST_DIR/mexay_${STAMP}.dump"

echo "Yedek alınıyor -> $OUT"
# --no-owner/--no-privileges: başka bir veritabanına sorunsuz geri yüklenebilsin.
if ! pg_dump --format=custom --no-owner --no-privileges --file="$OUT" "$DATABASE_URL"; then
  rm -f "$OUT"
  echo "HATA: pg_dump başarısız. (Sürüm uyuşmazlığı en yaygın nedendir: istemci, sunucudan eski olmamalı.)" >&2
  exit 1
fi

# Doğrulama 1: dosya boş olmamalı. Doğrulama 2: arşiv içeriği okunabilmeli.
if [[ ! -s "$OUT" ]]; then
  rm -f "$OUT"; echo "HATA: yedek dosyası boş." >&2; exit 1
fi
if ! pg_restore --list "$OUT" >/dev/null 2>&1; then
  echo "HATA: yedek arşivi okunamadı, dosya bozuk olabilir: $OUT" >&2
  exit 1
fi
TABLES="$(pg_restore --list "$OUT" | grep -c ' TABLE DATA ' || true)"

( cd "$DEST_DIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )

SIZE="$(du -h "$OUT" | cut -f1)"
echo "Tamam. Boyut: $SIZE, veri içeren tablo sayısı: $TABLES"
echo "SHA-256: $(cut -d' ' -f1 "$OUT.sha256")"
echo "UYARI: Bu dosyayı güvenli ve ikinci bir yere de kopyalayın; geri yükleme testi yapmadan yedeğe güvenmeyin."

if [[ -n "${KEEP_DAYS:-}" ]]; then
  find "$DEST_DIR" -maxdepth 1 -type f \( -name 'mexay_*.dump' -o -name 'mexay_*.dump.sha256' \) -mtime +"$KEEP_DAYS" -print -delete
fi
